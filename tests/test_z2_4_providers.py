"""
Unit tests for Z2-4 ST1: LLM Provider Abstraction Layer. [Z2-4 ST7]
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


def test_anthropic_is_available_when_key_set():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
        from src.mapping.providers.anthropic_provider import AnthropicProvider
        assert AnthropicProvider().is_available() is True


def test_anthropic_not_available_when_key_missing():
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        from src.mapping.providers.anthropic_provider import AnthropicProvider
        assert AnthropicProvider().is_available() is False


def test_anthropic_complete_returns_llm_response():
    mock_msg = MagicMock()
    mock_msg.content = [MagicMock(text='{"found": false, "provisions": []}')]
    mock_msg.usage.input_tokens = 100
    mock_msg.usage.output_tokens = 10

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_msg

    # Clear LLM_MODEL so the model is the deterministic default, not whatever a
    # loaded .env set (which made this test order-dependent in the full suite).
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test", "LLM_MODEL": ""}):
        from src.mapping.providers.anthropic_provider import AnthropicProvider
        import src.mapping.providers.anthropic_provider as ap_module
        # Ensure anthropic module is not None for this test
        with patch.object(ap_module, "anthropic", MagicMock(Anthropic=MagicMock(return_value=mock_client))):
            p = AnthropicProvider()
            p._client = None
            resp = p.complete("system", "user")
        assert resp.provider == "anthropic"
        assert resp.model == "claude-sonnet-4-20250514"
        assert resp.input_tokens == 100
        assert resp.output_tokens == 10
        assert resp.cost_usd > 0


def test_anthropic_rate_limit_raises_provider_error():
    from src.mapping.exceptions import ProviderRateLimitError

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
        from src.mapping.providers.anthropic_provider import AnthropicProvider
        import src.mapping.providers.anthropic_provider as ap_module

        class FakeRateLimitError(Exception):
            pass
        class FakeAPIStatusError(Exception):
            pass
        class FakeConnectionError(Exception):
            pass

        fake_anthropic = MagicMock()
        fake_anthropic.RateLimitError = FakeRateLimitError
        fake_anthropic.APIStatusError = FakeAPIStatusError
        fake_anthropic.APIConnectionError = FakeConnectionError
        fake_anthropic.Anthropic.return_value.messages.create.side_effect = FakeRateLimitError("rate limited")

        with patch.object(ap_module, "anthropic", fake_anthropic):
            p = AnthropicProvider()
            p._client = None
            with pytest.raises(ProviderRateLimitError):
                p.complete("system", "user")


def test_anthropic_api_error_raises_provider_api_error():
    from src.mapping.exceptions import ProviderAPIError

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
        from src.mapping.providers.anthropic_provider import AnthropicProvider
        import src.mapping.providers.anthropic_provider as ap_module

        # Use distinct, non-overlapping exception types
        class FakeRateLimitError(Exception):
            pass
        class FakeAPIStatusError(Exception):
            pass
        class FakeConnectionError(Exception):
            pass

        fake_anthropic = MagicMock()
        fake_anthropic.RateLimitError = FakeRateLimitError
        fake_anthropic.APIStatusError = FakeAPIStatusError
        fake_anthropic.APIConnectionError = FakeConnectionError
        fake_anthropic.Anthropic.return_value.messages.create.side_effect = FakeAPIStatusError("server error")

        with patch.object(ap_module, "anthropic", fake_anthropic):
            p = AnthropicProvider()
            p._client = None
            with pytest.raises(ProviderAPIError):
                p.complete("system", "user")


def test_openai_is_available_when_key_set():
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
        from src.mapping.providers.openai_provider import OpenAIProvider
        assert OpenAIProvider().is_available() is True


def test_openai_not_available_when_key_missing():
    env = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    with patch.dict(os.environ, env, clear=True):
        from src.mapping.providers.openai_provider import OpenAIProvider
        assert OpenAIProvider().is_available() is False


def test_groq_is_available_when_key_set():
    with patch.dict(os.environ, {"GROQ_API_KEY": "gsk_test"}):
        from src.mapping.providers.groq_provider import GroqProvider
        assert GroqProvider().is_available() is True


def test_ollama_blocks_llama33():
    """Llama 3.3 must never appear in OLLAMA_MODELS."""
    from src.mapping.providers.ollama_provider import LLAMA33_BLOCKLIST, OLLAMA_MODELS

    for model_str in OLLAMA_MODELS.values():
        assert model_str not in LLAMA33_BLOCKLIST, (
            f"Model {model_str} is in Llama 3.3 blocklist!"
        )


def test_ollama_not_available_when_server_down():
    import requests as req
    with patch("src.mapping.providers.ollama_provider.requests.get", side_effect=req.ConnectionError("connection refused")):
        from src.mapping.providers.ollama_provider import OllamaProvider
        assert OllamaProvider(6).is_available() is False


def test_ollama_not_available_when_model_not_pulled():
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"models": [{"name": "llama2:7b"}]}
    with patch("src.mapping.providers.ollama_provider.requests.get", return_value=mock_resp):
        from src.mapping.providers.ollama_provider import OllamaProvider
        assert OllamaProvider(6).is_available() is False


def test_ollama_provider_names():
    from src.mapping.providers.ollama_provider import OllamaProvider
    # Clear LLM_MODEL so we assert the tier DEFAULTS, not a .env override.
    with patch.dict(os.environ, {"LLM_MODEL": ""}):
        assert OllamaProvider(6).provider_name == "ollama"
        assert OllamaProvider(7).provider_name == "ollama"
        assert "qwen" in OllamaProvider(6).model
        assert "granite" in OllamaProvider(7).model


def test_ollama_is_reasoning_model_detection():
    from src.mapping.providers.ollama_provider import _is_reasoning_model
    assert _is_reasoning_model("deepseek-r1:8b")
    assert _is_reasoning_model("qwq:32b")
    assert not _is_reasoning_model("granite3-8b")
    assert not _is_reasoning_model("qwen2.5:7b")


def test_ollama_reasoning_model_gets_num_predict_headroom():
    """Reasoning models need extra num_predict so <think> doesn't starve the JSON
    answer; non-reasoning models keep the caller's budget unchanged."""
    from src.mapping.providers.ollama_provider import OllamaProvider, _REASONING_THINK_HEADROOM
    captured = {}

    def _fake_post(url, json, timeout):
        captured["payload"] = json
        m = MagicMock()
        m.raise_for_status = lambda: None
        m.json.return_value = {"response": '{"found": false, "provisions": []}',
                               "prompt_eval_count": 1, "eval_count": 1}
        return m

    with patch("src.mapping.providers.ollama_provider.requests.post", side_effect=_fake_post):
        p = OllamaProvider(6)
        p._model = "deepseek-r1:8b"          # reasoning
        p.complete("s", "u", max_tokens=1000)
        assert captured["payload"]["options"]["num_predict"] == 1000 + _REASONING_THINK_HEADROOM
        p._model = "granite3-8b"             # non-reasoning
        p.complete("s", "u", max_tokens=1000)
        assert captured["payload"]["options"]["num_predict"] == 1000


def test_anthropic_provider_name_and_model():
    from src.mapping.providers.anthropic_provider import ANTHROPIC_MODEL_DEFAULT, AnthropicProvider
    # Clear LLM_MODEL so we test the provider's default, not an env override
    # (a loaded .env can pin LLM_MODEL to a non-Claude model).
    with patch.dict(os.environ, {"LLM_MODEL": ""}):
        p = AnthropicProvider()
        assert p.provider_name == "anthropic"
        assert p.model == ANTHROPIC_MODEL_DEFAULT
    assert "claude" in ANTHROPIC_MODEL_DEFAULT
