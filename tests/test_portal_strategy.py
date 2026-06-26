"""
Tests for the pillar-agnostic per-economy portal strategy. [PRD: 86ey22k30]

Seams tested (no network — all fixtures/mocks):
  1. Strategy config parsing      — Portal model new fields + load_economy
  2. Transport ladder             — transport.fetch() rung escalation
  3. Pillar scoping               — build_pillar_keywords()
  4. Index discovery + ranking    — _parse_index_links + _rank_by_keywords + discover()
  5. pdf_endpoint routing         — router.route() URL rewrite
  6. Graceful degradation         — discover() on TBD / timeout → seed KNOWN URLs
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config.economy_config import EconomyConfig, Portal
from src.fetcher.models import Zone1Result

FIXTURES = Path(__file__).parent / "fixtures"

# ══════════════════════════════════════════════════════════════════════════════
# 1. Strategy config parsing
# ══════════════════════════════════════════════════════════════════════════════

_SG_MINIMAL = {
    "economy_name": "Singapore",
    "iso_code": "SG",
    "un_name": "Singapore",
    "script_type": "latin",
    "languages": ["en"],
    "portals": [{"name": "SSO", "url": "https://sso.agc.gov.sg"}],
}


class TestPortalStrategyFields:
    def test_new_fields_have_correct_defaults(self):
        cfg = EconomyConfig.model_validate(_SG_MINIMAL)
        portal = cfg.portals[0]
        assert portal.anti_bot == "none"
        assert portal.discovery == "TBD"
        assert portal.fetch == "TBD"
        assert portal.index_urls == []
        assert portal.pdf_view_suffix is None
        assert portal.transport_fallback is None

    def test_anti_bot_header_spoof_accepted(self):
        data = {**_SG_MINIMAL, "portals": [{
            "name": "SSO", "url": "https://sso.agc.gov.sg",
            "anti_bot": "header_spoof",
        }]}
        cfg = EconomyConfig.model_validate(data)
        assert cfg.portals[0].anti_bot == "header_spoof"

    def test_anti_bot_playwright_stealth_accepted(self):
        data = {**_SG_MINIMAL, "portals": [{
            "name": "SSO", "url": "https://sso.agc.gov.sg",
            "anti_bot": "playwright_stealth",
        }]}
        cfg = EconomyConfig.model_validate(data)
        assert cfg.portals[0].anti_bot == "playwright_stealth"

    def test_anti_bot_invalid_rejected(self):
        from pydantic import ValidationError
        data = {**_SG_MINIMAL, "portals": [{
            "name": "SSO", "url": "https://sso.agc.gov.sg",
            "anti_bot": "rotate_ips",
        }]}
        with pytest.raises(ValidationError):
            EconomyConfig.model_validate(data)

    def test_discovery_index_accepted(self):
        data = {**_SG_MINIMAL, "portals": [{
            "name": "SSO", "url": "https://sso.agc.gov.sg",
            "discovery": "index",
            "index_urls": ["https://sso.agc.gov.sg/Browse/Act/Current/All"],
        }]}
        cfg = EconomyConfig.model_validate(data)
        assert cfg.portals[0].discovery == "index"
        assert len(cfg.portals[0].index_urls) == 1

    def test_discovery_tbd_accepted(self):
        data = {**_SG_MINIMAL, "portals": [{
            "name": "Gazette", "url": "https://www.egazette.gov.sg",
            "discovery": "TBD",
        }]}
        cfg = EconomyConfig.model_validate(data)
        assert cfg.portals[0].discovery == "TBD"

    def test_discovery_invalid_rejected(self):
        from pydantic import ValidationError
        data = {**_SG_MINIMAL, "portals": [{
            "name": "SSO", "url": "https://sso.agc.gov.sg",
            "discovery": "scrape_all",
        }]}
        with pytest.raises(ValidationError):
            EconomyConfig.model_validate(data)

    def test_fetch_pdf_endpoint_accepted(self):
        data = {**_SG_MINIMAL, "portals": [{
            "name": "SSO", "url": "https://sso.agc.gov.sg",
            "fetch": "pdf_endpoint",
            "pdf_view_suffix": "?ViewType=Pdf",
        }]}
        cfg = EconomyConfig.model_validate(data)
        assert cfg.portals[0].fetch == "pdf_endpoint"
        assert cfg.portals[0].pdf_view_suffix == "?ViewType=Pdf"

    def test_transport_fallback_playwright_stealth_accepted(self):
        data = {**_SG_MINIMAL, "portals": [{
            "name": "SSO", "url": "https://sso.agc.gov.sg",
            "transport_fallback": "playwright_stealth",
        }]}
        cfg = EconomyConfig.model_validate(data)
        assert cfg.portals[0].transport_fallback == "playwright_stealth"

    def test_singapore_yaml_loads_with_strategy_fields(self):
        from src.config.economy_config import load_economy
        cfg = load_economy("singapore")
        sso = cfg.portals[0]
        assert sso.anti_bot == "header_spoof"
        assert sso.discovery == "index"
        assert sso.fetch == "pdf_endpoint"
        assert len(sso.index_urls) >= 2
        assert sso.pdf_view_suffix == "?ViewType=Pdf"
        assert sso.transport_fallback == "playwright_stealth"

    def test_singapore_gazette_has_tbd_strategy(self):
        from src.config.economy_config import load_economy
        cfg = load_economy("singapore")
        gazette = cfg.portals[1]
        assert gazette.discovery == "TBD"
        assert gazette.fetch == "TBD"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Transport ladder
# ══════════════════════════════════════════════════════════════════════════════

def _make_portal(anti_bot: str = "none", transport_fallback: str | None = None) -> Portal:
    return Portal(
        name="Test Portal",
        url="https://sso.agc.gov.sg",
        anti_bot=anti_bot,  # type: ignore[arg-type]
        transport_fallback=transport_fallback,  # type: ignore[arg-type]
    )


def _run(coro):
    return asyncio.run(coro)


class TestTransportLadder:
    def test_plain_403_then_headers_200_stops_at_rung2(self):
        """Rung 1 returns 403; rung 2 returns real 200 — Playwright never called."""
        from src.crawler import transport

        real_html = "<html><body>" + "x" * 300 + "</body></html>"

        async def mock_fetch_plain(url):
            return "", 403

        async def mock_fetch_headers(url):
            return real_html, 200

        with (
            patch.object(transport, "_fetch_plain", side_effect=mock_fetch_plain),
            patch.object(transport, "_fetch_with_headers", side_effect=mock_fetch_headers),
            patch.object(transport, "_fetch_playwright", side_effect=AssertionError("Playwright must not be called")),
        ):
            portal = _make_portal(anti_bot="header_spoof", transport_fallback="playwright_stealth")
            html, status = _run(transport.fetch("https://sso.agc.gov.sg/Browse", portal))

        assert status == 200
        assert len(html) > 200

    def test_plain_200_stops_at_rung1(self):
        """Rung 1 returns real 200 — no further escalation."""
        from src.crawler import transport

        real_html = "<html><body>" + "x" * 300 + "</body></html>"

        async def mock_fetch_plain(url):
            return real_html, 200

        with (
            patch.object(transport, "_fetch_plain", side_effect=mock_fetch_plain),
            patch.object(transport, "_fetch_with_headers", side_effect=AssertionError("Rung 2 must not be called")),
        ):
            portal = _make_portal(anti_bot="none")
            html, status = _run(transport.fetch("https://example.gov.sg", portal))

        assert status == 200

    def test_js_shell_escalates_to_next_rung(self):
        """Rung 1 returns a JS shell (< 200 chars body) → rung 2 is tried."""
        from src.crawler import transport

        js_shell = "<html><body><div id='root'></div></body></html>"
        real_html = "<html><body>" + "x" * 300 + "</body></html>"

        rung2_called = []

        async def mock_fetch_plain(url):
            return js_shell, 200

        async def mock_fetch_headers(url):
            rung2_called.append(True)
            return real_html, 200

        with (
            patch.object(transport, "_fetch_plain", side_effect=mock_fetch_plain),
            patch.object(transport, "_fetch_with_headers", side_effect=mock_fetch_headers),
        ):
            portal = _make_portal(anti_bot="none")
            html, status = _run(transport.fetch("https://example.gov.sg", portal))

        assert rung2_called, "Rung 2 should have been called on JS-shell response"
        assert status == 200

    def test_no_transport_fallback_never_calls_playwright(self):
        """When transport_fallback is None, Playwright is never invoked."""
        from src.crawler import transport

        async def always_fails(url):
            return "", 403

        with (
            patch.object(transport, "_fetch_plain", side_effect=always_fails),
            patch.object(transport, "_fetch_with_headers", side_effect=always_fails),
            patch.object(transport, "_fetch_playwright", side_effect=AssertionError("Playwright must not be called")),
        ):
            portal = _make_portal(anti_bot="none", transport_fallback=None)
            html, status = _run(transport.fetch("https://sso.agc.gov.sg", portal))

        assert html == ""
        assert status == 0

    def test_all_rungs_fail_returns_empty(self):
        """When every rung fails, (empty, 0) is returned without raising."""
        from src.crawler import transport

        async def always_fails(url):
            return "", 403

        async def playwright_fails(url):
            return "", 0

        with (
            patch.object(transport, "_fetch_plain", side_effect=always_fails),
            patch.object(transport, "_fetch_with_headers", side_effect=always_fails),
            patch.object(transport, "_fetch_playwright", side_effect=playwright_fails),
        ):
            portal = _make_portal(anti_bot="header_spoof", transport_fallback="playwright_stealth")
            html, status = _run(transport.fetch("https://sso.agc.gov.sg", portal))

        assert html == ""
        assert status == 0


# ══════════════════════════════════════════════════════════════════════════════
# 3. Pillar scoping
# ══════════════════════════════════════════════════════════════════════════════

_MINI_TAXONOMY = [
    {"indicator_id": "P6-I1", "probe_keywords": ["cross-border transfer", "data localisation"]},
    {"indicator_id": "P6-I2", "probe_keywords": ["adequacy decision", "data flow"]},
    {"indicator_id": "P7-I1", "probe_keywords": ["personal data protection", "consent"]},
    {"indicator_id": "P7-I2", "probe_keywords": ["data breach", "notification"]},
    {"indicator_id": "P7-I3", "probe_keywords": ["data subject rights", "access request"]},
]


class TestPillarScoping:
    def test_pillar7_returns_only_p7_keywords(self):
        from src.crawler.discover import build_pillar_keywords
        kws = build_pillar_keywords(_MINI_TAXONOMY, 7)
        assert "personal data protection" in kws
        assert "consent" in kws
        assert "data breach" in kws
        assert "cross-border transfer" not in kws
        assert "adequacy decision" not in kws

    def test_pillar6_returns_only_p6_keywords(self):
        from src.crawler.discover import build_pillar_keywords
        kws = build_pillar_keywords(_MINI_TAXONOMY, 6)
        assert "cross-border transfer" in kws
        assert "data localisation" in kws
        assert "personal data protection" not in kws

    def test_keywords_deduplicated(self):
        """No duplicate keywords even if multiple indicators share a term."""
        taxonomy = [
            {"indicator_id": "P7-I1", "probe_keywords": ["consent", "personal data"]},
            {"indicator_id": "P7-I2", "probe_keywords": ["consent", "breach"]},
        ]
        from src.crawler.discover import build_pillar_keywords
        kws = build_pillar_keywords(taxonomy, 7)
        assert kws.count("consent") == 1

    def test_unknown_pillar_returns_empty(self):
        from src.crawler.discover import build_pillar_keywords
        kws = build_pillar_keywords(_MINI_TAXONOMY, 99)
        assert kws == []


# ══════════════════════════════════════════════════════════════════════════════
# 4. Index discovery + ranking + cap
# ══════════════════════════════════════════════════════════════════════════════

_SSO_INDEX_HTML = (FIXTURES / "sso_browse_index.html").read_text(encoding="utf-8")

_SG_ECONOMY = EconomyConfig.model_validate({
    "economy_name": "Singapore",
    "iso_code": "SG",
    "un_name": "Singapore",
    "script_type": "latin",
    "languages": ["en"],
    "portals": [
        {
            "name": "Singapore Statutes Online",
            "url": "https://sso.agc.gov.sg",
            "type": "primary",
            "anti_bot": "header_spoof",
            "discovery": "index",
            "index_urls": ["https://sso.agc.gov.sg/Browse/Act/Current/All?PageSize=500"],
            "fetch": "pdf_endpoint",
            "pdf_view_suffix": "?ViewType=Pdf",
            "transport_fallback": "playwright_stealth",
        },
        {
            "name": "Singapore Government Gazette",
            "url": "https://www.egazette.gov.sg",
            "type": "secondary",
            "discovery": "TBD",
            "fetch": "TBD",
        },
    ],
})


class TestIndexDiscovery:
    def test_parse_index_links_extracts_act_and_sl_hrefs(self):
        from src.crawler.discover import _parse_index_links
        links = _parse_index_links(_SSO_INDEX_HTML, "https://sso.agc.gov.sg")
        urls = [u for _, u in links]
        assert any("/Act/PDPA2012" in u for u in urls)
        assert any("/SL/PDPA2012-S362" in u for u in urls)
        # Nav links should not be included
        assert all("sso.agc.gov.sg" in u for u in urls), "Off-domain links should be filtered"
        assert not any(u.endswith("/Home") for u in urls)
        assert not any("agc.gov.sg" in u and "/Act/" not in u and "/SL/" not in u for u in urls)

    def test_parse_index_links_returns_titles(self):
        from src.crawler.discover import _parse_index_links
        links = _parse_index_links(_SSO_INDEX_HTML, "https://sso.agc.gov.sg")
        titles = [t for t, _ in links]
        assert any("Personal Data Protection Act" in t for t in titles)

    def test_parse_index_links_deduplicates(self):
        """Duplicate hrefs in the HTML should only appear once."""
        html = """
        <html><body>
          <a href="/Act/PDPA2012">Personal Data Protection Act 2012</a>
          <a href="/Act/PDPA2012">Personal Data Protection Act 2012</a>
        </body></html>
        """
        from src.crawler.discover import _parse_index_links
        links = _parse_index_links(html, "https://sso.agc.gov.sg")
        pdpa_links = [u for _, u in links if "PDPA2012" in u]
        assert len(pdpa_links) == 1

    def test_rank_by_keywords_known_high_relevance(self):
        """PDPA should score high against personal-data keywords."""
        from src.crawler.discover import _rank_by_keywords
        candidates = [
            ("Personal Data Protection Act 2012", "https://sso.agc.gov.sg/Act/PDPA2012"),
            ("Banking Act", "https://sso.agc.gov.sg/Act/BCA2004"),
            ("Copyright Act", "https://sso.agc.gov.sg/Act/CP1985"),
        ]
        keywords = ["personal data protection", "consent", "data breach"]
        scored = _rank_by_keywords(candidates, keywords)
        top_title = scored[0][1]  # (score, title, url)
        assert "Personal Data Protection" in top_title

    def test_discover_known_seeds_always_included(self):
        """KNOWN seed URLs are always in discover() output, never dropped."""
        from src.crawler.discover import discover

        pdpa_url = "https://sso.agc.gov.sg/Act/PDPA2012"
        known_urls = {pdpa_url}

        async def mock_fetch(url, portal):
            return _SSO_INDEX_HTML, 200

        with patch("src.crawler.discover.transport_fetch", side_effect=mock_fetch):
            results = asyncio.run(
                discover(_SG_ECONOMY, 7, _MINI_TAXONOMY, known_urls)
            )

        result_urls = [z.url for z in results]
        assert any("PDPA2012" in u for u in result_urls), "KNOWN PDPA URL must be in results"
        known_results = [z for z in results if z.discovery_tag == "KNOWN"]
        assert any("PDPA2012" in z.url for z in known_results)

    def test_discover_new_above_threshold_included(self):
        """NEW acts with BM25 score above threshold should be included."""
        from src.crawler.discover import discover

        known_urls: set[str] = set()

        async def mock_fetch(url, portal):
            return _SSO_INDEX_HTML, 200

        with patch("src.crawler.discover.transport_fetch", side_effect=mock_fetch):
            results = asyncio.run(
                discover(_SG_ECONOMY, 7, _MINI_TAXONOMY, known_urls)
            )

        new_results = [z for z in results if z.discovery_tag == "NEW"]
        # At least one relevant act (e.g. PDPA, Cybersecurity) should appear as NEW
        assert len(new_results) >= 1

    def test_discover_capped_at_zone2_max_acts(self):
        """discover() must never return more than ZONE2_MAX_ACTS results."""
        from src.crawler.discover import discover

        known_urls: set[str] = set()

        async def mock_fetch(url, portal):
            # Simulate a huge index page with many data-protection-related acts
            big_html = "<html><body>" + "".join(
                f'<a href="/Act/Act{i}">Data Protection Personal Privacy Act {i}</a>'
                for i in range(100)
            ) + "</body></html>"
            return big_html, 200

        with (
            patch("src.crawler.discover.transport_fetch", side_effect=mock_fetch),
            patch("src.crawler.discover.ZONE2_MAX_ACTS", 5),
        ):
            results = asyncio.run(
                discover(_SG_ECONOMY, 7, _MINI_TAXONOMY, known_urls)
            )

        assert len(results) <= 5

    def test_discover_weak_new_acts_dropped(self):
        """Acts with BM25 score below threshold should NOT appear in NEW results."""
        from src.crawler.discover import discover

        # Only completely unrelated acts in the index
        unrelated_html = """
        <html><body>
          <a href="/Act/ROAD">Road Traffic Act</a>
          <a href="/Act/FIRE">Fire Safety Act</a>
        </body></html>
        """
        known_urls: set[str] = set()

        async def mock_fetch(url, portal):
            return unrelated_html, 200

        with patch("src.crawler.discover.transport_fetch", side_effect=mock_fetch):
            results = asyncio.run(
                discover(_SG_ECONOMY, 7, _MINI_TAXONOMY, known_urls)
            )

        # Unrelated acts should either be absent or have a very low score and be dropped
        result_titles = [z.act_title for z in results]
        # These should NOT appear as they're below relevance threshold for P7 keywords
        assert not any("Road Traffic" in t for t in result_titles), \
            "Road Traffic Act is not relevant to P7 and should be dropped"

    def test_discover_tags_known_by_title_when_no_seed_url(self):
        """Round 1 seed often has titles but no act URL → match by title → KNOWN."""
        from src.crawler.discover import discover
        from src.crawler.seed_loader import normalise_title

        # No known URLs at all — only a known title (the PDPA), as in SG P7.
        known_urls: set[str] = set()
        known_titles = {normalise_title("Personal Data Protection Act 2012")}

        async def mock_fetch(url, portal):
            return _SSO_INDEX_HTML, 200

        with patch("src.crawler.discover.transport_fetch", side_effect=mock_fetch):
            results = asyncio.run(
                discover(_SG_ECONOMY, 7, _MINI_TAXONOMY, known_urls, known_titles=known_titles)
            )

        pdpa = [z for z in results if "PDPA2012" in z.url]
        assert pdpa, "PDPA must be discovered from the index"
        assert pdpa[0].discovery_tag == "KNOWN", \
            "PDPA matched by title must be tagged KNOWN, not NEW"

    def test_discover_excludes_taxonomy_excluded_titles(self):
        """Acts whose title matches exclude_act_titles are dropped (never NEW)."""
        from src.crawler.discover import discover

        # Taxonomy where P7 excludes the Banking Act (present in the fixture).
        taxonomy = [
            {
                "indicator_id": "P7-I1",
                "probe_keywords": ["personal data protection", "banking"],
                "exclude_act_titles": ["banking act"],
            },
        ]
        known_urls: set[str] = set()

        async def mock_fetch(url, portal):
            return _SSO_INDEX_HTML, 200

        with patch("src.crawler.discover.transport_fetch", side_effect=mock_fetch):
            results = asyncio.run(
                discover(_SG_ECONOMY, 7, taxonomy, known_urls)
            )

        titles = [z.act_title for z in results]
        assert not any("Banking Act" in t for t in titles), \
            "Banking Act is in exclude_act_titles and must be dropped"

    def test_discover_known_seed_bypasses_exclude(self):
        """A Round 1 KNOWN seed act is ground truth and must be kept even when it
        matches a pillar's exclude list (Companies/Income Tax/Banking carry real
        storage/retention/secrecy provisions). Exclusion applies to NEW acts only.
        """
        from src.crawler.discover import discover
        from src.crawler.seed_loader import normalise_title

        taxonomy = [{
            "indicator_id": "P7-I1",
            "probe_keywords": ["personal data protection"],
            "exclude_act_titles": ["banking act"],
        }]
        # Banking Act is BOTH in the exclude list AND a known seed act.
        known_titles = {normalise_title("Banking Act 1970")}

        async def mock_fetch(url, portal):
            return _SSO_INDEX_HTML, 200

        with patch("src.crawler.discover.transport_fetch", side_effect=mock_fetch):
            results = asyncio.run(
                discover(_SG_ECONOMY, 7, taxonomy, set(), known_titles=known_titles)
            )

        banking = [z for z in results if "Banking Act" in z.act_title]
        assert banking, "KNOWN seed act must be kept despite being in the exclude list"
        assert banking[0].discovery_tag == "KNOWN"


# ══════════════════════════════════════════════════════════════════════════════
# 5. pdf_endpoint routing
# ══════════════════════════════════════════════════════════════════════════════

class TestPdfEndpointRouting:
    def _sg_config_with_pdf_endpoint(self) -> EconomyConfig:
        return EconomyConfig.model_validate({
            "economy_name": "Singapore",
            "iso_code": "SG",
            "un_name": "Singapore",
            "script_type": "latin",
            "languages": ["en"],
            "portals": [{
                "name": "SSO",
                "url": "https://sso.agc.gov.sg",
                "type": "primary",
                "fetch": "pdf_endpoint",
                "pdf_view_suffix": "?ViewType=Pdf",
            }],
        })

    def _sg_config_no_pdf_endpoint(self) -> EconomyConfig:
        return EconomyConfig.model_validate({
            "economy_name": "Singapore",
            "iso_code": "SG",
            "un_name": "Singapore",
            "script_type": "latin",
            "languages": ["en"],
            "portals": [{"name": "SSO", "url": "https://sso.agc.gov.sg"}],
        })

    def test_rewrite_to_pdf_url_appends_suffix(self):
        from src.fetcher.router import _rewrite_to_pdf_url
        result = _rewrite_to_pdf_url("https://sso.agc.gov.sg/Act/PDPA2012", "?ViewType=Pdf")
        assert result == "https://sso.agc.gov.sg/Act/PDPA2012?ViewType=Pdf"

    def test_rewrite_preserves_existing_query_params(self):
        from src.fetcher.router import _rewrite_to_pdf_url
        result = _rewrite_to_pdf_url("https://sso.agc.gov.sg/Act/PDPA2012?Lang=en", "?ViewType=Pdf")
        assert "ViewType=Pdf" in result
        assert "Lang=en" in result

    def test_route_pdf_endpoint_rewrites_url_before_download(self, tmp_path):
        """route() rewrites act URL to PDF view when fetch: pdf_endpoint."""
        from src.fetcher import router
        from src.fetcher.models import CostLogEntry, FetchedDocument

        zone1 = Zone1Result(
            url="https://sso.agc.gov.sg/Act/PDPA2012",
            economy="SG",
            act_title="Personal Data Protection Act 2012",
            discovery_tag="KNOWN",
            archive_url="",
        )
        sg_config = self._sg_config_with_pdf_endpoint()

        pdf_bytes = (FIXTURES / "z2_1" / "pdpa_sg_sample.pdf").read_bytes()
        downloaded_urls = []

        def mock_download(url, timeout=30):
            downloaded_urls.append(url)
            return pdf_bytes, "application/pdf", url

        with (
            patch.object(router, "download", side_effect=mock_download),
            patch.object(router, "is_consolidated_volume", return_value=False),
        ):
            result = router.route(zone1, sg_config)

        assert downloaded_urls, "download() should have been called"
        assert "ViewType=Pdf" in downloaded_urls[0], \
            f"Expected ?ViewType=Pdf in downloaded URL, got: {downloaded_urls[0]}"
        assert isinstance(result, FetchedDocument)

    def test_pdf_endpoint_skips_consolidated_volume_segmentation(self):
        """A single act fetched via pdf_endpoint must NOT be segmented as a volume."""
        from src.fetcher import router
        from src.fetcher.models import FetchedDocument

        zone1 = Zone1Result(
            url="https://sso.agc.gov.sg/Act/PDPA2012",
            economy="SG",
            act_title="Personal Data Protection Act 2012",
            discovery_tag="KNOWN",
            archive_url="",
        )
        sg_config = self._sg_config_with_pdf_endpoint()
        pdf_bytes = (FIXTURES / "z2_1" / "pdpa_sg_sample.pdf").read_bytes()

        def mock_download(url, timeout=30):
            return pdf_bytes, "application/pdf", url

        with (
            patch.object(router, "download", side_effect=mock_download),
            # Even if detection WOULD say "volume", pdf_endpoint must bypass it.
            patch.object(router, "is_consolidated_volume", return_value=True),
            patch.object(router, "segment_volume",
                         side_effect=AssertionError("segmentation must be skipped for pdf_endpoint")),
        ):
            result = router.route(zone1, sg_config)

        assert isinstance(result, FetchedDocument), \
            "Single-act pdf_endpoint fetch must return one document, not a segmented list"

    def test_route_no_pdf_endpoint_uses_original_url(self):
        """Without fetch:pdf_endpoint, the URL is not rewritten."""
        from src.fetcher import router
        from src.fetcher.models import FetchedDocument

        zone1 = Zone1Result(
            url="https://sso.agc.gov.sg/Act/PDPA2012",
            economy="SG",
            act_title="Personal Data Protection Act 2012",
            discovery_tag="KNOWN",
            archive_url="",
        )
        sg_config = self._sg_config_no_pdf_endpoint()

        pdf_bytes = (FIXTURES / "z2_1" / "pdpa_sg_sample.pdf").read_bytes()
        downloaded_urls = []

        def mock_download(url, timeout=30):
            downloaded_urls.append(url)
            return pdf_bytes, "application/pdf", url

        with (
            patch.object(router, "download", side_effect=mock_download),
            patch.object(router, "is_consolidated_volume", return_value=False),
        ):
            router.route(zone1, sg_config)

        assert "ViewType=Pdf" not in downloaded_urls[0], \
            "URL should NOT be rewritten when fetch strategy is not pdf_endpoint"


# ══════════════════════════════════════════════════════════════════════════════
# 6. Graceful degradation
# ══════════════════════════════════════════════════════════════════════════════

class TestGracefulDegradation:
    def test_tbd_portal_returns_seed_known_urls(self):
        """A portal with discovery:TBD is skipped; seed KNOWN URLs are returned."""
        from src.crawler.discover import discover

        tbd_economy = EconomyConfig.model_validate({
            "economy_name": "Singapore",
            "iso_code": "SG",
            "un_name": "Singapore",
            "script_type": "latin",
            "languages": ["en"],
            "portals": [{
                "name": "Gazette",
                "url": "https://www.egazette.gov.sg",
                "discovery": "TBD",
            }],
        })
        known_urls = {"https://sso.agc.gov.sg/Act/PDPA2012"}

        results = asyncio.run(
            discover(tbd_economy, 7, _MINI_TAXONOMY, known_urls)
        )

        assert len(results) >= 1
        result_urls = [z.url for z in results]
        assert any("PDPA2012" in u for u in result_urls), \
            "KNOWN seed URL should appear in results even when portal is TBD"
        assert all(z.discovery_tag == "KNOWN" for z in results)

    def test_index_fetch_failure_falls_back_to_seed(self):
        """If index URL fetch fails (returns empty), seed KNOWN URLs are returned."""
        from src.crawler.discover import discover

        known_urls = {"https://sso.agc.gov.sg/Act/PDPA2012"}

        async def mock_fetch_fail(url, portal):
            return "", 403

        with patch("src.crawler.discover.transport_fetch", side_effect=mock_fetch_fail):
            results = asyncio.run(
                discover(_SG_ECONOMY, 7, _MINI_TAXONOMY, known_urls)
            )

        assert len(results) >= 1
        result_urls = [z.url for z in results]
        assert any("PDPA2012" in u for u in result_urls)

    def test_discover_does_not_raise_on_empty_index(self):
        """discover() never raises — returns seed fallback gracefully."""
        from src.crawler.discover import discover

        known_urls = {"https://sso.agc.gov.sg/Act/PDPA2012"}

        async def mock_fetch_empty(url, portal):
            return "<html><body></body></html>", 200

        with patch("src.crawler.discover.transport_fetch", side_effect=mock_fetch_empty):
            results = asyncio.run(
                discover(_SG_ECONOMY, 7, _MINI_TAXONOMY, known_urls)
            )

        # No exception raised; known seeds should still be in output
        result_urls = [z.url for z in results]
        assert any("PDPA2012" in u for u in result_urls)

    def test_discover_returns_zone1results(self):
        """discover() always returns Zone1Result instances."""
        from src.crawler.discover import discover

        known_urls = {"https://sso.agc.gov.sg/Act/PDPA2012"}

        async def mock_fetch(url, portal):
            return _SSO_INDEX_HTML, 200

        with patch("src.crawler.discover.transport_fetch", side_effect=mock_fetch):
            results = asyncio.run(
                discover(_SG_ECONOMY, 7, _MINI_TAXONOMY, known_urls)
            )

        assert all(isinstance(r, Zone1Result) for r in results)
        assert all(r.economy == "SG" for r in results)
        assert all(r.discovery_tag in ("KNOWN", "NEW") for r in results)
