"""
Tests for the Australia `api` discovery + `api_versioned_pdf` fetch strategy.
(D3/D4 — ADR-042/ADR-043; unified-portal-strategy.md step 5)

All network is mocked. Live-validated facts these fixtures mirror are recorded in
docs/prd/au_portal_findings.md.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import httpx
import respx

from src.config.economy_config import EconomyConfig, load_economy
from src.fetcher.models import FetchedDocument, Zone1Result

FRL = "https://www.legislation.gov.au"
API = "https://api.prod.legislation.gov.au/v1"


def _au_portal():
    return load_economy("australia").portals[0]


# ── Config parsing ───────────────────────────────────────────────────────────────

class TestAustraliaConfig:
    def test_australia_yaml_declares_api_strategy(self):
        p = _au_portal()
        assert p.anti_bot == "none"
        assert p.discovery == "api"
        assert p.fetch == "api_versioned_pdf"
        assert p.api_base == "https://api.prod.legislation.gov.au/v1"
        assert p.api_collection == "Act"
        assert p.pdf_path_suffix == "text/original/pdf"

    def test_api_strategy_literals_accepted(self):
        cfg = EconomyConfig.model_validate({
            "economy_name": "X", "iso_code": "XX", "un_name": "X",
            "script_type": "latin", "languages": ["en"],
            "portals": [{"name": "P", "url": "https://e.gov",
                         "discovery": "api", "fetch": "api_versioned_pdf"}],
        })
        assert cfg.portals[0].discovery == "api"


# ── api search-term derivation ────────────────────────────────────────────────────

class TestApiSearchTerms:
    def test_extracts_significant_words_drops_stopwords(self):
        from src.crawler.discover import _api_search_terms
        terms = _api_search_terms(["personal data protection", "the act and law", "privacy"])
        assert "personal" in terms and "data" in terms and "privacy" in terms
        assert "the" not in terms and "act" not in terms and "law" not in terms

    def test_caps_term_count(self):
        from src.crawler.discover import _api_search_terms
        kws = [f"keyword{i}longword" for i in range(30)]
        assert len(_api_search_terms(kws, cap=5)) == 5


# ── api discovery adapter ─────────────────────────────────────────────────────────

class TestDiscoverApi:
    def test_emits_candidates_deduped_by_title_id(self):
        from src.crawler.discover import _discover_api

        async def fake_query(client, api_base, collection, term, top=50):
            return {
                "privacy": [{"id": "C2004A03712", "name": "Privacy Act 1988"}],
                "data": [
                    {"id": "C2004A03712", "name": "Privacy Act 1988"},   # dup id
                    {"id": "C2022A00011", "name": "Data Availability and Transparency Act 2022"},
                ],
            }.get(term, [])

        tax = [{"indicator_id": "P7-I1", "probe_keywords": ["privacy", "data"]}]
        with patch("src.crawler.discover._api_titles_contains", side_effect=fake_query):
            cands = asyncio.run(_discover_api(_au_portal(), 7, tax, time.monotonic() + 30))

        urls = {u for _, u in cands}
        assert f"{FRL}/C2004A03712" in urls
        assert f"{FRL}/C2022A00011" in urls
        assert len(cands) == 2, "duplicate title id must appear once"

    def test_no_api_base_returns_none(self):
        from src.crawler.discover import _discover_api
        p = EconomyConfig.model_validate({
            "economy_name": "X", "iso_code": "XX", "un_name": "X",
            "script_type": "latin", "languages": ["en"],
            "portals": [{"name": "P", "url": "https://e.gov", "discovery": "api"}],
        }).portals[0]
        tax = [{"indicator_id": "P7-I1", "probe_keywords": ["privacy"]}]
        assert asyncio.run(_discover_api(p, 7, tax, time.monotonic() + 30)) is None

    def test_empty_results_returns_none(self):
        from src.crawler.discover import _discover_api

        async def empty(client, api_base, collection, term, top=50):
            return []

        tax = [{"indicator_id": "P7-I1", "probe_keywords": ["privacy"]}]
        with patch("src.crawler.discover._api_titles_contains", side_effect=empty):
            assert asyncio.run(_discover_api(_au_portal(), 7, tax, time.monotonic() + 30)) is None


# ── api_versioned_pdf fetch resolver ──────────────────────────────────────────────

class TestTitleIdExtraction:
    def test_extracts_from_various_url_forms(self):
        from src.fetcher.router import _extract_title_id
        assert _extract_title_id(f"{FRL}/C2004A03712") == "C2004A03712"
        assert _extract_title_id(f"{FRL}/c2004a03712/latest/text") == "C2004A03712"
        assert _extract_title_id(f"{FRL}/details/c2023c00106") == "C2023C00106"
        assert _extract_title_id(f"{FRL}/f2021l00289") == "F2021L00289"

    def test_returns_none_when_no_id(self):
        from src.fetcher.router import _extract_title_id
        assert _extract_title_id("https://www.oaic.gov.au/privacy/guidance") is None


class TestVersionResolution:
    @respx.mock
    def test_picks_islatest_row_skips_future(self):
        from src.fetcher.router import _latest_version_start
        respx.get(url__startswith=f"{API}/versions/search").mock(
            return_value=httpx.Response(200, json={"value": [
                {"start": "2026-12-10T00:00:00", "isLatest": False, "registerId": None},
                {"start": "2026-06-04T00:00:00", "isLatest": True, "registerId": "C2026C00227"},
                {"start": "2025-06-10T00:00:00", "isLatest": False, "registerId": "C2025C00378"},
            ]})
        )
        assert _latest_version_start(API, "C2004A03712") == "2026-06-04"

    @respx.mock
    def test_resolve_versioned_pdf_url_builds_dated_url(self):
        from src.fetcher import router
        respx.get(url__startswith=f"{API}/versions/search").mock(
            return_value=httpx.Response(200, json={"value": [
                {"start": "2026-06-04T00:00:00", "isLatest": True, "registerId": "C2026C00227"},
            ]})
        )
        with patch.object(router, "_url_serves_pdf", return_value=True):
            url = router._resolve_versioned_pdf_url(f"{FRL}/C2004A03712", _au_portal())
        assert url == f"{FRL}/C2004A03712/2026-06-04/2026-06-04/text/original/pdf"

    @respx.mock
    def test_resolve_falls_back_to_older_compilation_when_latest_pdf_404s(self):
        """FRL often hasn't generated the newest compilation's PDF (404) — walk older."""
        from src.fetcher import router
        respx.get(url__startswith=f"{API}/versions/search").mock(
            return_value=httpx.Response(200, json={"value": [
                {"start": "2026-06-04T00:00:00", "isLatest": True, "registerId": "C1"},
                {"start": "2025-04-04T00:00:00", "isLatest": False, "registerId": "C2"},
            ]})
        )
        # latest date's PDF is missing; the older compilation's exists.
        with patch.object(router, "_url_serves_pdf", side_effect=lambda u, timeout=30: "2025-04-04" in u):
            url = router._resolve_versioned_pdf_url(f"{FRL}/C2004A02124", _au_portal())
        assert url == f"{FRL}/C2004A02124/2025-04-04/2025-04-04/text/original/pdf"

    @respx.mock
    def test_resolve_returns_none_when_no_compilation_has_pdf(self):
        from src.fetcher import router
        respx.get(url__startswith=f"{API}/versions/search").mock(
            return_value=httpx.Response(200, json={"value": [
                {"start": "2026-06-04T00:00:00", "isLatest": True, "registerId": "C1"},
            ]})
        )
        with patch.object(router, "_url_serves_pdf", return_value=False):
            assert router._resolve_versioned_pdf_url(f"{FRL}/C2004A05145", _au_portal()) is None

    @respx.mock
    def test_resolve_returns_none_on_api_error(self):
        from src.fetcher.router import _resolve_versioned_pdf_url
        respx.get(url__startswith=f"{API}/versions/search").mock(return_value=httpx.Response(500))
        assert _resolve_versioned_pdf_url(f"{FRL}/C2004A03712", _au_portal()) is None


# ── Router dispatch for api_versioned_pdf ─────────────────────────────────────────

class TestRouterApiVersionedPdf:
    def test_route_resolves_dated_url_before_download(self):
        from src.fetcher import router

        zone1 = Zone1Result(
            url=f"{FRL}/C2004A03712", economy="AU", act_title="Privacy Act 1988",
            discovery_tag="KNOWN", archive_url="",
        )
        cfg = load_economy("australia")
        from pathlib import Path
        pdf_bytes = (Path(__file__).parent / "fixtures" / "z2_1" / "pdpa_sg_sample.pdf").read_bytes()
        downloaded = []

        def mock_download(url, timeout=30):
            downloaded.append(url)
            return pdf_bytes, "application/pdf", url

        with (
            patch.object(router, "_inforce_version_dates", return_value=["2026-06-04"]),
            patch.object(router, "_url_serves_pdf", return_value=True),
            patch.object(router, "download", side_effect=mock_download),
            patch.object(router, "is_consolidated_volume", return_value=False),
        ):
            result = router.route(zone1, cfg)

        assert downloaded, "download() should have been called"
        assert downloaded[0] == f"{FRL}/C2004A03712/2026-06-04/2026-06-04/text/original/pdf"
        assert isinstance(result, FetchedDocument)
