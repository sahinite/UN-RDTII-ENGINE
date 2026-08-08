"""Authenticated user's profile, API key, and provider settings."""

from __future__ import annotations

import os
from typing import Any

import gradio as gr

from src.auth import db, session

PROVIDER_OPTIONS: list[tuple[str, str]] = [
    ("Anthropic", "anthropic"),
    ("OpenAI", "openai"),
    ("Gemini", "gemini"),
    ("DeepSeek", "deepseek"),
    ("Groq", "groq"),
    ("DashScope / Qwen", "qwen"),
    ("Ollama Qwen", "ollama"),
    ("Ollama Granite", "ollama_granite"),
]

PROVIDER_KEY_FIELDS: list[tuple[str, str, str]] = [
    ("ANTHROPIC_API_KEY", "Anthropic API key", "sk-ant-..."),
    ("OPENAI_API_KEY", "OpenAI API key", "sk-..."),
    ("GEMINI_API_KEY", "Gemini API key", "AI..."),
    ("DEEPSEEK_API_KEY", "DeepSeek API key", "sk-..."),
    ("GROQ_API_KEY", "Groq API key", "gsk_..."),
    ("DASHSCOPE_API_KEY", "DashScope API key", "sk-..."),
    ("OLLAMA_BASE_URL", "Ollama base URL", "http://localhost:11434"),
    ("OLLAMA_NUM_CTX", "Ollama context window", "8192"),
]

_PROVIDER_TO_REQUIRED_KEY = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "ollama": "OLLAMA_BASE_URL",
    "ollama_granite": "OLLAMA_BASE_URL",
}


def selected_provider(ctx: Any) -> str:
    provider = getattr(ctx, "config", {}).get("LLM_PROVIDER") if ctx else None
    valid = {value for _label, value in PROVIDER_OPTIONS}
    return provider if provider in valid else "openai"


def missing_provider_key_message(ctx: Any) -> str | None:
    provider = selected_provider(ctx)
    required = _PROVIDER_TO_REQUIRED_KEY.get(provider)
    if not required:
        return None
    if required.startswith("OLLAMA_"):
        return None
    if required not in getattr(ctx, "secrets", {}):
        return (
            f"No API key is stored for {provider}. Open My Settings and add "
            f"{required} before running the pipeline."
        )
    return None


def _admin_emails() -> list[str]:
    return [e.strip() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]


def _reload_ctx(email: str):
    conn = db.get_connection(db.DEFAULT_DB_PATH)
    try:
        return session.load_run_context(email, conn, _admin_emails())
    finally:
        conn.close()


def _current_contact(ctx: Any) -> str:
    if ctx is None:
        return ""
    conn = db.get_connection(db.DEFAULT_DB_PATH)
    try:
        row = conn.execute(
            "SELECT contact FROM users WHERE email=?", (ctx.email.lower(),)
        ).fetchone()
        return (row["contact"] or "") if row else ""
    finally:
        conn.close()


def settings_values(ctx: Any, status_message: str = "") -> tuple:
    if ctx is None:
        return (
            "", "", "", "", selected_provider(None),
            status_message or "Sign in to manage your settings.",
            *["" for _ in PROVIDER_KEY_FIELDS],
        )
    secret_values = []
    for key_name, _label, _placeholder in PROVIDER_KEY_FIELDS:
        if key_name.startswith("OLLAMA_"):
            secret_values.append(ctx.secrets.get(key_name, ""))
        else:
            secret_values.append("")
    return (
        ctx.name,
        ctx.email,
        ctx.picture_url,
        _current_contact(ctx),
        selected_provider(ctx),
        status_message,
        *secret_values,
    )


def save_settings(contact: str, provider: str, *key_values_and_ctx):
    *key_values, ctx = key_values_and_ctx
    if ctx is None:
        return None, *settings_values(None, "Sign in required.")

    conn = db.get_connection(db.DEFAULT_DB_PATH)
    try:
        session.update_user_contact(ctx.email, str(contact or ""), conn)
        config = dict(ctx.config or {})
        config["LLM_PROVIDER"] = provider
        session.save_user_config(ctx.email, config, conn)

        for (key_name, _label, _placeholder), raw_value in zip(PROVIDER_KEY_FIELDS, key_values):
            value = str(raw_value or "").strip()
            if value:
                session.save_user_secret(ctx.email, key_name, value, conn)

        new_ctx = session.load_run_context(ctx.email, conn, _admin_emails())
    finally:
        conn.close()

    return new_ctx, *settings_values(new_ctx, "Saved. Applies to your next pipeline run.")


def build_settings_screen(visible: bool = False) -> dict:
    with gr.Tab("My Settings", visible=visible) as tab:
        with gr.Row():
            profile_name = gr.Textbox(label="Name", interactive=False)
            profile_email = gr.Textbox(label="Email", interactive=False)
        picture_url = gr.Textbox(label="Picture URL", interactive=False)
        contact = gr.Textbox(label="Contact")

        provider = gr.Dropdown(
            PROVIDER_OPTIONS,
            label="Active LLM provider",
            value="openai",
        )

        key_components = []
        with gr.Accordion("API keys", open=True):
            for key_name, label, placeholder in PROVIDER_KEY_FIELDS:
                field_type = "text" if key_name.startswith("OLLAMA_") else "password"
                key_components.append(
                    gr.Textbox(
                        label=label,
                        type=field_type,
                        placeholder=placeholder,
                    )
                )

        with gr.Row():
            save_button = gr.Button("Save settings", variant="primary")
            sign_out_button = gr.Button("Sign out")
        status = gr.Markdown("")

    return {
        "tab": tab,
        "profile_name": profile_name,
        "profile_email": profile_email,
        "picture_url": picture_url,
        "contact": contact,
        "provider": provider,
        "key_components": key_components,
        "save_button": save_button,
        "sign_out_button": sign_out_button,
        "status": status,
        "value_outputs": [
            profile_name,
            profile_email,
            picture_url,
            contact,
            provider,
            status,
            *key_components,
        ],
    }
