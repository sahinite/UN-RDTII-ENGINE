"""
Unit tests for 5-Tier Auto-Cascade Engine.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from tests.fixtures.z2_4.fixtures import make_llm_response


def _make_provider(name: str, available: bool = True, fail_with=None):
    m = MagicMock()
    m.provider_name = name
    m.model = f"{name}-model"
    m.is_available.return_value = available
    if fail_with:
        m.complete.side_effect = fail_with
    else:
        m.complete.return_value = make_llm_response(provider=name, model=f"{name}-model")
    return m


def test_pin_active_provider_selects_anthropic_first():
    """Anthropic available → pinned as priority 1."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test", "LLM_PROVIDER": ""}):
        import src.mapping.llm_client as client
        client._SESSION_PROVIDER = None
        p = client.pin_active_provider()
        assert p.provider_name == "anthropic"


def test_pin_active_provider_respects_env_override():
    """LLM_PROVIDER=groq env var → pins groq if available."""
    import src.mapping.llm_client as client

    groq_mock = _make_provider("groq")
    client._SESSION_PROVIDER = None
    with patch.dict(os.environ, {"LLM_PROVIDER": "groq"}):
        with patch.object(client, "PROVIDER_CASCADE", [
            _make_provider("anthropic", available=False),
            _make_provider("openai", available=False),
            groq_mock,
        ]):
            p = client.pin_active_provider()
    assert p.provider_name == "groq"


def test_cascade_falls_through_to_openai_on_rate_limit():
    """Anthropic rate-limits → cascade falls to OpenAI."""
    from src.mapping.exceptions import ProviderRateLimitError

    anthropic_mock = _make_provider("anthropic", fail_with=ProviderRateLimitError("anthropic", "429"))
    openai_mock = _make_provider("openai")

    import src.mapping.llm_client as client
    client._SESSION_PROVIDER = anthropic_mock
    with patch.object(client, "PROVIDER_CASCADE", [anthropic_mock, openai_mock]):
        resp = client.call_llm_with_cascade("sys", "user")
    assert resp.provider == "openai"


def test_cascade_falls_through_on_api_error():
    """Anthropic API error → cascade to next provider."""
    from src.mapping.exceptions import ProviderAPIError

    anthropic_mock = _make_provider("anthropic", fail_with=ProviderAPIError("anthropic", "500"))
    openai_mock = _make_provider("openai")

    import src.mapping.llm_client as client
    client._SESSION_PROVIDER = anthropic_mock
    with patch.object(client, "PROVIDER_CASCADE", [anthropic_mock, openai_mock]):
        resp = client.call_llm_with_cascade("sys", "user")
    assert resp.provider == "openai"


def test_all_providers_exhausted_raises():
    """All providers fail → AllProvidersExhaustedError."""
    from src.mapping.exceptions import AllProvidersExhaustedError, ProviderRateLimitError

    failing = _make_provider("mock", fail_with=ProviderRateLimitError("mock", "429"))

    import src.mapping.llm_client as client
    client._SESSION_PROVIDER = failing
    with patch.object(client, "PROVIDER_CASCADE", [failing]):
        with pytest.raises(AllProvidersExhaustedError):
            client.call_llm_with_cascade("sys", "user")


def test_call_before_pin_raises_runtime_error():
    """call_llm_with_cascade without pin → RuntimeError."""
    import src.mapping.llm_client as client
    client._SESSION_PROVIDER = None
    with pytest.raises(RuntimeError, match="pin_active_provider"):
        client.call_llm_with_cascade("sys", "user")


def test_pin_provider_raises_config_error_when_none_available():
    """No provider configured → ConfigError with setup instructions."""
    from src.mapping.exceptions import ConfigError

    import src.mapping.llm_client as client
    client._SESSION_PROVIDER = None
    with patch.object(client, "PROVIDER_CASCADE", [
        _make_provider("anthropic", available=False),
        _make_provider("openai", available=False),
        _make_provider("groq", available=False),
        _make_provider("ollama", available=False),
    ]):
        with pytest.raises(ConfigError):
            client.pin_active_provider()


def test_get_active_model_version_returns_pinned():
    import src.mapping.llm_client as client
    client._SESSION_PROVIDER = _make_provider("anthropic")
    client._SESSION_PROVIDER.model = "claude-sonnet-4-20250514"
    version = client.get_active_model_version()
    assert "anthropic" in version
    assert "claude" in version


def test_get_active_model_version_unknown_when_not_pinned():
    import src.mapping.llm_client as client
    client._SESSION_PROVIDER = None
    assert client.get_active_model_version() == "unknown/unknown"


def test_get_active_model_version_with_ocr_engine():
    """get_active_model_version(ocr_engine=...) returns combined LLM + OCR string."""
    import src.mapping.llm_client as client
    client._SESSION_PROVIDER = _make_provider("anthropic")
    client._SESSION_PROVIDER.model = "claude-sonnet-4-20250514"
    version = client.get_active_model_version(ocr_engine="tesseract-5.3")
    assert "+" in version
    assert "tesseract-5.3" in version
    assert "anthropic" in version


def test_get_active_model_version_no_ocr_engine_no_plus():
    """get_active_model_version() without ocr_engine returns simple version string."""
    import src.mapping.llm_client as client
    client._SESSION_PROVIDER = _make_provider("anthropic")
    client._SESSION_PROVIDER.model = "claude-sonnet-4-20250514"
    version = client.get_active_model_version()
    assert "+" not in version


def test_get_active_model_version_none_provider_with_ocr():
    """Handles None session provider gracefully even with ocr_engine."""
    import src.mapping.llm_client as client
    client._SESSION_PROVIDER = None
    version = client.get_active_model_version(ocr_engine="tesseract-5.3")
    assert "tesseract-5.3" in version
    assert "+" in version


def test_pinned_provider_tried_first_even_if_not_first_in_cascade():
    """Session-pinned provider is always tried first in attempt order."""
    anthropic_mock = _make_provider("anthropic")
    openai_mock = _make_provider("openai")

    import src.mapping.llm_client as client
    # Pin openai (not the first in cascade)
    client._SESSION_PROVIDER = openai_mock
    with patch.object(client, "PROVIDER_CASCADE", [anthropic_mock, openai_mock]):
        resp = client.call_llm_with_cascade("sys", "user")
    # openai.complete should be called (it's pinned)
    openai_mock.complete.assert_called_once()


# ── LLM smoke-check (fail-fast on a provider that can't produce output) ──────────

def test_smoke_check_passes_on_valid_json():
    import src.mapping.llm_client as client
    with patch.object(client, "call_llm_with_cascade",
                      return_value=make_llm_response('{"ok": true, "n": 26}')):
        ok, detail = client.smoke_check_llm()
    assert ok is True


def test_smoke_check_fails_on_empty_response():
    """Thinking-only model / context overrun → empty response → caught pre-run."""
    import src.mapping.llm_client as client
    with patch.object(client, "call_llm_with_cascade", return_value=make_llm_response("")):
        ok, detail = client.smoke_check_llm()
    assert ok is False and "EMPTY" in detail


def test_smoke_check_fails_on_unparseable_output():
    import src.mapping.llm_client as client
    with patch.object(client, "call_llm_with_cascade",
                      return_value=make_llm_response("I think it is 26")):
        ok, detail = client.smoke_check_llm()
    assert ok is False and "unparseable" in detail


def test_smoke_check_fails_when_providers_exhausted():
    """Dead/quota'd key → all providers exhausted → caught pre-run."""
    import src.mapping.llm_client as client
    from src.mapping.exceptions import AllProvidersExhaustedError
    with patch.object(client, "call_llm_with_cascade",
                      side_effect=AllProvidersExhaustedError("429 quota")):
        ok, detail = client.smoke_check_llm()
    assert ok is False and "failed" in detail
