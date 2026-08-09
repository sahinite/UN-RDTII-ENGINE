"""Focused UI-state tests for settings requirements and empty Results."""
from __future__ import annotations

from dataclasses import dataclass, field

from UI import results_screen, settings_screen
from UI.run_screen import run_pipeline_streaming
from src.queue.env_builder import PROVIDER_KEYS


@dataclass
class FakeCtx:
    email: str = "user@example.com"
    user_hash: str = "user-hash"
    secrets: dict[str, str] = field(default_factory=dict)
    config: dict = field(default_factory=lambda: {"LLM_PROVIDER": "openai"})


def test_settings_excludes_ollama_and_uses_two_unified_keys():
    provider_values = {value for _label, value in settings_screen.PROVIDER_OPTIONS}
    assert "ollama" not in provider_values
    assert "ollama_granite" not in provider_values
    assert [field[0] for field in settings_screen.PROVIDER_KEY_FIELDS] == [
        "LLM_API_KEY",
        "MISTRAL_API_KEY",
    ]


def test_provider_defaults_and_user_model_override():
    assert settings_screen.default_model_for_provider("openai") == "gpt-5"
    assert settings_screen.default_model_for_provider("gemini") == "gemini-2.5-flash"
    assert settings_screen.provider_model_update("anthropic")["value"] == (
        "claude-sonnet-4-20250514"
    )
    ctx = FakeCtx(config={"LLM_PROVIDER": "openai", "LLM_MODEL": "gpt-5-mini"})
    assert settings_screen.selected_model(ctx) == "gpt-5-mini"


def test_both_user_keys_are_required_before_run():
    assert "LLM API key and Mistral API key" in (
        settings_screen.missing_required_keys_message(FakeCtx()) or ""
    )
    assert "Mistral API key" in (
        settings_screen.missing_required_keys_message(
            FakeCtx(secrets={"LLM_API_KEY": "llm-key"})
        ) or ""
    )
    assert settings_screen.missing_required_keys_message(
        FakeCtx(secrets={"LLM_API_KEY": "llm-key", "MISTRAL_API_KEY": "ocr-key"})
    ) is None


def test_run_pipeline_returns_settings_message_when_keys_are_missing():
    payload = next(run_pipeline_streaming("Australia", 6, FakeCtx()))
    assert "My Settings" in payload[1]
    assert "LLM API key" in payload[1]
    assert "Mistral API key" in payload[1]


def test_save_settings_rejects_missing_required_keys(monkeypatch):
    ctx = FakeCtx()
    monkeypatch.setattr(
        settings_screen,
        "settings_values",
        lambda current_ctx, status="": (status,),
    )
    result = settings_screen.save_settings("", "openai", "gpt-5", "", "", ctx)
    assert result[0] is ctx
    assert result[1] == "Required: LLM API key and Mistral API key."


def test_empty_results_payload_shows_centered_state_and_hides_content():
    payload = results_screen.empty_results_payload()
    assert "rd-results-empty" in payload[8]["value"]
    assert payload[8]["visible"] is True
    assert payload[9]["visible"] is False


def test_results_cannot_load_without_authenticated_context():
    payload = results_screen.load_run("some-output.csv", None)
    assert "Sign in to view your runs" in payload[8]["value"]
    assert payload[9]["visible"] is False


def test_active_run_controls_are_hidden_when_there_are_no_runs():
    rows, panel = results_screen.active_runs_view(None)
    assert rows.empty
    assert panel["visible"] is False
    assert panel["open"] is False


def test_user_keys_are_removed_from_parent_environment():
    assert {"LLM_API_KEY", "MISTRAL_API_KEY"}.issubset(PROVIDER_KEYS)


def test_openai_runtime_default_is_gpt5():
    from src.mapping.providers.openai_provider import OPENAI_MODEL_DEFAULT

    assert OPENAI_MODEL_DEFAULT == "gpt-5"
