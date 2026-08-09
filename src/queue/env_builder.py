import os

PROVIDER_KEYS: frozenset[str] = frozenset({
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "DEEPSEEK_API_KEY",
    "GROQ_API_KEY",
    "DASHSCOPE_API_KEY",
    "LLM_API_KEY",
    "MISTRAL_API_KEY",
    "LLM_PROVIDER",
    "LLM_MODEL",
})


def build_subprocess_env(
    run_context,
    run_id: str,
    base_env: dict[str, str] | None = None,
    outputs_root: str = "outputs",
    logs_root: str = "logs",
) -> dict[str, str]:
    env: dict[str, str] = dict(os.environ) if base_env is None else dict(base_env)

    # Strip parent-inherited provider keys BEFORE merging user secrets so an
    # operator's stray .env cannot leak into another user's run.
    for key in PROVIDER_KEYS:
        env.pop(key, None)

    for key, value in run_context.secrets.items():
        env[key] = str(value)

    config = getattr(run_context, "config", {}) or {}
    provider = config.get("LLM_PROVIDER")
    if provider:
        if provider == "ollama_granite":
            env["LLM_PROVIDER"] = "ollama"
            env["LLM_MODEL"] = "granite3-8b"
        else:
            env["LLM_PROVIDER"] = str(provider)
    if config.get("LLM_MODEL"):
        env["LLM_MODEL"] = str(config["LLM_MODEL"])

    user_hash = run_context.user_hash
    env["RDTII_OUTPUT_DIR"] = f"{outputs_root}/{user_hash}/{run_id}"
    env["RDTII_LOG_DIR"] = f"{logs_root}/{user_hash}/{run_id}"

    return {k: str(v) for k, v in env.items()}
