"""OpenAI GPT-4o provider. [Z2-4 ST1]"""

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

OPENAI_MODEL = "gpt-4o"

OPENAI_INPUT_COST_PER_1K = 0.005
OPENAI_OUTPUT_COST_PER_1K = 0.015


class OpenAIProvider(BaseLLMProvider):
    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return OPENAI_MODEL

    def is_available(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY", "").strip())

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> LLMResponse:
        if openai is None:
            raise ProviderAPIError("openai", "openai package not installed")

        client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except openai.RateLimitError as e:
            raise ProviderRateLimitError("openai", str(e))
        except openai.APIStatusError as e:
            raise ProviderAPIError("openai", str(e))
        except openai.APIConnectionError as e:
            raise ProviderTimeoutError("openai", str(e))

        latency_ms = (time.time() - t0) * 1000
        usage = resp.usage
        cost = (
            usage.prompt_tokens / 1000 * OPENAI_INPUT_COST_PER_1K
            + usage.completion_tokens / 1000 * OPENAI_OUTPUT_COST_PER_1K
        )

        return LLMResponse(
            text=resp.choices[0].message.content,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            model=OPENAI_MODEL,
            provider="openai",
            latency_ms=latency_ms,
            cost_usd=cost,
        )
