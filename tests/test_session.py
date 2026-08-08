"""Tests for src.auth.session."""
from __future__ import annotations

import importlib
import os
import time

import pytest
from cryptography.fernet import Fernet

os.environ["SECRET_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ.setdefault(
    "GOOGLE_OAUTH_CLIENT_ID", "test.apps.googleusercontent.com"
)

import src.auth.crypto as crypto_module
import src.auth.google_oauth as google_oauth_module
import src.auth.session as session_module
from src.auth import db as db_module


@pytest.fixture(autouse=True)
def _reload_modules():
    importlib.reload(crypto_module)
    importlib.reload(google_oauth_module)
    importlib.reload(session_module)
    yield


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    db_module.init_db(db_path)
    connection = db_module.get_connection(db_path)
    try:
        yield connection
    finally:
        connection.close()


def _google_user(
    email: str = "user@example.com",
    name: str = "Test User",
    picture_url: str = "https://example.com/p.png",
) -> google_oauth_module.GoogleUser:
    return google_oauth_module.GoogleUser(
        email=email, name=name, picture_url=picture_url, email_verified=True
    )


def test_upsert_new_user(conn):
    session_module.upsert_user_from_google(_google_user(), conn)

    row = conn.execute("SELECT * FROM users WHERE email = ?", ("user@example.com",)).fetchone()
    assert row is not None
    assert row["email"] == "user@example.com"
    assert row["name"] == "Test User"
    assert row["picture_url"] == "https://example.com/p.png"
    assert row["user_hash"] == session_module.sha256_hash("user@example.com")


def test_upsert_updates_existing(conn):
    session_module.upsert_user_from_google(_google_user(name="Old", picture_url="old.png"), conn)
    original = conn.execute(
        "SELECT created_at, contact FROM users WHERE email = ?", ("user@example.com",)
    ).fetchone()
    session_module.update_user_contact("user@example.com", "555-1234", conn)

    time.sleep(1.05)
    session_module.upsert_user_from_google(_google_user(name="New Name", picture_url="new.png"), conn)

    row = conn.execute("SELECT * FROM users WHERE email = ?", ("user@example.com",)).fetchone()
    assert row["name"] == "New Name"
    assert row["picture_url"] == "new.png"
    assert row["contact"] == "555-1234"
    assert row["created_at"] == original["created_at"]


def test_upsert_preserves_contact(conn):
    session_module.upsert_user_from_google(_google_user(), conn)
    session_module.update_user_contact("user@example.com", "+1-555-9999", conn)
    session_module.upsert_user_from_google(_google_user(name="Second"), conn)

    row = conn.execute("SELECT contact FROM users WHERE email = ?", ("user@example.com",)).fetchone()
    assert row["contact"] == "+1-555-9999"


def test_load_run_context_returns_none_for_missing_user(conn):
    ctx = session_module.load_run_context("nobody@example.com", conn, admin_emails=[])
    assert ctx is None


def test_load_run_context_has_no_secrets_initially(conn):
    session_module.upsert_user_from_google(_google_user(), conn)
    ctx = session_module.load_run_context("user@example.com", conn, admin_emails=[])
    assert ctx is not None
    assert ctx.secrets == {}
    assert ctx.config == {}
    assert ctx.email == "user@example.com"
    assert ctx.name == "Test User"


def test_save_and_load_secret_roundtrip(conn):
    session_module.upsert_user_from_google(_google_user(), conn)
    session_module.save_user_secret("user@example.com", "OPENAI_API_KEY", "sk-abc123", conn)

    ctx = session_module.load_run_context("user@example.com", conn, admin_emails=[])
    assert ctx is not None
    assert ctx.secrets["OPENAI_API_KEY"] == "sk-abc123"


def test_save_secret_is_encrypted_at_rest(conn):
    session_module.upsert_user_from_google(_google_user(), conn)
    plaintext = "sk-super-secret-value-42"
    session_module.save_user_secret("user@example.com", "OPENAI_API_KEY", plaintext, conn)

    row = conn.execute(
        "SELECT value_encrypted FROM user_secrets WHERE email = ? AND key_name = ?",
        ("user@example.com", "OPENAI_API_KEY"),
    ).fetchone()
    assert plaintext.encode("utf-8") not in bytes(row["value_encrypted"])


def test_save_secret_updates_existing(conn):
    session_module.upsert_user_from_google(_google_user(), conn)
    session_module.save_user_secret("user@example.com", "OPENAI_API_KEY", "first", conn)
    session_module.save_user_secret("user@example.com", "OPENAI_API_KEY", "second", conn)

    ctx = session_module.load_run_context("user@example.com", conn, admin_emails=[])
    assert ctx is not None
    assert ctx.secrets["OPENAI_API_KEY"] == "second"


def test_delete_user_secret(conn):
    session_module.upsert_user_from_google(_google_user(), conn)
    session_module.save_user_secret("user@example.com", "OPENAI_API_KEY", "sk-a", conn)
    session_module.save_user_secret("user@example.com", "ANTHROPIC_API_KEY", "sk-b", conn)

    session_module.delete_user_secret("user@example.com", "OPENAI_API_KEY", conn)

    ctx = session_module.load_run_context("user@example.com", conn, admin_emails=[])
    assert ctx is not None
    assert "OPENAI_API_KEY" not in ctx.secrets
    assert ctx.secrets["ANTHROPIC_API_KEY"] == "sk-b"


def test_save_and_load_config_roundtrip(conn):
    session_module.upsert_user_from_google(_google_user(), conn)
    config = {
        "last_economy": "Singapore",
        "last_pillar": 7,
        "LLM_PROVIDER": "openai",
        "nested": {"a": 1, "b": [1, 2, 3]},
    }
    session_module.save_user_config("user@example.com", config, conn)

    ctx = session_module.load_run_context("user@example.com", conn, admin_emails=[])
    assert ctx is not None
    assert ctx.config == config


def test_is_admin_true_for_matching_email(conn):
    session_module.upsert_user_from_google(_google_user(), conn)
    ctx = session_module.load_run_context(
        "USER@example.com", conn, admin_emails=["user@example.com"]
    )
    assert ctx is not None
    assert ctx.is_admin is True


def test_is_admin_false_for_non_matching(conn):
    session_module.upsert_user_from_google(_google_user(), conn)
    ctx = session_module.load_run_context(
        "user@example.com", conn, admin_emails=["other@example.com"]
    )
    assert ctx is not None
    assert ctx.is_admin is False


def test_is_admin_empty_list(conn):
    session_module.upsert_user_from_google(_google_user(), conn)
    ctx = session_module.load_run_context("user@example.com", conn, admin_emails=[])
    assert ctx is not None
    assert ctx.is_admin is False


def test_cascade_delete_removes_secrets(conn):
    session_module.upsert_user_from_google(_google_user(), conn)
    session_module.save_user_secret("user@example.com", "OPENAI_API_KEY", "sk-a", conn)
    session_module.save_user_config("user@example.com", {"k": "v"}, conn)

    conn.execute("DELETE FROM users WHERE email = ?", ("user@example.com",))
    conn.commit()

    n_secrets = conn.execute(
        "SELECT COUNT(*) FROM user_secrets WHERE email = ?", ("user@example.com",)
    ).fetchone()[0]
    n_configs = conn.execute(
        "SELECT COUNT(*) FROM user_configs WHERE email = ?", ("user@example.com",)
    ).fetchone()[0]
    assert n_secrets == 0
    assert n_configs == 0


def test_update_contact(conn):
    session_module.upsert_user_from_google(_google_user(), conn)
    session_module.update_user_contact("user@example.com", "+44-20-7946-0958", conn)

    row = conn.execute(
        "SELECT contact FROM users WHERE email = ?", ("user@example.com",)
    ).fetchone()
    assert row["contact"] == "+44-20-7946-0958"
