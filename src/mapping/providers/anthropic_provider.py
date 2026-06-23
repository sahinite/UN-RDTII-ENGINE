"""Anthropic Claude provider. [Z2-4 ST1]"""

from __future__ import annotations

import os
import time

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

from src.mapping.base_provider import BaseLLMProvider
from src.mapping.exceptions import ProviderAPIError, ProviderRateLimitError, ProviderTimeoutError
from src.mapping.models import LLMResponse

ANTHROPIC_MODEL_DEFAULT = "claude-sonnet-4-20250514"

ANTHROPIC_INPUT_COST_PER_1K = 0.003   # $3 per 1M input tokens
ANTHROPIC_OUTPUT_COST_PER_1K = 0.015  # $15 per 1M output tokens


def _resolve_model() -> str:
    """Returns LLM_MODEL from env if set, else the default."""
    return os.environ.get("LLM_MODEL", "").strip() or ANTHROPIC_MODEL_DEFAULT


class AnthropicProvider(BaseLLMProvider):
    def __init__(self):
        self._client = None  # lazy init

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return _resolve_model()

    def is_available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> LLMResponse:
        if anthropic is None:
            raise ProviderAPIError("anthropic", "anthropic package not installed")

        if self._client is None:
            self._client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

        model = _resolve_model()
        t0 = time.time()
        try:
            msg = self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
        except anthropic.RateLimitError as e:
            raise ProviderRateLimitError("anthropic", str(e))
        except anthropic.APIStatusError as e:
            raise ProviderAPIError("anthropic", str(e))
        except anthropic.APIConnectionError as e:
            raise ProviderTimeoutError("anthropic", str(e))

        latency_ms = (time.time() - t0) * 1000
        input_tok = msg.usage.input_tokens
        output_tok = msg.usage.output_tokens
        cost = (
            input_tok / 1000 * ANTHROPIC_INPUT_COST_PER_1K
            + output_tok / 1000 * ANTHROPIC_OUTPUT_COST_PER_1K
        )

        return LLMResponse(
            text=msg.content[0].text,
            input_tokens=input_tok,
            output_tokens=output_tok,
            model=model,
            provider="anthropic",
            latency_ms=latency_ms,
            cost_usd=cost,
        )
