"""Google Gemini provider — OpenAI-compatible API."""

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

# Gemini exposes an OpenAI-compatible endpoint, so we reuse the openai client
# with a base_url override (same pattern as DeepSeek) rather than a new SDK.
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_MODEL_DEFAULT = "gemini-2.5-flash"

# Approximate pricing (gemini-2.5-flash) for cost logging.
GEMINI_INPUT_COST_PER_1K = 0.0003    # $0.30 per 1M input tokens
GEMINI_OUTPUT_COST_PER_1K = 0.0025   # $2.50 per 1M output tokens


def _resolve_model() -> str:
    return os.environ.get("LLM_MODEL", "").strip() or GEMINI_MODEL_DEFAULT


def _resolve_key() -> str:
    """GEMINI_API_KEY, else GOOGLE_API_KEY, else the unified LLM_API_KEY."""
    return resolve_api_key("GEMINI_API_KEY") or resolve_api_key("GOOGLE_API_KEY")


class GeminiProvider(BaseLLMProvider):
    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model(self) -> str:
        return _resolve_model()

    def is_available(self) -> bool:
        return bool(_resolve_key())

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> LLMResponse:
        if openai is None:
            raise ProviderAPIError("gemini", "openai package not installed")

        model = _resolve_model()
        client = openai.OpenAI(api_key=_resolve_key(), base_url=GEMINI_BASE_URL)
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
            raise ProviderRateLimitError("gemini", str(e))
        except openai.APIStatusError as e:
            raise ProviderAPIError("gemini", str(e))
        except openai.APIConnectionError as e:
            raise ProviderTimeoutError("gemini", str(e))

        latency_ms = (time.time() - t0) * 1000
        usage = resp.usage
        cost = (
            usage.prompt_tokens / 1000 * GEMINI_INPUT_COST_PER_1K
            + usage.completion_tokens / 1000 * GEMINI_OUTPUT_COST_PER_1K
        )

        return LLMResponse(
            text=resp.choices[0].message.content,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            model=model,
            provider="gemini",
            latency_ms=latency_ms,
            cost_usd=cost,
        )
