"""Qwen direct provider — DashScope OpenAI-compatible API. [Z2-4]"""

from __future__ import annotations

import os
import time

try:
    import openai
except ImportError:
    openai = None  # type: ignore[assignment]

from src.mapping.base_provider import BaseLLMProvider
from src.mapping.exceptions import ProviderAPIError, ProviderRateLimitError, ProviderTimeoutError
from src.mapping.models import LLMResponse

# International endpoint (works outside China); domestic: dashscope.aliyuncs.com
QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
QWEN_MODEL_DEFAULT = "qwen-plus"

# Approximate pricing — qwen-plus; varies by model.
QWEN_INPUT_COST_PER_1K = 0.0004   # $0.40 per 1M input tokens
QWEN_OUTPUT_COST_PER_1K = 0.0012  # $1.20 per 1M output tokens


def _resolve_model() -> str:
    return os.environ.get("LLM_MODEL", "").strip() or QWEN_MODEL_DEFAULT


class QwenProvider(BaseLLMProvider):
    @property
    def provider_name(self) -> str:
        return "qwen"

    @property
    def model(self) -> str:
        return _resolve_model()

    def is_available(self) -> bool:
        return bool(os.environ.get("DASHSCOPE_API_KEY", "").strip())

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> LLMResponse:
        if openai is None:
            raise ProviderAPIError("qwen", "openai package not installed")

        model = _resolve_model()
        client = openai.OpenAI(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            base_url=QWEN_BASE_URL,
        )
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except openai.RateLimitError as e:
            raise ProviderRateLimitError("qwen", str(e))
        except openai.APIStatusError as e:
            raise ProviderAPIError("qwen", str(e))
        except openai.APIConnectionError as e:
            raise ProviderTimeoutError("qwen", str(e))

        latency_ms = (time.time() - t0) * 1000
        usage = resp.usage
        cost = (
            usage.prompt_tokens / 1000 * QWEN_INPUT_COST_PER_1K
            + usage.completion_tokens / 1000 * QWEN_OUTPUT_COST_PER_1K
        )

        return LLMResponse(
            text=resp.choices[0].message.content,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            model=model,
            provider="qwen",
            latency_ms=latency_ms,
            cost_usd=cost,
        )
