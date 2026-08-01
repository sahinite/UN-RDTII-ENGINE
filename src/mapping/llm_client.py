"""
7-tier auto-cascade LLM engine with session pinning.

PROVIDER_CASCADE order (DO NOT reorder):
  1. AnthropicProvider  — claude-sonnet-4-20250514 (pinned primary)
  2. OpenAIProvider     — gpt-4o
  3. GeminiProvider     — gemini-2.5-flash (OpenAI-compatible; GEMINI_API_KEY)
  4. DeepSeekProvider   — deepseek-chat (V3, OpenAI-compatible; DEEPSEEK_API_KEY)
  5. GroqProvider       — qwen/qwen3-32b (free tier; fallback qwen/qwen3.6-27b)
  6. QwenProvider       — qwen-plus via DashScope (DASHSCOPE_API_KEY)
  7. OllamaProvider(6) — qwen2.5:7b (Apache 2.0, offline)
  8. OllamaProvider(7) — granite3-dense:8b (Apache 2.0, offline)

Llama 3.3 is NOT in cascade — non-Apache 2.0 license.
Pin a provider with LLM_PROVIDER env var: anthropic | openai | gemini | deepseek | groq | qwen | ollama
"""

from __future__ import annotations

import logging
import os
import threading

from src.mapping.base_provider import BaseLLMProvider
from src.mapping.exceptions import (
    AllProvidersExhaustedError,
    ConfigError,
    ProviderAPIError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from src.mapping.models import LLMResponse
from src.mapping.providers.anthropic_provider import AnthropicProvider
from src.mapping.providers.deepseek_provider import DeepSeekProvider
from src.mapping.providers.gemini_provider import GeminiProvider
from src.mapping.providers.groq_provider import GroqProvider
from src.mapping.providers.ollama_provider import OllamaProvider
from src.mapping.providers.openai_provider import OpenAIProvider
from src.mapping.providers.qwen_provider import QwenProvider

logger = logging.getLogger("mapping.llm_client")

# Ordered by priority — DO NOT reorder
PROVIDER_CASCADE: list[BaseLLMProvider] = [
    AnthropicProvider(),   # 1 — claude-sonnet-4-20250514 (pinned)
    OpenAIProvider(),      # 2 — gpt-4o
    GeminiProvider(),      # 3 — gemini-2.5-flash (GEMINI_API_KEY)
    DeepSeekProvider(),    # 4 — deepseek-chat V3 (DEEPSEEK_API_KEY)
    GroqProvider(),        # 5 — qwen/qwen3-32b via Groq (qwen/qwen3.6-27b fallback)
    QwenProvider(),        # 6 — qwen-plus via DashScope (DASHSCOPE_API_KEY)
    OllamaProvider(6),     # 7 — qwen2.5:7b (Apache 2.0, offline)
    OllamaProvider(7),     # 8 — granite3-dense:8b (Apache 2.0, offline)
]

_SESSION_PROVIDER: BaseLLMProvider | None = None
_SESSION_LOCK = threading.Lock()

CASCADE_FAILURE_EXCEPTIONS = (ProviderRateLimitError, ProviderAPIError, ProviderTimeoutError)


def pin_active_provider() -> BaseLLMProvider:
    """
    Called ONCE at engine startup before any extraction begins.

    Selection logic:
      1. Read LLM_PROVIDER from env → use if is_available()
      2. Walk PROVIDER_CASCADE → use first available
      3. Raise ConfigError if none available
    """
    global _SESSION_PROVIDER
    with _SESSION_LOCK:
        preferred = os.environ.get("LLM_PROVIDER", "").strip().lower()
        if preferred:
            for p in PROVIDER_CASCADE:
                if p.provider_name == preferred and p.is_available():
                    _SESSION_PROVIDER = p
                    logger.info({
                        "event": "provider_pinned",
                        "provider": p.provider_name,
                        "model": p.model,
                        "source": "env",
                    })
                    return p
            logger.warning({
                "event": "preferred_provider_unavailable",
                "preferred": preferred,
                "falling_through": True,
            })

        for p in PROVIDER_CASCADE:
            if p.is_available():
                _SESSION_PROVIDER = p
                logger.info({
                    "event": "provider_pinned",
                    "provider": p.provider_name,
                    "model": p.model,
                    "source": "auto_detect",
                })
                return p

        raise ConfigError(
            "No LLM provider is available. Set LLM_PROVIDER + a single LLM_API_KEY:\n"
            "  LLM_PROVIDER=anthropic   (Priority 1 — recommended)\n"
            "  LLM_API_KEY=<your key>   (used by whichever provider is pinned)\n"
            "Provider priority: anthropic > openai > gemini > deepseek > groq > qwen(DashScope) > ollama.\n"
            "Provider-specific vars (ANTHROPIC_API_KEY, OPENAI_API_KEY, ...) still work and\n"
            "override LLM_API_KEY. Offline: run 'ollama serve' + 'ollama pull qwen2.5:7b'."
        )


def call_llm_with_cascade(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1000,
    temperature: float = 0.0,
) -> LLMResponse:
    """
    Attempts the pinned provider first. On CASCADE_FAILURE_EXCEPTION,
    falls through to the next available tier. Raises AllProvidersExhaustedError
    if all tiers fail.
    """
    pinned = _SESSION_PROVIDER
    if pinned is None:
        raise RuntimeError("pin_active_provider() must be called before call_llm_with_cascade()")

    remaining = [p for p in PROVIDER_CASCADE if p is not pinned and p.is_available()]
    attempt_order = [pinned] + remaining

    last_error = None
    for provider in attempt_order:
        try:
            response = _call_with_retry(provider, system_prompt, user_prompt, max_tokens, temperature)
            if provider is not pinned:
                logger.warning({
                    "event": "cascade_fallthrough",
                    "pinned_provider": pinned.provider_name,
                    "active_provider": provider.provider_name,
                    "model": provider.model,
                })
            else:
                logger.debug({
                    "event": "llm_call_success",
                    "provider": provider.provider_name,
                    "model": provider.model,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "cost_usd": response.cost_usd,
                    "latency_ms": round(response.latency_ms, 1),
                })
            return response

        except CASCADE_FAILURE_EXCEPTIONS as e:
            last_error = e
            logger.warning({
                "event": "provider_failed",
                "provider": provider.provider_name,
                "error_type": type(e).__name__,
                "error": str(e),
                "trying_next": True,
            })
            continue

    raise AllProvidersExhaustedError(
        f"All {len(attempt_order)} LLM providers exhausted. Last error: {last_error}"
    )


def smoke_check_llm() -> "tuple[bool, str]":
    """One tiny real call to prove the LLM can PRODUCE PARSEABLE OUTPUT — not just
    that a key is present (all `is_available()` checks). Catches in ~seconds the
    three ways the LLM silently yields nothing and turns every provision into a
    false "no barrier" N/A: (1) provider dead (bad/quota'd key → all tiers raise);
    (2) empty response (thinking-only model, or prompt overran the context window);
    (3) unparseable JSON. Uses the real cascade path. Returns (ok, detail).
    """
    from src.mapping.parser import _extract_json

    system = "You output only a JSON object. No prose, no markdown, no thinking."
    user = 'Return exactly this and nothing else: {"ok": true, "n": 26}'
    try:
        resp = call_llm_with_cascade(system, user, max_tokens=200, temperature=0.0)
    except Exception as e:  # noqa: BLE001 — any failure means the LLM can't run
        return False, f"provider call failed ({type(e).__name__}): {e}"

    text = (resp.text or "").strip()
    if not text:
        return False, (
            f"{resp.provider}/{resp.model} returned an EMPTY response — likely a "
            "thinking-only model or a context/token limit (for Ollama, raise OLLAMA_NUM_CTX)"
        )
    try:
        _extract_json(text)
    except Exception:  # noqa: BLE001 — unparseable output
        return False, (
            f"{resp.provider}/{resp.model} returned unparseable (non-JSON) output: {text[:80]!r}"
        )
    return True, f"{resp.provider}/{resp.model}"


def _call_with_retry(
    provider: BaseLLMProvider,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
) -> LLMResponse:
    """One retry on ProviderTimeoutError only; no retry on rate limit or API error."""
    try:
        return provider.complete(system, user, max_tokens, temperature)
    except ProviderTimeoutError:
        logger.warning({"event": "timeout_retry", "provider": provider.provider_name})
        return provider.complete(system, user, max_tokens, temperature)


def get_active_model_version(ocr_engine: str = "") -> str:
    """
    Returns model version string for the JSON output envelope.

    When ocr_engine is provided, combines LLM and OCR identifiers:
      e.g. "claude-sonnet-4-20250514 + tesseract-5.3"
    Without ocr_engine, returns '{provider}/{model}'.
    """
    if _SESSION_PROVIDER is None:
        llm_version = "unknown/unknown"
    else:
        llm_version = f"{_SESSION_PROVIDER.provider_name}/{_SESSION_PROVIDER.model}"
    if ocr_engine:
        return f"{llm_version} + {ocr_engine}"
    return llm_version
