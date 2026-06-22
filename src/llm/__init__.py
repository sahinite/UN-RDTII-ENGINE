"""
LLM cascade package. [Z2-4]

The implementation lives in src/mapping/llm_client.py (ADR-021).
This package re-exports the public API so callers can import from either path.

Public API:
    pin_active_provider()          -> BaseLLMProvider
        Call ONCE at engine startup. Reads LLM_PROVIDER env var, walks
        PROVIDER_CASCADE, pins the first available provider for the session.

    call_llm_with_cascade(system_prompt, user_prompt, ...) -> LLMResponse
        Calls the pinned provider. On cascade-failure exceptions falls through
        to the next available tier. Raises AllProvidersExhaustedError if all fail.

    get_active_model_version()     -> str
        Returns "{provider}/{model}" string for JSON output envelope.

    PROVIDER_CASCADE               — ordered list of BaseLLMProvider instances

Provider cascade order (pinned, never reordered):
    1. AnthropicProvider  — claude-sonnet-4-20250514
    2. OpenAIProvider     — gpt-4o
    3. GroqProvider       — qwen3-32b (fallback: qwen3.6-27b)
    4. OllamaProvider(4)  — qwen2.5:7b  (Apache 2.0, offline)
    5. OllamaProvider(5)  — granite3-8b  (Apache 2.0, offline)

NOTE: Llama 3.3 is explicitly excluded — non-Apache 2.0 license.
"""

from src.mapping.llm_client import (  # noqa: F401
    PROVIDER_CASCADE,
    call_llm_with_cascade,
    get_active_model_version,
    pin_active_provider,
)

__all__ = [
    "pin_active_provider",
    "call_llm_with_cascade",
    "get_active_model_version",
    "PROVIDER_CASCADE",
]
