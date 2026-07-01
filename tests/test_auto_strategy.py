"""
Tests for the best-effort `auto` discovery + fetch strategy (D7 / ADR-046).

`auto` is the zero-config default: an undeclared portal is crawled best-effort,
SPA-vs-SSR auto-detected via the shared probe (ADR-045). All network/render is
mocked.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

from src.config.economy_config import EconomyConfig
from src.fetcher.models import FetchedDocument, Zone1Result

# SSR page: real content (>200 chars body), no framework markers, same-domain links.
_SSR_HTML = (
    "<html><body><main>"
    "<h1>Legislation Index</h1>"
    "<p>" + ("This portal lists the current in-force statutes of the jurisdiction. " * 6) + "</p>"
    "<ul>"
    "<li><a href='/act/privacy-act-1988'>Privacy Act 1988</a></li>"
    "<li><a href='/act/data-protection-act'>Data Protection Act</a></li>"
    "</ul></main></body></html>"
)
# SPA shell: Angular marker + little content → classify_render → spa.
_SPA_SHELL = "<html><body ng-version='17'><app-root></app-root></body></html>"


def _portal(discovery="auto", fetch="TBD", url="https://legis.example.gov"):
    return EconomyConfig.model_validate({
        "economy_name": "X", "iso_code": "XX", "un_name": "X",
        "script_type": "latin", "languages": ["en"],
        "portals": [{"name": "P", "url": url, "discovery": discovery, "fetch": fetch}],
    }).portals[0]


# ── Config default ────────────────────────────────────────────────────────────────

class TestAutoDefault:
    def test_undeclared_discovery_defaults_to_auto(self):
        p = EconomyConfig.model_validate({
            "economy_name": "X", "iso_code": "XX", "un_name": "X",
            "script_type": "latin", "languages": ["en"],
            "portals": [{"name": "P", "url": "https://e.gov"}],
        }).portals[0]
        assert p.discovery == "auto"

    def test_explicit_tbd_still_skips(self):
        assert _portal(discovery="TBD").discovery == "TBD"

    def test_fetch_auto_accepted(self):
        assert _portal(fetch="auto").fetch == "auto"


# ── auto discovery adapter ─────────────────────────────────────────────────────────

class TestDiscoverAuto:
    def test_ssr_extracts_links_without_render(self):
        from src.crawler.discover import _discover_auto

        async def fake_fetch(url, portal):
            return _SSR_HTML, 200

        with (
            patch("src.crawler.discover.transport_fetch", side_effect=fake_fetch),
            patch("src.crawler.discover._render_spa") as mock_render,
        ):
            cands = asyncio.run(_discover_auto(_portal(), time.monotonic() + 30))

        urls = {u for _, u in cands}
        assert any("privacy-act-1988" in u for u in urls)
        mock_render.assert_not_called()   # SSR → no Playwright render

    def test_spa_shell_triggers_render(self):
        from src.crawler.discover import _discover_auto

        async def fake_fetch(url, portal):
            return _SPA_SHELL, 200

        async def fake_render(url):
            return _SSR_HTML  # rendered DOM has the real links

        with (
            patch("src.crawler.discover.transport_fetch", side_effect=fake_fetch),
            patch("src.crawler.discover._render_spa", side_effect=fake_render) as mock_render,
        ):
            cands = asyncio.run(_discover_auto(_portal(), time.monotonic() + 30))

        assert mock_render.called, "SPA shell must trigger a render"
        assert any("privacy-act-1988" in u for _, u in cands)

    def test_nothing_found_returns_none(self):
        from src.crawler.discover import _discover_auto

        async def empty(url, portal):
            return "<html><body><main>" + ("text " * 60) + "</main></body></html>", 200

        with patch("src.crawler.discover.transport_fetch", side_effect=empty):
            assert asyncio.run(_discover_auto(_portal(), time.monotonic() + 30)) is None

    def test_discover_integrates_auto_through_shared_tail(self):
        from src.crawler.discover import discover

        econ = EconomyConfig.model_validate({
            "economy_name": "X", "iso_code": "XX", "un_name": "X",
            "script_type": "latin", "languages": ["en"],
            "portals": [{"name": "P", "url": "https://legis.example.gov", "discovery": "auto"}],
        })
        tax = [{"indicator_id": "P7-I1", "probe_keywords": ["privacy", "data protection"]}]

        async def fake_fetch(url, portal):
            return _SSR_HTML, 200

        # A KNOWN seed matching an auto-discovered URL proves the auto candidates
        # flow through the shared rank/tag tail (KNOWN tagging bypasses the BM25
        # NEW-threshold, which is meaningless on a 2-candidate corpus).
        known = {"https://legis.example.gov/act/privacy-act-1988"}
        with patch("src.crawler.discover.transport_fetch", side_effect=fake_fetch):
            results = asyncio.run(discover(econ, 7, tax, known))

        assert all(isinstance(r, Zone1Result) for r in results)
        privacy = [r for r in results if "privacy-act-1988" in r.url]
        assert privacy and privacy[0].discovery_tag == "KNOWN"


# ── auto fetch (router) ────────────────────────────────────────────────────────────

class TestFetchAuto:
    def _cfg(self):
        return EconomyConfig.model_validate({
            "economy_name": "X", "iso_code": "XX", "un_name": "X",
            "script_type": "latin", "languages": ["en"],
            "portals": [{"name": "P", "url": "https://legis.example.gov", "fetch": "auto"}],
        })

    def _zone1(self):
        return Zone1Result(url="https://legis.example.gov/act/1", economy="XX",
                           act_title="Some Act", discovery_tag="NEW", archive_url="")

    def test_ssr_html_not_rendered(self):
        from src.fetcher import router

        def mock_download(url, timeout=30):
            return _SSR_HTML.encode(), "text/html", url

        with (
            patch.object(router, "download", side_effect=mock_download),
            patch.object(router, "_render_spa_sync") as mock_render,
        ):
            doc = router.route(self._zone1(), self._cfg())

        assert isinstance(doc, FetchedDocument)
        assert doc.doc_type == "HTML"
        mock_render.assert_not_called()

    def test_spa_shell_is_rendered_before_extraction(self):
        from src.fetcher import router

        def mock_download(url, timeout=30):
            return _SPA_SHELL.encode(), "text/html", url

        with (
            patch.object(router, "download", side_effect=mock_download),
            patch.object(router, "_render_spa_sync", return_value=_SSR_HTML) as mock_render,
        ):
            doc = router.route(self._zone1(), self._cfg())

        assert mock_render.called, "SPA shell must be rendered before extraction"
        assert isinstance(doc, FetchedDocument)
        assert "Privacy Act 1988" in doc.raw_text
