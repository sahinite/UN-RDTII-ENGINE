"""Tests for UI.auth_modal."""
from __future__ import annotations

import importlib
import os
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

os.environ["SECRET_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "test.apps.googleusercontent.com")
os.environ.setdefault("ADMIN_EMAILS", "admin@example.com")

# The auth_modal reads env vars (CLIENT_ID) at import time, so import happens
# only after the env is primed.
import gradio as gr  # noqa: E402

import UI.auth_modal as auth_modal  # noqa: E402
from src.auth import db as db_module  # noqa: E402
from src.auth import google_oauth as google_oauth_module  # noqa: E402


@pytest.fixture
def modal_module(monkeypatch, tmp_path):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test.apps.googleusercontent.com")
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    module = importlib.reload(auth_modal)
    db_path = tmp_path / "users.db"
    monkeypatch.setattr(db_module, "DEFAULT_DB_PATH", db_path)
    db_module.init_db(db_path)
    return module


def _google_user(email: str = "user@example.com", name: str = "Test User"):
    return google_oauth_module.GoogleUser(
        email=email, name=name, picture_url="https://ex/p.png", email_verified=True
    )


# ── render_overlay_html ──────────────────────────────────────────────────────


def test_render_overlay_html_contains_tagline(modal_module):
    html = modal_module.render_overlay_html()
    assert modal_module.TAGLINE in html


def test_render_overlay_html_contains_gis_script(modal_module):
    html = modal_module.gis_head_html()
    assert "accounts.google.com/gsi/client" in html


def test_render_overlay_html_contains_client_id(modal_module):
    html = modal_module.js_init_gis_button()
    assert "test.apps.googleusercontent.com" in html


def test_gis_button_uses_high_contrast_theme(modal_module):
    html = modal_module.js_init_gis_button()
    assert "theme: 'filled_blue'" in html


def test_gis_init_surfaces_origin_configuration_error(modal_module):
    html = modal_module.js_init_gis_button()
    assert "origin is not allowed" in html
    assert "Google sign-in is not configured" in html


def test_gis_init_watches_for_a_modal_reopened_by_server_event(modal_module):
    html = modal_module.js_init_gis_button()
    assert "MutationObserver" in html
    assert "__rdtiiGISMountWatcher" in html


def test_render_overlay_html_has_no_script_tag(modal_module):
    html = modal_module.render_overlay_html()
    assert "<script" not in html


def test_render_overlay_html_error_slot(modal_module):
    html = modal_module.render_overlay_html("Boom happened")
    assert "auth-error" in html
    assert "Boom happened" in html
    # Empty error should not render the div.
    assert 'class="auth-error"' not in modal_module.render_overlay_html()


def test_build_auth_modal_starts_hidden(modal_module):
    with gr.Blocks():
        components = modal_module.build_auth_modal()

    assert components["overlay_html"].visible is False


# ── render_header_html ───────────────────────────────────────────────────────


def test_render_header_html_signed_out(modal_module):
    html = modal_module.render_header_html(None)
    assert "Not signed in" in html


def test_render_header_html_signed_in(modal_module):
    from src.auth.session import RunContext

    ctx = RunContext(
        email="a@x.com",
        name="Alice",
        picture_url="https://ex/a.png",
        user_hash="h",
        is_admin=False,
    )
    html = modal_module.render_header_html(ctx)
    assert "Alice" in html
    assert "a@x.com" in html


def test_full_header_keeps_user_control_inside_brand_bar(modal_module):
    html = modal_module.render_full_header_html(None)
    assert 'class="rd-top"' in html
    assert 'class="rd-auth-header"' in html
    assert html.index('class="rd-auth-header"') < html.index("</header>")


# ── handle_sign_in ───────────────────────────────────────────────────────────


def test_handle_sign_in_verifies_and_upserts(modal_module, monkeypatch):
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    with patch(
        "src.auth.google_oauth.verify_id_token",
        return_value=_google_user(email="new@example.com"),
    ):
        result = modal_module.handle_sign_in("fake-token", None)

    ctx = result[0]
    assert ctx is not None
    assert ctx.email == "new@example.com"

    conn = db_module.get_connection(db_module.DEFAULT_DB_PATH)
    try:
        row = conn.execute(
            "SELECT email FROM users WHERE email = ?", ("new@example.com",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None


def test_handle_sign_in_invalid_token_keeps_overlay(modal_module, monkeypatch):
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    with patch("src.auth.google_oauth.verify_id_token", return_value=None):
        result = modal_module.handle_sign_in("bad-token", None)

    assert result[0] is None
    overlay_update = result[2]
    # gr.update returns a plain dict describing the change.
    assert overlay_update.get("visible") is True


def test_handle_sign_in_sets_admin_flag(modal_module, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com,other@example.com")
    with patch(
        "src.auth.google_oauth.verify_id_token",
        return_value=_google_user(email="admin@example.com", name="Admin"),
    ):
        result = modal_module.handle_sign_in("tok", None)

    ctx = result[0]
    assert ctx is not None
    assert ctx.is_admin is True


def test_handle_sign_in_non_admin(modal_module, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    with patch(
        "src.auth.google_oauth.verify_id_token",
        return_value=_google_user(email="regular@example.com"),
    ):
        result = modal_module.handle_sign_in("tok", None)

    ctx = result[0]
    assert ctx is not None
    assert ctx.is_admin is False


def test_handle_sign_in_bypass_mode(modal_module, monkeypatch):
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    monkeypatch.setenv("DEV_USER_EMAIL", "dev@x.com")
    result = modal_module.handle_sign_in("", None)
    ctx = result[0]
    assert ctx is not None
    assert ctx.email == "dev@x.com"


# ── handle_sign_out ──────────────────────────────────────────────────────────


def test_handle_sign_out_returns_no_ctx(modal_module):
    result = modal_module.handle_sign_out(object())
    assert result[0] is None
    assert result[2].get("visible") is True  # overlay shown again
    assert result[3].get("visible") is False
    assert result[4].get("visible") is False


# ── module import guards ─────────────────────────────────────────────────────


def test_import_fails_without_client_id_when_not_bypass(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    import UI.auth_modal as mod

    with pytest.raises(RuntimeError, match="GOOGLE_OAUTH_CLIENT_ID"):
        importlib.reload(mod)


def test_import_succeeds_in_bypass_mode_without_client_id(monkeypatch):
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    import UI.auth_modal as mod

    reloaded = importlib.reload(mod)
    assert reloaded is not None
    # Restore for downstream tests since reload mutates module-level state.
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test.apps.googleusercontent.com")
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    importlib.reload(mod)
