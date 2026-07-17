"""OpenAI GPT-4o provider."""

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

OPENAI_MODEL_DEFAULT = "gpt-4o"

# Cost per 1K tokens — approximate; accurate only for gpt-4o.
# The hackathon cost logger reads actual usage from the API response,
# so this is only used as a fallback estimate when the model is unknown.
OPENAI_INPUT_COST_PER_1K = 0.005
OPENAI_OUTPUT_COST_PER_1K = 0.015


def _resolve_model() -> str:
    """Returns LLM_MODEL from env if set, else the default."""
    return os.environ.get("LLM_MODEL", "").strip() or OPENAI_MODEL_DEFAULT


def _is_reasoning_model(model: str) -> bool:
    """gpt-5 and o-series reasoning models drop `max_tokens`/custom `temperature`."""
    m = model.lower()
    return m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4")


class OpenAIProvider(BaseLLMProvider):
    @property
    def provider_name(self) -> str:
        return "openai"

    @property
    def model(self) -> str:
        return _resolve_model()

    def is_available(self) -> bool:
        return bool(resolve_api_key("OPENAI_API_KEY"))

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> LLMResponse:
        if openai is None:
            raise ProviderAPIError("openai", "openai package not installed")

        model = _resolve_model()
        client = openai.OpenAI(api_key=resolve_api_key("OPENAI_API_KEY"))
        # gpt-5 / o-series: token cap uses `max_completion_tokens` and only the
        # default temperature (1.0) is accepted, so we omit `temperature`.
        if _is_reasoning_model(model):
            # Reasoning tokens count against the completion budget, so give a
            # floor to leave room for actual output; keep reasoning minimal since
            # this is structured extraction, not a reasoning task.
            params = {
                "max_completion_tokens": max(max_tokens, 4000),
                "reasoning_effort": "minimal",
            }
        else:
            params = {"max_tokens": max_tokens, "temperature": temperature}
        t0 = time.time()
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                **params,
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
            model=model,
            provider="openai",
            latency_ms=latency_ms,
            cost_usd=cost,
        )
