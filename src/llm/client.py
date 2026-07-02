"""
LLM client — thin re-export of the 7-tier auto-cascade in src/mapping/llm_client.py.

The actual implementation lives in src/mapping/llm_client.py (ADR-021).
This module re-exports the public API so tooling and README guides that reference
src/llm/client.py work correctly.

PROVIDER_CASCADE order:
    1. anthropic / claude-sonnet-4-20250514   (primary, pinned)
    2. openai    / gpt-4o
    3. groq      / qwen3-32b                   (fallback: qwen3.6-27b)
    4. ollama    / qwen2.5:7b                  (offline, Apache 2.0)
    5. ollama    / granite3-dense:8b           (offline, Apache 2.0)

NOTE: Llama 3.3 is explicitly EXCLUDED — non-Apache 2.0 (Meta custom) license.
One provider is PINNED per run (set via .env LLM_PROVIDER=anthropic|openai|groq|ollama);
auto-fallback to the next tier ONLY on runtime failure.
"""

from src.mapping.llm_client import (  # noqa: F401  re-export
    PROVIDER_CASCADE,
    call_llm_with_cascade,
    get_active_model_version,
    pin_active_provider,
)
