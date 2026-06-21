"""Groq provider — DeepSeek/Qwen via Groq free tier. [Z2-4 ST1]"""

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

GROQ_MODEL = "deepseek-r1-distill-llama-70b"
GROQ_MODEL_FALLBACK = "qwen-qwq-32b"


class GroqProvider(BaseLLMProvider):
    @property
    def provider_name(self) -> str:
        return "groq"

    @property
    def model(self) -> str:
        return GROQ_MODEL

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
        try:
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as e:
            err_str = str(e).lower()
            if "rate" in err_str or "429" in err_str:
                raise ProviderRateLimitError("groq", str(e))
            elif "timeout" in err_str or "connection" in err_str:
                raise ProviderTimeoutError("groq", str(e))
            else:
                raise ProviderAPIError("groq", str(e))

        latency_ms = (time.time() - t0) * 1000
        usage = resp.usage

        return LLMResponse(
            text=resp.choices[0].message.content,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            model=GROQ_MODEL,
            provider="groq",
            latency_ms=latency_ms,
            cost_usd=0.0,  # Groq free tier
        )
