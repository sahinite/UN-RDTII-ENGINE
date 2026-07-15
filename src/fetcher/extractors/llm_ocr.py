"""
LLM-vision OCR — a cascade tier that runs the OCR on the *configured* LLM.

Provider comes from LLM_PROVIDER (never hardcoded). The vision model is resolved
per-provider: LLM_MODEL when it can see, else a per-provider default. Supported
providers: anthropic, openai, ollama (local, incl. vision-capable models like
qwen3.5). Providers with no vision-capable model report unavailable so the caller
cleanly falls through to the local Tesseract/Paddle floor.
"""

from __future__ import annotations

import base64
import os
import unicodedata

import requests

from src.fetcher.logger import get_logger

logger = get_logger("llm_ocr")

# Per-provider default vision model, used when LLM_MODEL isn't itself vision-capable.
_VISION_DEFAULTS = {
    "anthropic": "claude-sonnet-4-20250514",  # every Claude model has vision
    "openai": "gpt-4o",
}

_OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

_OCR_PROMPT = (
    "You are a document OCR assistant. Extract ALL text from this scanned document page "
    "exactly as it appears — verbatim, preserving section numbers, headings, punctuation, "
    "and paragraph breaks. Do not summarise, paraphrase, or omit any text. "
    "Output only the extracted text, nothing else."
)

class LLMOCRUnavailableError(Exception):
    """Raised when the configured LLM provider cannot perform vision OCR."""


# ── Provider / model resolution ────────────────────────────────────────────────

def _ollama_vision_models() -> list[str]:
    """Names of locally-pulled Ollama models whose capabilities include vision."""
    base = os.environ.get("OLLAMA_BASE_URL", _OLLAMA_BASE_URL)
    try:
        r = requests.get(f"{base}/api/tags", timeout=3)
        r.raise_for_status()
    except requests.RequestException:
        return []
    return [
        m["name"] for m in r.json().get("models", [])
        if "vision" in (m.get("capabilities") or [])
    ]


def _resolve_vision_model(provider: str) -> str | None:
    """Vision model for the configured provider, or None if it can't do vision.

    LLM_MODEL wins when it's vision-capable; otherwise the per-provider default.
    For Ollama, if the pinned model can't see we auto-pick another pulled vision model.
    """
    pinned = os.environ.get("LLM_MODEL", "").strip()
    if provider == "ollama":
        vision = _ollama_vision_models()
        if pinned and any(pinned.replace(":latest", "") in m for m in vision):
            return pinned
        return vision[0] if vision else None
    if provider == "anthropic":
        return pinned or _VISION_DEFAULTS["anthropic"]  # all Claude models see
    if provider == "openai":
        return _VISION_DEFAULTS["openai"]  # guarantee a vision model
    return None  # deepseek / groq / qwen / unknown → no vision in this cascade


def _provider_key(provider: str) -> str | None:
    """API key env for a provider (None = local, no key needed)."""
    return {
        "openai": os.environ.get("OPENAI_API_KEY", "").strip(),
        "anthropic": os.environ.get("ANTHROPIC_API_KEY", "").strip(),
        "ollama": None,
    }.get(provider, "")


def llm_vision_available() -> bool:
    """True when the configured provider has a usable vision model (+ key if cloud)."""
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if _resolve_vision_model(provider) is None:
        return False
    key = _provider_key(provider)
    return key is None or bool(key)  # local (None) needs no key; cloud needs one


def _estimate_cer(text: str) -> float:
    if not text or not text.strip():
        return 1.0
    garbage = sum(
        1 for ch in text
        if unicodedata.category(ch) in ("Cc", "Cs", "Co", "Cn")
        and ch not in ("\n", "\t", "\r")
    )
    return min(garbage / len(text), 1.0)


def _b64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode()


# ── Provider implementations ───────────────────────────────────────────────────

def _ocr_openai(image_bytes: bytes, model: str, api_key: str) -> tuple[str, int, int]:
    """Returns (text, input_tokens, output_tokens)."""
    try:
        import openai as openai_lib
    except ImportError as exc:
        raise LLMOCRUnavailableError("openai package not installed") from exc

    client = openai_lib.OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        temperature=0.0,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": _OCR_PROMPT},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/png;base64,{_b64(image_bytes)}",
                    "detail": "high",
                }},
            ],
        }],
    )
    usage = resp.usage
    return (
        resp.choices[0].message.content or "",
        usage.prompt_tokens if usage else 0,
        usage.completion_tokens if usage else 0,
    )


def _ocr_anthropic(image_bytes: bytes, model: str, api_key: str) -> tuple[str, int, int]:
    """Returns (text, input_tokens, output_tokens)."""
    try:
        import anthropic as anthropic_lib
    except ImportError as exc:
        raise LLMOCRUnavailableError("anthropic package not installed") from exc

    client = anthropic_lib.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=0.0,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": _b64(image_bytes),
                }},
                {"type": "text", "text": _OCR_PROMPT},
            ],
        }],
    )
    return (
        msg.content[0].text if msg.content else "",
        msg.usage.input_tokens if msg.usage else 0,
        msg.usage.output_tokens if msg.usage else 0,
    )


def _ocr_ollama(image_bytes: bytes, model: str) -> tuple[str, int, int]:
    """Local Ollama vision OCR. Returns (text, input_tokens, output_tokens)."""
    base = os.environ.get("OLLAMA_BASE_URL", _OLLAMA_BASE_URL)
    payload = {
        "model": model,
        "prompt": _OCR_PROMPT,
        "images": [_b64(image_bytes)],
        "stream": False,
        "think": False,  # want the transcription, not chain-of-thought
        "options": {"temperature": 0.0, "num_ctx": 8192},
    }
    try:
        r = requests.post(f"{base}/api/generate", json=payload, timeout=300)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise LLMOCRUnavailableError(f"Ollama vision request failed: {exc}") from exc
    d = r.json()
    return d.get("response") or "", d.get("prompt_eval_count", 0), d.get("eval_count", 0)


# ── Public entry point ─────────────────────────────────────────────────────────

def run_llm_ocr(image_bytes: bytes) -> tuple[str, float, int, int]:
    """
    Run vision OCR on a single page image using the provider configured in .env.

    Returns (text, cer_estimate, input_tokens, output_tokens).
    Raises LLMOCRUnavailableError when the configured provider cannot do vision OCR.
    """
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    model = _resolve_vision_model(provider)
    if not model:
        raise LLMOCRUnavailableError(
            f"No vision-capable model for LLM_PROVIDER='{provider}'. "
            "Use openai/anthropic, or an Ollama model with vision (e.g. qwen3.5)."
        )
    logger.info({"event": "llm_ocr_attempt", "provider": provider, "model": model})

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise LLMOCRUnavailableError("OPENAI_API_KEY not set")
        text, in_tok, out_tok = _ocr_openai(image_bytes, model, api_key)
    elif provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise LLMOCRUnavailableError("ANTHROPIC_API_KEY not set")
        text, in_tok, out_tok = _ocr_anthropic(image_bytes, model, api_key)
    elif provider == "ollama":
        text, in_tok, out_tok = _ocr_ollama(image_bytes, model)
    else:
        raise LLMOCRUnavailableError(f"Vision OCR not supported for provider '{provider}'")

    cer = _estimate_cer(text)
    logger.info({
        "event": "llm_ocr_completed",
        "provider": provider,
        "model": model,
        "text_length": len(text),
        "cer_estimate": round(cer, 4),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
    })
    return text, cer, in_tok, out_tok
