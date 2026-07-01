"""
Unit tests for src/crawler/probe.py. [Z1-2-ST7]

All HTTP calls are mocked — zero real network requests.
Coverage target: ≥ 90% of crawler/probe.py.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from src.config.economy_config import EconomyConfig
from src.crawler.exceptions import ConfigError
from src.crawler.probe import (
    ProbeRawResult,
    ProbeResult,
    _aggregate_portal_results,
    _build_search_url,
    _is_js_rendered,
    _parse_search_page,
    _probe_portal_keyword,
    _probe_with_httpx,
    _rank_portals,
    _translate_keyword,
    translate_keywords,
    validate_taxonomy,
)

# ── Fixtures / helpers ─────────────────────────────────────────────────────────

_GOOD_HTML = Path(__file__).parent / "fixtures" / "search_result_page.html"

_GOOD_HTML_TEXT = _GOOD_HTML.read_text(encoding="utf-8")

_JS_SPA_HTML = "<html><body id='root'></body></html>"

_EMPTY_HTML = "<html><body></body></html>"


def _make_raw(portal_url: str, keyword: str, hit_count: int, status: str,
              result_urls: list[str] | None = None) -> ProbeRawResult:
    return ProbeRawResult(
        portal_url=portal_url,
        keyword=keyword,
        hit_count=hit_count,
        status=status,
        result_urls=result_urls or [],
    )


# ── 1. Taxonomy validation ────────────────────────────────────────────────────


def test_missing_keywords_raises_config_error(full_taxonomy):
    bad = [dict(ind) for ind in full_taxonomy]
    bad[3]["probe_keywords"] = []  # empty list for P6-I4
    with pytest.raises(ConfigError, match="P6-I4"):
        validate_taxonomy(bad)


def test_missing_probe_keywords_field_raises_config_error(full_taxonomy):
    bad = [dict(ind) for ind in full_taxonomy]
    del bad[0]["probe_keywords"]  # remove field entirely from P6-I1
    with pytest.raises(ConfigError, match="P6-I1"):
        validate_taxonomy(bad)


def test_valid_taxonomy_does_not_raise(full_taxonomy):
    validate_taxonomy(full_taxonomy)  # should not raise


# ── 2. JS detection ────────────────────────────────────────────────────────────


def test_js_rendered_detected_empty_body():
    assert _is_js_rendered(_EMPTY_HTML) is True


def test_js_rendered_detected_spa_root_id():
    assert _is_js_rendered(_JS_SPA_HTML) is True


def test_js_rendered_false_for_real_content():
    assert _is_js_rendered(_GOOD_HTML_TEXT) is False


# ── 3. Search URL construction ─────────────────────────────────────────────────


def test_build_search_url_default_pattern():
    url = _build_search_url("https://example.gov", "personal data", None)
    assert "personal+data" in url or "personal%20data" in url
    assert url.startswith("https://example.gov/search?q=")


def test_build_search_url_custom_pattern():
    pattern = "https://sso.agc.gov.sg/Search?SearchAct={keyword}"
    url = _build_search_url("https://sso.agc.gov.sg", "PDPA", pattern)
    assert url == "https://sso.agc.gov.sg/Search?SearchAct=PDPA"


def test_build_search_url_encodes_spaces():
    url = _build_search_url("https://example.gov", "cross border transfer", None)
    assert " " not in url


# ── 4. HTML parsing ────────────────────────────────────────────────────────────


def test_parse_search_page_extracts_count():
    count, urls = _parse_search_page(_GOOD_HTML_TEXT, "https://example.gov")
    assert count == 12  # "Found 12 results" in fixture HTML


def test_parse_search_page_extracts_urls():
    _, urls = _parse_search_page(_GOOD_HTML_TEXT, "https://example.gov")
    assert any("/acts/pdpa-2012" in u for u in urls)


def test_parse_search_page_no_count_falls_back_to_url_count():
    html = """
    <html><body>
    <a href="/act/a">Act A</a>
    <a href="/act/b">Act B</a>
    </body></html>
    """
    count, urls = _parse_search_page(html, "https://example.gov")
    assert count == len(urls)
    assert count == 2


# ── 5. Dispatcher routing ──────────────────────────────────────────────────────


@respx.mock
async def test_static_portal_uses_httpx(mocker):
    """Static portal returns good HTML → httpx succeeds, Playwright NOT called."""
    mock_playwright = mocker.patch(
        "src.crawler.probe._probe_with_playwright",
        new_callable=AsyncMock,
        return_value=(5, ["https://legislation.gov.au/act/a"], "ok"),
    )
    respx.get(url__startswith="https://legislation.gov.au").mock(
        return_value=httpx.Response(200, text=_GOOD_HTML_TEXT)
    )
    result = await _probe_portal_keyword(
        "https://legislation.gov.au", "personal data", None
    )
    assert result.status == "ok"
    mock_playwright.assert_not_called()


@respx.mock
async def test_js_portal_uses_playwright(mocker):
    """JS-rendered response from httpx → falls back to Playwright automatically."""
    mock_playwright = mocker.patch(
        "src.crawler.probe._probe_with_playwright",
        new_callable=AsyncMock,
        return_value=(8, ["https://sso.agc.gov.sg/acts/pdpa"], "ok"),
    )
    respx.get(url__startswith="https://sso.agc.gov.sg").mock(
        return_value=httpx.Response(200, text=_JS_SPA_HTML)
    )
    result = await _probe_portal_keyword(
        "https://sso.agc.gov.sg", "personal data protection", None
    )
    mock_playwright.assert_called_once()
    assert result.status == "ok"
    assert result.hit_count == 8


@respx.mock
async def test_http_403_skipped_gracefully():
    respx.get(url__startswith="https://blocked.gov").mock(
        return_value=httpx.Response(403)
    )
    result = await _probe_portal_keyword("https://blocked.gov", "data", None)
    assert result.status == "error"
    assert result.hit_count == 0


@respx.mock
async def test_http_429_skipped_gracefully():
    respx.get(url__startswith="https://ratelimited.gov").mock(
        return_value=httpx.Response(429)
    )
    result = await _probe_portal_keyword("https://ratelimited.gov", "data", None)
    assert result.status == "error"
    assert result.hit_count == 0


@respx.mock
async def test_timeout_skipped_gracefully():
    respx.get(url__startswith="https://slow.gov").mock(
        side_effect=httpx.TimeoutException("timed out")
    )
    result = await _probe_portal_keyword("https://slow.gov", "data", None)
    assert result.status == "timeout"
    assert result.hit_count == 0


# ── 6. Hit counting & ranking ─────────────────────────────────────────────────


def test_hit_count_aggregation():
    raw = [
        _make_raw("https://p.gov", "kw1", 5, "ok", ["https://p.gov/a", "https://p.gov/b"]),
        _make_raw("https://p.gov", "kw2", 3, "ok", ["https://p.gov/c", "https://p.gov/d"]),
    ]
    result = _aggregate_portal_results(raw, "P", "primary", "en")
    assert result.total_hit_count == 4  # 4 unique URLs
    assert result.is_active is True


def test_duplicate_result_urls_deduplicated():
    shared_url = "https://p.gov/pdpa"
    raw = [
        _make_raw("https://p.gov", "kw1", 1, "ok", [shared_url, "https://p.gov/act2"]),
        _make_raw("https://p.gov", "kw2", 1, "ok", [shared_url, "https://p.gov/act3"]),
    ]
    result = _aggregate_portal_results(raw, "P", "primary", "en")
    # shared_url counted once; unique total = 3
    assert result.total_hit_count == 3


def test_ranking_order():
    results = [
        ProbeResult("https://b.gov", "B", "primary", 3, True, "en", "ok"),
        ProbeResult("https://a.gov", "A", "primary", 10, True, "en", "ok"),
    ]
    ranked = _rank_portals(results)
    assert ranked[0].url == "https://a.gov"
    assert ranked[1].url == "https://b.gov"


def test_primary_portal_wins_tie():
    results = [
        ProbeResult("https://sec.gov", "Secondary", "secondary", 5, True, "en", "ok"),
        ProbeResult("https://pri.gov", "Primary", "primary", 5, True, "en", "ok"),
    ]
    ranked = _rank_portals(results)
    assert ranked[0].portal_type == "primary"
    assert ranked[0].url == "https://pri.gov"


def test_probe_status_ok_when_all_ok():
    raw = [
        _make_raw("https://p.gov", "kw1", 5, "ok"),
        _make_raw("https://p.gov", "kw2", 3, "ok"),
    ]
    result = _aggregate_portal_results(raw, "P", "primary", "en")
    assert result.probe_status == "ok"


def test_probe_status_partial_when_some_fail():
    raw = [
        _make_raw("https://p.gov", "kw1", 5, "ok"),
        _make_raw("https://p.gov", "kw2", 0, "timeout"),
    ]
    result = _aggregate_portal_results(raw, "P", "primary", "en")
    assert result.probe_status == "partial"


def test_probe_status_failed_when_all_fail():
    raw = [
        _make_raw("https://p.gov", "kw1", 0, "error"),
        _make_raw("https://p.gov", "kw2", 0, "timeout"),
    ]
    result = _aggregate_portal_results(raw, "P", "primary", "en")
    assert result.probe_status == "failed"
    assert result.is_active is False


# ── 7. Zero-result filtering ──────────────────────────────────────────────────


def test_zero_result_portal_excluded():
    results = [
        ProbeResult("https://a.gov", "A", "primary", 5, True, "en", "ok"),
        ProbeResult("https://z.gov", "Z", "secondary", 0, False, "en", "zero"),
    ]
    active = [r for r in results if r.is_active]
    assert len(active) == 1
    assert active[0].url == "https://a.gov"


def test_skip_log_written(tmp_path):
    """Zero-result portal should produce a probe_skip_*.jsonl log entry."""
    from src.crawler.probe import _write_probe_logs

    results = [
        ProbeResult("https://active.gov", "Active", "primary", 10, True, "en", "ok"),
        ProbeResult("https://inactive.gov", "Inactive", "secondary", 0, False, "en", "failed"),
    ]
    _write_probe_logs(results, "TestEconomy", str(tmp_path))

    skip_files = list(tmp_path.glob("probe_skip_*.jsonl"))
    assert len(skip_files) == 1
    lines = skip_files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["portal_url"] == "https://inactive.gov"
    assert entry["reason"] in ("zero_results", "all_probes_failed")
    assert entry["economy"] == "TestEconomy"


def test_summary_log_always_written(tmp_path):
    """probe_summary_*.json is written even when no portals are skipped."""
    from src.crawler.probe import _write_probe_logs

    results = [
        ProbeResult("https://active.gov", "Active", "primary", 10, True, "en", "ok"),
    ]
    _write_probe_logs(results, "SG", str(tmp_path))

    summary_files = list(tmp_path.glob("probe_summary_*.json"))
    assert len(summary_files) == 1
    summary = json.loads(summary_files[0].read_text(encoding="utf-8"))
    assert summary["active_portals"] == 1
    assert summary["skipped_portals"] == 0
    assert summary["economy"] == "SG"


# ── 8. Translation (Layer 1) ──────────────────────────────────────────────────


def test_translation_skipped_for_english(sg_economy, mocker):
    """English-only economy → zero DeepL or Google Translate calls."""
    mock_deepl_mod = MagicMock()
    mock_google_mod = MagicMock()
    mocker.patch.dict(sys.modules, {"deepl": mock_deepl_mod, "googletrans": mock_google_mod})

    keywords = ["personal data transfer", "data protection"]
    result = translate_keywords(keywords, sg_economy)

    assert result == keywords  # returned unchanged
    mock_deepl_mod.Translator.assert_not_called()
    mock_google_mod.Translator.assert_not_called()


def test_keywords_translated_for_bahasa(malaysia_economy, mocker):
    """Bahasa Malaysia economy → DeepL called for each keyword."""
    mock_translator = MagicMock()
    mock_translator.translate_text.return_value = MagicMock(text="pindahan data peribadi")
    mock_deepl_mod = MagicMock()
    mock_deepl_mod.Translator.return_value = mock_translator
    mocker.patch.dict(sys.modules, {"deepl": mock_deepl_mod})
    mocker.patch.dict(os.environ, {"DEEPL_API_KEY": "fake-key-for-test"})

    keywords = ["personal data transfer"]
    result = translate_keywords(keywords, malaysia_economy)

    mock_deepl_mod.Translator.assert_called_once()
    mock_translator.translate_text.assert_called_once()
    assert result == ["pindahan data peribadi"]


def test_deepl_fallback_to_google_translate(mocker):
    """DeepL raises → Google Translate called as fallback, no exception raised."""
    mock_deepl_translator = MagicMock()
    mock_deepl_translator.translate_text.side_effect = Exception("DeepL API unavailable")
    mock_deepl_mod = MagicMock()
    mock_deepl_mod.Translator.return_value = mock_deepl_translator
    mocker.patch.dict(sys.modules, {"deepl": mock_deepl_mod})
    mocker.patch.dict(os.environ, {"DEEPL_API_KEY": "fake-key"})

    mock_google_result = MagicMock(text="pemindahan data")
    mock_google_translator = MagicMock()
    mock_google_translator.translate.return_value = mock_google_result
    mock_google_mod = MagicMock()
    mock_google_mod.Translator.return_value = mock_google_translator
    mocker.patch.dict(sys.modules, {"googletrans": mock_google_mod})

    result = _translate_keyword("data transfer", "ms", "deepl")

    mock_google_translator.translate.assert_called_once_with("data transfer", dest="ms")
    assert result == "pemindahan data"


def test_translation_cache_hit_skips_api(mocker):
    """Same keyword+lang pair on second call hits cache — no API call."""
    mock_deepl_mod = MagicMock()
    mocker.patch.dict(sys.modules, {"deepl": mock_deepl_mod})
    mocker.patch.dict(os.environ, {"DEEPL_API_KEY": "fake-key"})

    from src.crawler.probe import _translation_memory
    _translation_memory["data transfer_ms"] = "pemindahan data"  # pre-populate cache

    result = _translate_keyword("data transfer", "ms", "deepl")

    assert result == "pemindahan data"
    mock_deepl_mod.Translator.assert_not_called()


# ── 9. Output contract (ST6) ──────────────────────────────────────────────────


def test_probe_result_fields_complete():
    """Every ProbeResult has all 7 required fields with correct types."""
    r = ProbeResult(
        url="https://example.gov",
        portal_name="Example",
        portal_type="primary",
        total_hit_count=5,
        is_active=True,
        language="en",
        probe_status="ok",
    )
    assert isinstance(r.url, str)
    assert isinstance(r.portal_name, str)
    assert r.portal_type in ("primary", "secondary")
    assert isinstance(r.total_hit_count, int)
    assert isinstance(r.is_active, bool)
    assert isinstance(r.language, str)
    assert r.probe_status in ("ok", "partial", "failed")
