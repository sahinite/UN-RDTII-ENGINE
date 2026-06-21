"""BaseLLMProvider abstract interface. [Z2-4 ST1]"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.mapping.models import LLMResponse


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
