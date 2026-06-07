"""
LLM client — abstracts provider behind a single interface and implements the
5-tier auto-cascade. [Z2-4]

PROVIDER_CASCADE = [
    ("anthropic", "claude-sonnet-4-20250514"),   # primary, pinned
    ("openai",    "gpt-4o"),
    ("groq",      "deepseek-v3"),                 # / qwen-2.5, free tier
    ("ollama",    "qwen2.5:7b"),                  # offline, Apache 2.0
    ("ollama",    "granite3-8b"),                 # offline, Apache 2.0
]
# NOTE: Llama 3.3 explicitly EXCLUDED — non-Apache 2.0 (Meta custom) license.
# One provider PINNED per run (set via .env LLM_PROVIDER); auto-fallback to
# next tier ONLY on runtime failure — no mid-run model switching by design.

TODO: class BaseLLMClient -> .complete(prompt, system)
TODO: def get_client() -> BaseLLMClient with cascade fallback wrapper
"""
