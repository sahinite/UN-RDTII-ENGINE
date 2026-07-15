"""DeepSeek provider — OpenAI-compatible API."""

from __future__ import annotations

import os
import time

try:
    import openai
except ImportError:
    openai = None  # type: ignore[assignment]

from src.mapping.base_provider import BaseLLMProvider, resolve_api_key
from src.mapping.exceptions import ProviderAPIError, ProviderRateLimitError, ProviderTimeoutError
from src.mapping.models import LLMResponse

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL_DEFAULT = "deepseek-chat"

# Approximate pricing (deepseek-chat V3): cache-hit/miss varies;
# use conservative non-cached rates for cost logging.
DEEPSEEK_INPUT_COST_PER_1K = 0.00027   # $0.27 per 1M input tokens
DEEPSEEK_OUTPUT_COST_PER_1K = 0.0011   # $1.10 per 1M output tokens


def _resolve_model() -> str:
    return os.environ.get("LLM_MODEL", "").strip() or DEEPSEEK_MODEL_DEFAULT


class DeepSeekProvider(BaseLLMProvider):
    @property
    def provider_name(self) -> str:
        return "deepseek"

    @property
    def model(self) -> str:
        return _resolve_model()

    def is_available(self) -> bool:
        return bool(resolve_api_key("DEEPSEEK_API_KEY"))

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> LLMResponse:
        if openai is None:
            raise ProviderAPIError("deepseek", "openai package not installed")

        model = _resolve_model()
        client = openai.OpenAI(
            api_key=resolve_api_key("DEEPSEEK_API_KEY"),
            base_url=DEEPSEEK_BASE_URL,
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
            raise ProviderRateLimitError("deepseek", str(e))
        except openai.APIStatusError as e:
            raise ProviderAPIError("deepseek", str(e))
        except openai.APIConnectionError as e:
            raise ProviderTimeoutError("deepseek", str(e))

        latency_ms = (time.time() - t0) * 1000
        usage = resp.usage
        cost = (
            usage.prompt_tokens / 1000 * DEEPSEEK_INPUT_COST_PER_1K
            + usage.completion_tokens / 1000 * DEEPSEEK_OUTPUT_COST_PER_1K
        )

        return LLMResponse(
            text=resp.choices[0].message.content,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            model=model,
            provider="deepseek",
            latency_ms=latency_ms,
            cost_usd=cost,
        )
