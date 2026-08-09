"""Authenticated user's profile, API key, and provider settings."""

from __future__ import annotations

import os
from html import escape
from typing import Any

import gradio as gr

from src.auth import db, session
from .auth_modal import profile_picture_src

PROVIDER_OPTIONS: list[tuple[str, str]] = [
    ("Anthropic", "anthropic"),
    ("OpenAI", "openai"),
    ("Gemini", "gemini"),
    ("DeepSeek", "deepseek"),
    ("Groq", "groq"),
    ("DashScope / Qwen", "qwen"),
]

PROVIDER_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-5",
    "gemini": "gemini-2.5-flash",
    "deepseek": "deepseek-chat",
    "groq": "qwen/qwen3-32b",
    "qwen": "qwen-plus",
}

PROVIDER_KEY_FIELDS: list[tuple[str, str, str]] = [
    ("LLM_API_KEY", "LLM API key (required)", "Key for the selected provider"),
    ("MISTRAL_API_KEY", "Mistral API key (required)", "Mistral OCR key"),
]

_REQUIRED_KEYS = {key_name: label.replace(" (required)", "")
                  for key_name, label, _placeholder in PROVIDER_KEY_FIELDS}


def selected_provider(ctx: Any) -> str:
    provider = getattr(ctx, "config", {}).get("LLM_PROVIDER") if ctx else None
    valid = {value for _label, value in PROVIDER_OPTIONS}
    return provider if provider in valid else "openai"


def default_model_for_provider(provider: str) -> str:
    return PROVIDER_DEFAULT_MODELS.get(provider, PROVIDER_DEFAULT_MODELS["openai"])


def selected_model(ctx: Any, provider: str | None = None) -> str:
    active_provider = provider or selected_provider(ctx)
    configured = getattr(ctx, "config", {}).get("LLM_MODEL") if ctx else None
    return str(configured).strip() if configured else default_model_for_provider(active_provider)


def provider_model_update(provider: str):
    default_model = default_model_for_provider(provider)
    return gr.update(choices=[default_model], value=default_model)


def key_visibility_updates(show_keys: bool) -> tuple:
    field_type = "text" if show_keys else "password"
    return tuple(gr.update(type=field_type) for _ in PROVIDER_KEY_FIELDS)


def reset_key_visibility() -> tuple:
    return False, *key_visibility_updates(False)


def render_profile_picture(ctx: Any) -> str:
    src = profile_picture_src(ctx)
    name = str(getattr(ctx, "name", "") or "User")
    return (
        '<div class="rd-settings-avatar-wrap">'
        f'<img class="rd-settings-avatar" src="{escape(src, quote=True)}" '
        f'alt="{escape(name, quote=True)} profile picture">'
        '</div>'
    )


def missing_required_keys_message(ctx: Any) -> str | None:
    secrets = getattr(ctx, "secrets", {}) or {}
    missing = [label for key_name, label in _REQUIRED_KEYS.items()
               if not str(secrets.get(key_name, "")).strip()]
    if not missing:
        return None
    return (
        f"Set up {' and '.join(missing)} in My Settings before running the pipeline."
    )


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
            "", "", render_profile_picture(None), "", selected_provider(None), selected_model(None),
            status_message or "Sign in to manage your settings.",
            *["" for _ in PROVIDER_KEY_FIELDS],
        )
    provider = selected_provider(ctx)
    secrets = getattr(ctx, "secrets", {}) or {}
    return (
        ctx.name,
        ctx.email,
        render_profile_picture(ctx),
        _current_contact(ctx),
        provider,
        selected_model(ctx, provider),
        status_message,
        *[str(secrets.get(key_name, "") or "")
          for key_name, _label, _placeholder in PROVIDER_KEY_FIELDS],
    )


def save_settings(contact: str, provider: str, model: str, *key_values_and_ctx):
    *key_values, ctx = key_values_and_ctx
    if ctx is None:
        return None, *settings_values(None, "Sign in required.")

    submitted = {
        key_name: str(raw_value or "").strip()
        for (key_name, _label, _placeholder), raw_value
        in zip(PROVIDER_KEY_FIELDS, key_values)
    }
    missing = [
        label for key_name, label in _REQUIRED_KEYS.items()
        if not submitted.get(key_name) and not str(ctx.secrets.get(key_name, "")).strip()
    ]
    if missing:
        message = f"Required: {' and '.join(missing)}."
        return ctx, *settings_values(ctx, message)

    conn = db.get_connection(db.DEFAULT_DB_PATH)
    try:
        session.update_user_contact(ctx.email, str(contact or ""), conn)
        config = dict(ctx.config or {})
        valid_providers = {value for _label, value in PROVIDER_OPTIONS}
        provider = provider if provider in valid_providers else selected_provider(ctx)
        config["LLM_PROVIDER"] = provider
        config["LLM_MODEL"] = str(model or "").strip() or default_model_for_provider(provider)
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
        with gr.Row(elem_classes=["rd-settings-profile"]):
            profile_picture = gr.HTML(
                render_profile_picture(None), scale=0, min_width=104
            )
            with gr.Column():
                with gr.Row():
                    profile_name = gr.Textbox(label="Name", interactive=False)
                    profile_email = gr.Textbox(label="Email", interactive=False)
        contact = gr.Textbox(label="Contact")

        provider = gr.Dropdown(
            PROVIDER_OPTIONS,
            label="Active LLM provider",
            value="openai",
        )
        model = gr.Dropdown(
            [PROVIDER_DEFAULT_MODELS["openai"]],
            label="Model",
            value=PROVIDER_DEFAULT_MODELS["openai"],
            allow_custom_value=True,
        )
        provider.input(
            provider_model_update,
            inputs=provider,
            outputs=model,
            show_progress="hidden",
        )

        key_components = []
        with gr.Accordion("API keys", open=True):
            for key_name, label, placeholder in PROVIDER_KEY_FIELDS:
                key_components.append(
                    gr.Textbox(
                        label=label,
                        type="password",
                        placeholder=placeholder,
                    )
                )
            show_keys = gr.Checkbox(label="Show API keys", value=False)
            show_keys.change(
                key_visibility_updates,
                inputs=show_keys,
                outputs=key_components,
                show_progress="hidden",
            )

        with gr.Row():
            save_button = gr.Button("Save settings", variant="primary")
            sign_out_button = gr.Button("Sign out")
        status = gr.Markdown("")

    return {
        "tab": tab,
        "profile_name": profile_name,
        "profile_email": profile_email,
        "profile_picture": profile_picture,
        "contact": contact,
        "provider": provider,
        "model": model,
        "key_components": key_components,
        "show_keys": show_keys,
        "save_button": save_button,
        "sign_out_button": sign_out_button,
        "status": status,
        "value_outputs": [
            profile_name,
            profile_email,
            profile_picture,
            contact,
            provider,
            model,
            status,
            *key_components,
        ],
    }
