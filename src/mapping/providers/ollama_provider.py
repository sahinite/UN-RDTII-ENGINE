"""Ollama offline provider — qwen2.5:7b (P6) and granite3-8b (P7)."""

from __future__ import annotations

import os
import time

import requests

from src.mapping.base_provider import BaseLLMProvider
from src.mapping.exceptions import ProviderAPIError, ProviderTimeoutError
from src.mapping.models import LLMResponse

# Default local models per cascade tier — used ONLY when LLM_MODEL is unset. The
# active model is taken from the LLM_MODEL env var (same convention as every other
# provider), so no model name is dictated by code; these are just Apache-2.0 safe
# fallbacks that keep the offline cascade working out of the box.
OLLAMA_MODEL_DEFAULTS = {
    6: "qwen2.5:7b",    # Priority 6 — Apache 2.0
    7: "granite3-8b",   # Priority 7 — Apache 2.0 (IBM Granite 3.0 8B)
}

# CRITICAL: Llama 3.3 is EXPLICITLY EXCLUDED — non-Apache 2.0 license.
LLAMA33_BLOCKLIST = ["llama3.3", "llama-3.3", "llama_3.3", "llama3.3:latest"]

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


def _resolve_model(priority: int) -> str:
    """Model to run: LLM_MODEL env if set, else the tier's Apache-2.0 default."""
    return os.environ.get("LLM_MODEL", "").strip() or OLLAMA_MODEL_DEFAULTS[priority]


# Backwards-compat alias for legacy importers (e.g. crawler.ranker).
OLLAMA_MODELS = OLLAMA_MODEL_DEFAULTS

# Thinking models divert their chain-of-thought to a separate `thinking` field and
# can return an empty `response`, losing the JSON. We disable thinking for these
# (also ~60x faster for structured extraction).
_REASONING_MODEL_HINTS = ("r1", "qwq", "qwen3", "reasoning", "thinking")

# Ollama's default num_ctx (4096) silently truncates our ~6000-token prompt, leaving
# no room to generate. Size it to fit prompt + answer; override via OLLAMA_NUM_CTX.
_DEFAULT_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "8192"))

# Read timeouts (seconds). Reasoning models run a long chain-of-thought before the
# answer, so they need much longer than a normal local generation. Override with
# the OLLAMA_TIMEOUT env var.
_DEFAULT_READ_TIMEOUT = 120
_REASONING_READ_TIMEOUT = 600


def _is_reasoning_model(model: str) -> bool:
    m = model.lower()
    return any(hint in m for hint in _REASONING_MODEL_HINTS)


class OllamaProvider(BaseLLMProvider):
    def __init__(self, priority: int):
        assert priority in (6, 7), f"Invalid Ollama priority: {priority}"
        self._priority = priority
        self._model = _resolve_model(priority)
        assert self._model not in LLAMA33_BLOCKLIST, "Llama 3.3 blocked — non-Apache 2.0 license"

    @property
    def provider_name(self) -> str:
        return "ollama"

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        base_url = os.environ.get("OLLAMA_BASE_URL", OLLAMA_BASE_URL)
        try:
            r = requests.get(f"{base_url}/api/tags", timeout=3)
            if r.status_code != 200:
                return False
            models = [m["name"] for m in r.json().get("models", [])]
            return any(self._model.replace(":latest", "") in m for m in models)
        except requests.RequestException:
            return False

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> LLMResponse:
        base_url = os.environ.get("OLLAMA_BASE_URL", OLLAMA_BASE_URL)
        reasoning = _is_reasoning_model(self._model)
        timeout_s = int(os.environ.get("OLLAMA_TIMEOUT", "0")) or (
            _REASONING_READ_TIMEOUT if reasoning else _DEFAULT_READ_TIMEOUT
        )
        payload = {
            "model": self._model,
            "prompt": f"{system_prompt}\n\n{user_prompt}",
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": _DEFAULT_NUM_CTX,
            },
        }
        # Disable chain-of-thought for thinking models so the JSON answer lands in
        # `response` (newer Ollama otherwise diverts it to a separate `thinking`
        # field and returns an empty `response`). Ollama ignores the flag for
        # non-thinking models. This is also far faster (~3s vs ~180s per call).
        if reasoning:
            payload["think"] = False
        t0 = time.time()
        try:
            r = requests.post(f"{base_url}/api/generate", json=payload, timeout=timeout_s)
            r.raise_for_status()
        except requests.Timeout as e:
            raise ProviderTimeoutError("ollama", str(e))
        except requests.RequestException as e:
            raise ProviderAPIError("ollama", str(e))

        data = r.json()
        latency_ms = (time.time() - t0) * 1000

        return LLMResponse(
            text=data.get("response") or "",
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            model=self._model,
            provider="ollama",
            latency_ms=latency_ms,
            cost_usd=0.0,  # self-hosted, no API cost
        )
