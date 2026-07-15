"""BaseLLMProvider abstract interface."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

from src.mapping.models import LLMResponse

# Single unified API key for whichever provider is pinned per run.
UNIFIED_API_KEY_ENV = "LLM_API_KEY"


def resolve_api_key(provider_env: str) -> str:
    """
    Resolve a provider's API key.

    Precedence: the provider-specific var (e.g. ANTHROPIC_API_KEY) wins if set,
    otherwise fall back to the single unified LLM_API_KEY. Only one provider is
    pinned per run, so LLM_API_KEY is unambiguous.
    """
    return (
        os.environ.get(provider_env, "").strip()
        or os.environ.get(UNIFIED_API_KEY_ENV, "").strip()
    )


class BaseLLMProvider(ABC):
    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Returns lowercase provider identifier."""

    @property
    @abstractmethod
    def model(self) -> str:
        """Returns exact model string."""

    @abstractmethod
    def is_available(self) -> bool:
        """
        Returns True if the provider's API key / service is configured and reachable.
        MUST NOT make a real API inference call.
        """

    @abstractmethod
    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """
        Makes a single completion call.
        Raises ProviderRateLimitError, ProviderAPIError, or ProviderTimeoutError.
        Never catches these internally.
        """
