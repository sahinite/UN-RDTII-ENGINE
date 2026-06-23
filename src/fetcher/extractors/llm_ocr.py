"""
LLM-based OCR fallback for scanned PDFs when Tesseract/PaddleOCR is unavailable.

Reads LLM_PROVIDER + LLM_MODEL from env (same vars as the mapping pipeline).
Supported providers: anthropic, openai.
Groq and Ollama do not expose vision APIs for their cascade models — raises
DependencyError so callers can surface a clear install message.
"""

from __future__ import annotations

import base64
import os
import unicodedata

from src.fetcher.logger import get_logger

logger = get_logger("llm_ocr")

_OCR_PROMPT = (
    "You are a document OCR assistant. Extract ALL text from this scanned document page "
    "exactly as it appears — verbatim, preserving section numbers, headings, punctuation, "
    "and paragraph breaks. Do not summarise, paraphrase, or omit any text. "
    "Output only the extracted text, nothing else."
)

_GROQ_VISION_MODELS = {"llava-v1.5-7b-4096-preview"}  # only groq models with vision


class LLMOCRUnavailableError(Exception):
    """Raised when the configured LLM provider cannot perform vision OCR."""


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


# ── Public entry point ─────────────────────────────────────────────────────────

def run_llm_ocr(image_bytes: bytes) -> tuple[str, float, int, int]:
    """
    Run vision OCR on a single page image using the provider configured in .env.

    Returns (text, cer_estimate, input_tokens, output_tokens).
    Raises LLMOCRUnavailableError when the configured provider cannot do vision OCR.
    """
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    model = os.environ.get("LLM_MODEL", "").strip()

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise LLMOCRUnavailableError("OPENAI_API_KEY not set")
        model = model or "gpt-4o"
        logger.info({"event": "llm_ocr_attempt", "provider": "openai", "model": model})
        text, in_tok, out_tok = _ocr_openai(image_bytes, model, api_key)

    elif provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise LLMOCRUnavailableError("ANTHROPIC_API_KEY not set")
        model = model or "claude-sonnet-4-20250514"
        logger.info({"event": "llm_ocr_attempt", "provider": "anthropic", "model": model})
        text, in_tok, out_tok = _ocr_anthropic(image_bytes, model, api_key)

    elif provider == "groq":
        model = model or ""
        if model not in _GROQ_VISION_MODELS:
            raise LLMOCRUnavailableError(
                f"Groq model '{model or 'qwen3-32b'}' does not support vision OCR. "
                "Install tesseract (run setup.py) or switch LLM_PROVIDER to openai/anthropic."
            )
        raise LLMOCRUnavailableError("Groq vision OCR not implemented in this cascade.")

    elif provider == "ollama":
        raise LLMOCRUnavailableError(
            "Ollama models in the cascade (qwen2.5:7b, granite3-dense:8b) do not support "
            "vision OCR. Install tesseract (run setup.py) or switch LLM_PROVIDER to openai/anthropic."
        )

    else:
        raise LLMOCRUnavailableError(
            f"Unknown or unset LLM_PROVIDER='{provider}'. "
            "Set LLM_PROVIDER=openai or LLM_PROVIDER=anthropic for vision OCR fallback."
        )

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
