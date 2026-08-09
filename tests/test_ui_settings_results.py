"""Focused UI-state tests for settings requirements and empty Results."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from UI import results_screen, settings_screen
from UI.run_screen import latest_run_cost_html, run_pipeline_streaming
from src.queue.env_builder import PROVIDER_KEYS


@dataclass
class FakeCtx:
    email: str = "user@example.com"
    name: str = "Test User"
    picture_url: str = ""
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


def test_settings_restore_saved_keys_and_selection(monkeypatch):
    monkeypatch.setattr(settings_screen, "_current_contact", lambda _ctx: "+60 123")
    ctx = FakeCtx(
        secrets={"LLM_API_KEY": "saved-llm-key", "MISTRAL_API_KEY": "saved-ocr-key"},
        config={"LLM_PROVIDER": "gemini", "LLM_MODEL": "gemini-custom"},
    )

    values = settings_screen.settings_values(ctx)

    assert values[3] == "+60 123"
    assert values[4] == "gemini"
    assert values[5] == "gemini-custom"
    assert values[7:] == ("saved-llm-key", "saved-ocr-key")


def test_settings_profile_picture_uses_user_image_or_default():
    with_picture = settings_screen.render_profile_picture(
        FakeCtx(name="Profile User", picture_url="https://example.com/profile.png")
    )
    default_picture = settings_screen.render_profile_picture(FakeCtx())

    assert 'class="rd-settings-avatar"' in with_picture
    assert 'src="https://example.com/profile.png"' in with_picture
    assert "Profile User profile picture" in with_picture
    assert "data:image/svg+xml" in default_picture


def test_key_visibility_can_be_revealed_and_reset():
    shown = settings_screen.key_visibility_updates(True)
    hidden = settings_screen.reset_key_visibility()

    assert all(update["type"] == "text" for update in shown)
    assert hidden[0] is False
    assert all(update["type"] == "password" for update in hidden[1:])


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


def test_comparison_summary_has_aligned_metrics():
    summary = results_screen.render_comparison_summary("Malaysia", 6, 10, 24, 1)

    assert "rd-results-summary" in summary
    assert "Malaysia" in summary
    assert "10</strong> Round 1 indicators" in summary
    assert "24</strong> provision rows" in summary
    assert "1</strong> mismatch" in summary


def test_results_cannot_load_without_authenticated_context():
    payload = results_screen.load_run("some-output.csv", None)
    assert "Sign in to view your runs" in payload[8]["value"]
    assert payload[9]["visible"] is False


def test_loaded_result_hides_empty_state_and_shows_content(tmp_path, monkeypatch):
    csv_path = tmp_path / "Malaysia_P6_test.csv"
    pd.DataFrame([{"indicator_id": "6.1.1", "law_name": "Test Act"}]).to_csv(
        csv_path, index=False
    )
    monkeypatch.setattr(results_screen, "find_run_csv", lambda *_: csv_path)
    monkeypatch.setattr(
        results_screen,
        "build_round1_comparison",
        lambda *_: (pd.DataFrame(), pd.DataFrame(), "Loaded result"),
    )
    monkeypatch.setattr(results_screen, "run_report_path", lambda *_: tmp_path / "report.json")
    monkeypatch.setattr(results_screen, "cost_report_path", lambda *_: tmp_path / "cost.json")

    payload = results_screen.load_run(csv_path.name, FakeCtx())

    assert len(payload[0]) == 1
    assert payload[8]["visible"] is False
    assert payload[9]["visible"] is True


def test_run_cost_uses_latest_authenticated_user_run(tmp_path, monkeypatch):
    cost_path = tmp_path / "Malaysia_P6_latest_cost.json"
    cost_path.write_text(
        '{"economy":"Malaysia","pillar":6,"total_cost_usd":0.48,'
        '"components":{},"model_version":"gpt-5"}',
        encoding="utf-8",
    )
    monkeypatch.setattr("UI.run_screen.list_runs", lambda user_hash: ["Malaysia_P6_latest.csv"])
    monkeypatch.setattr("UI.run_screen.cost_report_path", lambda *_: cost_path)

    html = latest_run_cost_html(FakeCtx())

    assert "Malaysia" in html
    assert "$0.4800" in html


def test_run_cost_does_not_expose_shared_report_before_sign_in():
    html = latest_run_cost_html(None)

    assert "Sign in to view your latest run cost." in html
    assert "Australia" not in html


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
