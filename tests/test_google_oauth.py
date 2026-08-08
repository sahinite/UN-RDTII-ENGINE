"""Tests for src.auth.google_oauth."""
from __future__ import annotations

import importlib
import os
from unittest.mock import patch

import pytest

os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "test-client-id.apps.googleusercontent.com")


@pytest.fixture
def google_oauth_module():
    module = importlib.import_module("src.auth.google_oauth")
    return importlib.reload(module)


def _valid_idinfo(**overrides):
    idinfo = {
        "iss": "https://accounts.google.com",
        "aud": "test-client-id.apps.googleusercontent.com",
        "email": "user@example.com",
        "email_verified": True,
        "name": "Test User",
        "picture": "https://example.com/pic.png",
        "sub": "1234567890",
    }
    idinfo.update(overrides)
    return idinfo


def test_valid_token_returns_user(google_oauth_module):
    with patch(
        "src.auth.google_oauth.google_id_token.verify_oauth2_token",
        return_value=_valid_idinfo(),
    ):
        user = google_oauth_module.verify_id_token("fake-token")
    assert user is not None
    assert user.email == "user@example.com"
    assert user.name == "Test User"
    assert user.picture_url == "https://example.com/pic.png"
    assert user.email_verified is True


def test_email_lowercased(google_oauth_module):
    with patch(
        "src.auth.google_oauth.google_id_token.verify_oauth2_token",
        return_value=_valid_idinfo(email="Alice@Gmail.com"),
    ):
        user = google_oauth_module.verify_id_token("fake-token")
    assert user is not None
    assert user.email == "alice@gmail.com"


def test_missing_name_default(google_oauth_module):
    idinfo = _valid_idinfo()
    idinfo.pop("name")
    with patch(
        "src.auth.google_oauth.google_id_token.verify_oauth2_token",
        return_value=idinfo,
    ):
        user = google_oauth_module.verify_id_token("fake-token")
    assert user is not None
    assert user.name == ""


def test_missing_picture_default(google_oauth_module):
    idinfo = _valid_idinfo()
    idinfo.pop("picture")
    with patch(
        "src.auth.google_oauth.google_id_token.verify_oauth2_token",
        return_value=idinfo,
    ):
        user = google_oauth_module.verify_id_token("fake-token")
    assert user is not None
    assert user.picture_url == ""


def test_wrong_issuer_returns_none(google_oauth_module):
    with patch(
        "src.auth.google_oauth.google_id_token.verify_oauth2_token",
        return_value=_valid_idinfo(iss="evil.com"),
    ):
        user = google_oauth_module.verify_id_token("fake-token")
    assert user is None


def test_unverified_email_returns_none(google_oauth_module):
    with patch(
        "src.auth.google_oauth.google_id_token.verify_oauth2_token",
        return_value=_valid_idinfo(email_verified=False),
    ):
        user = google_oauth_module.verify_id_token("fake-token")
    assert user is None


def test_missing_email_verified_returns_none(google_oauth_module):
    idinfo = _valid_idinfo()
    idinfo.pop("email_verified")
    with patch(
        "src.auth.google_oauth.google_id_token.verify_oauth2_token",
        return_value=idinfo,
    ):
        user = google_oauth_module.verify_id_token("fake-token")
    assert user is None


def test_verify_raises_returns_none(google_oauth_module):
    with patch(
        "src.auth.google_oauth.google_id_token.verify_oauth2_token",
        side_effect=ValueError("bad token"),
    ):
        user = google_oauth_module.verify_id_token("fake-token")
    assert user is None


def test_missing_client_id_fails_at_import(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    import src.auth.google_oauth as module

    with pytest.raises(RuntimeError, match="GOOGLE_OAUTH_CLIENT_ID"):
        importlib.reload(module)
