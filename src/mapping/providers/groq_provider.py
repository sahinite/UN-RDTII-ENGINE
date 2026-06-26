"""Groq provider — Qwen3 via Groq free tier. [Z2-4 ST1]"""

from __future__ import annotations

import os
import time

try:
    from groq import Groq
except ImportError:
    Groq = None  # type: ignore[assignment,misc]

from src.mapping.base_provider import BaseLLMProvider
from src.mapping.exceptions import ProviderAPIError, ProviderRateLimitError, ProviderTimeoutError
from src.mapping.models import LLMResponse

GROQ_MODEL_DEFAULT = "qwen/qwen3-32b"
GROQ_MODEL_FALLBACK = "qwen/qwen3.6-27b"


def _resolve_model() -> str:
    """Returns LLM_MODEL from env if set, else the default."""
    return os.environ.get("LLM_MODEL", "").strip() or GROQ_MODEL_DEFAULT


class GroqProvider(BaseLLMProvider):
    @property
    def provider_name(self) -> str:
        return "groq"

    @property
    def model(self) -> str:
        return _resolve_model()

    def is_available(self) -> bool:
        return bool(os.environ.get("GROQ_API_KEY", "").strip())

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> LLMResponse:
        if Groq is None:
            raise ProviderAPIError("groq", "groq package not installed")

        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        t0 = time.time()
        model_to_use = _resolve_model()
        try:
            resp = client.chat.completions.create(
                model=model_to_use,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception:
            # Primary model failed for any reason — always attempt fallback first
            try:
                model_to_use = GROQ_MODEL_FALLBACK
                resp = client.chat.completions.create(
                    model=model_to_use,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                )
            except Exception as e2:
                err_str2 = str(e2).lower()
                if "rate" in err_str2 or "429" in err_str2:
                    raise ProviderRateLimitError("groq", str(e2))
                elif "timeout" in err_str2 or "connection" in err_str2:
                    raise ProviderTimeoutError("groq", str(e2))
                else:
                    raise ProviderAPIError("groq", str(e2))

        latency_ms = (time.time() - t0) * 1000
        usage = resp.usage

        return LLMResponse(
            text=resp.choices[0].message.content,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            model=model_to_use,
            provider="groq",
            latency_ms=latency_ms,
            cost_usd=0.0,  # Groq free tier
        )
