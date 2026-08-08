"""Tests for src.auth.dev_bypass."""
from __future__ import annotations

import pytest

from src.auth import dev_bypass


def test_is_bypass_enabled_true(monkeypatch):
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    assert dev_bypass.is_bypass_enabled() is True


def test_is_bypass_enabled_false_when_unset(monkeypatch):
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    assert dev_bypass.is_bypass_enabled() is False


def test_is_bypass_enabled_false_when_0(monkeypatch):
    monkeypatch.setenv("AUTH_DEV_BYPASS", "0")
    assert dev_bypass.is_bypass_enabled() is False


def test_is_bypass_enabled_false_when_other_value(monkeypatch):
    monkeypatch.setenv("AUTH_DEV_BYPASS", "yes")
    assert dev_bypass.is_bypass_enabled() is False


def test_get_dev_user_returns_user(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "test.apps.googleusercontent.com")
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    monkeypatch.setenv("DEV_USER_EMAIL", "dev@example.com")

    user = dev_bypass.get_dev_user()

    assert user.email == "dev@example.com"
    assert user.name == "dev"
    assert user.picture_url == ""
    assert user.email_verified is True


def test_get_dev_user_raises_when_bypass_off(monkeypatch):
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    monkeypatch.setenv("DEV_USER_EMAIL", "dev@example.com")
    with pytest.raises(RuntimeError, match="bypass disabled"):
        dev_bypass.get_dev_user()


def test_get_dev_user_raises_when_email_missing(monkeypatch):
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    monkeypatch.delenv("DEV_USER_EMAIL", raising=False)
    with pytest.raises(RuntimeError, match="DEV_USER_EMAIL is required"):
        dev_bypass.get_dev_user()


def test_get_dev_user_raises_when_email_empty_string(monkeypatch):
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    monkeypatch.setenv("DEV_USER_EMAIL", "")
    with pytest.raises(RuntimeError, match="DEV_USER_EMAIL is required"):
        dev_bypass.get_dev_user()


def test_check_bypass_safety_returns_none_when_off(monkeypatch):
    monkeypatch.delenv("AUTH_DEV_BYPASS", raising=False)
    assert dev_bypass.check_bypass_safety("0.0.0.0") is None
    assert dev_bypass.check_bypass_safety("example.com") is None


@pytest.mark.parametrize("bind", ["127.0.0.1", "localhost", "::1"])
def test_check_bypass_safety_returns_none_for_localhost(monkeypatch, bind):
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    assert dev_bypass.check_bypass_safety(bind) is None


def test_check_bypass_safety_warns_for_public_bind(monkeypatch):
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    result = dev_bypass.check_bypass_safety("0.0.0.0")
    assert result is not None
    assert "WARNING" in result
    assert "0.0.0.0" in result


def test_check_bypass_safety_warns_for_domain(monkeypatch):
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    result = dev_bypass.check_bypass_safety("example.com")
    assert result is not None
    assert "WARNING" in result
    assert "example.com" in result
