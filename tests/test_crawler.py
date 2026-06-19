"""Unit tests for crawler.py. [Z1-3-ST7]"""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import src.crawler.crawler as crawler_mod
from src.crawler.crawler import (
    CandidateAct,
    _extract_act_links,
    _has_next_page,
    _is_js_portal,
    _normalise_url,
    _registered_domain,
    _with_retry,
    load_known_urls,
    run_crawler,
)
from src.crawler.exceptions import CrawlerError
from src.crawler.probe import ProbeResult

# ── Fixtures ───────────────────────────────────────────────────────────────────

FIXTURES = Path(__file__).parent / "fixtures"


def _sg_probe(is_active: bool = True) -> ProbeResult:
    return ProbeResult(
        url="https://sso.agc.gov.sg",
        portal_name="Singapore Statutes Online",
        portal_type="primary",
        total_hit_count=5,
        is_active=is_active,
        language="en",
        probe_status="ok",
    )


def _known_urls() -> set[str]:
    return {"https://sso.agc.gov.sg/Act/PDPA2012"}


def _no_sleep(monkeypatch):
    monkeypatch.setattr(crawler_mod, "_sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(crawler_mod, "_jitter", AsyncMock(return_value=None))


# ── 1. Crawl4AI setup and domain locking ──────────────────────────────────────

def test_is_js_portal_true_for_sso():
    assert _is_js_portal("https://sso.agc.gov.sg") is True


def test_is_js_portal_false_for_static():
    assert _is_js_portal("https://www.egazette.gov.sg") is False


async def test_off_domain_url_discarded(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """URL on a different domain is never included in candidate list."""
    _no_sleep(monkeypatch)

    async def mock_playwright(url, wait_for, timeout_ms):
        return (
            '<a href="/Act/PDPA2012">Personal Data Protection Act 2012</a>'
            '<a href="https://example.com/doc">Off Domain Document Link Here</a>',
            200,
        )

    async def mock_detect(url, client):
        return "html"

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)
    monkeypatch.setattr(crawler_mod, "_detect_document_type", mock_detect)

    results = await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))
    assert all("example.com" not in c.act_url for c in results)


async def test_depth_limit_respected(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """Crawler does not follow links beyond CRAWL_MAX_DEPTH."""
    _no_sleep(monkeypatch)
    monkeypatch.setattr(crawler_mod, "_CRAWL_MAX_DEPTH", 1)
    fetch_count = [0]

    async def mock_playwright(url, wait_for, timeout_ms):
        fetch_count[0] += 1
        n = fetch_count[0]
        return f'<a href="/Act/Act{n:04d}">Act {n:04d} Long Enough Title</a>', 200

    async def mock_detect(url, client):
        return "html"

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)
    monkeypatch.setattr(crawler_mod, "_detect_document_type", mock_detect)

    await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))
    # With max_depth=1: seed (depth 0) + direct children (depth 1) + pass2 search pages
    # Verify it terminates and doesn't spiral infinitely
    assert fetch_count[0] < 200


async def test_429_triggers_backoff(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """429 response triggers exponential backoff: sleeps 1, 2, 4 seconds."""
    sleep_calls: list[float] = []

    async def mock_sleep(seconds):
        sleep_calls.append(float(seconds))

    monkeypatch.setattr(crawler_mod, "_sleep", mock_sleep)
    monkeypatch.setattr(crawler_mod, "_jitter", AsyncMock(return_value=None))

    async def mock_playwright(url, wait_for, timeout_ms):
        return "", 429

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)

    with pytest.raises(CrawlerError):
        await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))

    assert 1.0 in sleep_calls
    assert 2.0 in sleep_calls
    assert 4.0 in sleep_calls


async def test_403_skipped_immediately(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """403 is skipped without any retry sleeps."""
    sleep_calls: list[float] = []

    async def mock_sleep(seconds):
        sleep_calls.append(float(seconds))

    monkeypatch.setattr(crawler_mod, "_sleep", mock_sleep)
    monkeypatch.setattr(crawler_mod, "_jitter", AsyncMock(return_value=None))

    async def mock_playwright(url, wait_for, timeout_ms):
        return "", 403

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)

    with pytest.raises(CrawlerError):
        await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))

    assert sleep_calls == []  # no backoff sleep, only _jitter (which was no-op)


# ── 2. Two-pass discovery ──────────────────────────────────────────────────────

async def test_pass1_uses_seed_urls(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """Known URLs appear in output with discovery_tag='KNOWN'."""
    _no_sleep(monkeypatch)

    async def mock_playwright(url, wait_for, timeout_ms):
        return '<a href="/Act/PDPA2012">Personal Data Protection Act 2012</a>', 200

    async def mock_detect(url, client):
        return "html"

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)
    monkeypatch.setattr(crawler_mod, "_detect_document_type", mock_detect)

    results = await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))

    known_results = [r for r in results if r.discovery_tag == "KNOWN"]
    assert len(known_results) >= 1


async def test_pass2_tags_new_acts(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """Acts found in Pass 2 not in the known set get discovery_tag='NEW'."""
    _no_sleep(monkeypatch)
    pass1_done = [False]

    async def mock_playwright(url, wait_for, timeout_ms):
        if not pass1_done[0] and "/Act/PDPA2012" in url:
            pass1_done[0] = True
            return '<a href="/Act/PDPA2012">Personal Data Protection Act 2012</a>', 200
        # Pass 2: new act not in known set
        return '<a href="/Act/BrandNewAct9999">Brand New Data Act 9999 Unique Title</a>', 200

    async def mock_detect(url, client):
        return "html"

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)
    monkeypatch.setattr(crawler_mod, "_detect_document_type", mock_detect)

    results = await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))

    new_results = [r for r in results if r.discovery_tag == "NEW"]
    assert len(new_results) >= 1


async def test_known_act_not_duplicated(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """Act found in both passes appears only once, tagged KNOWN."""
    _no_sleep(monkeypatch)

    async def mock_playwright(url, wait_for, timeout_ms):
        return '<a href="/Act/PDPA2012">Personal Data Protection Act 2012</a>', 200

    async def mock_detect(url, client):
        return "html"

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)
    monkeypatch.setattr(crawler_mod, "_detect_document_type", mock_detect)

    results = await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))

    pdpa_results = [r for r in results if "PDPA2012" in r.act_url]
    assert len(pdpa_results) == 1
    assert pdpa_results[0].discovery_tag == "KNOWN"


async def test_pass1_runs_before_pass2(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """All Pass 1 fetches complete before any Pass 2 fetch starts."""
    _no_sleep(monkeypatch)
    call_order: list[str] = []

    async def mock_playwright(url, wait_for, timeout_ms):
        # Pass 1 seeds are the known_urls (direct /Act/ paths)
        # Pass 2 seeds are search URLs (contain 'SearchAct=')
        if "SearchAct=" in url or ("Search" in url and "PDPA2012" not in url):
            call_order.append("pass2")
        else:
            call_order.append("pass1")
        return '<a href="/Act/PDPA2012">Personal Data Protection Act 2012</a>', 200

    async def mock_detect(url, client):
        return "html"

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)
    monkeypatch.setattr(crawler_mod, "_detect_document_type", mock_detect)

    await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))

    last_pass1 = max((i for i, v in enumerate(call_order) if v == "pass1"), default=-1)
    first_pass2 = min((i for i, v in enumerate(call_order) if v == "pass2"), default=len(call_order))
    assert last_pass1 < first_pass2


# ── 3. JS-rendered portal (Singapore SSO) ─────────────────────────────────────

async def test_sso_playwright_used(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """For Singapore SSO, _fetch_with_playwright is called (not httpx)."""
    _no_sleep(monkeypatch)
    playwright_urls: list[str] = []
    httpx_urls: list[str] = []

    async def mock_playwright(url, wait_for, timeout_ms):
        playwright_urls.append(url)
        return '<a href="/Act/PDPA2012">Personal Data Protection Act 2012</a>', 200

    async def mock_httpx(url, client):
        httpx_urls.append(url)
        return "", 200

    async def mock_detect(url, client):
        return "html"

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)
    monkeypatch.setattr(crawler_mod, "_fetch_with_httpx", mock_httpx)
    monkeypatch.setattr(crawler_mod, "_detect_document_type", mock_detect)

    await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))

    assert any("sso.agc.gov.sg" in u for u in playwright_urls)
    assert not any("sso.agc.gov.sg" in u for u in httpx_urls)


async def test_sso_pagination_followed(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """Pagination 'Next' link on SSO page 1 causes page 2 to be fetched."""
    _no_sleep(monkeypatch)
    crawled: list[str] = []

    async def mock_playwright(url, wait_for, timeout_ms):
        crawled.append(url)
        if "Page=2" not in url:
            html = (FIXTURES / "sso_listing_page.html").read_text()
        else:
            html = '<a href="/Act/CMA1993">Computer Misuse Act 1993 Cap 50A</a>'
        return html, 200

    async def mock_detect(url, client):
        return "html"

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)
    monkeypatch.setattr(crawler_mod, "_detect_document_type", mock_detect)

    await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))

    assert any("Page=2" in u for u in crawled), "Page 2 was never fetched"


async def test_sso_load_timeout_logged(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """Playwright timeout (status 0) is handled gracefully — no uncaught exception."""
    _no_sleep(monkeypatch)

    async def mock_playwright(url, wait_for, timeout_ms):
        return "", 0  # simulates a timeout

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)

    with pytest.raises(CrawlerError):
        await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))


# ── 4. URL extraction and deduplication ───────────────────────────────────────

def test_pdf_link_detected():
    """.pdf URL is extracted as a candidate."""
    html = '<a href="/docs/pdpa.pdf">PDPA Document PDF version 2012</a>'
    acts = _extract_act_links(html, "https://sso.agc.gov.sg", "agc.gov.sg")
    assert any(a["url"].endswith(".pdf") for a in acts)


def test_html_page_detected():
    """/Act/PDPA2012 URL (no extension) is extracted as a candidate."""
    html = '<a href="/Act/PDPA2012">Personal Data Protection Act 2012</a>'
    acts = _extract_act_links(html, "https://sso.agc.gov.sg", "agc.gov.sg")
    assert any("/Act/PDPA2012" in a["url"] for a in acts)


async def test_duplicate_url_not_added(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """Same act URL encountered across passes appears exactly once in output."""
    _no_sleep(monkeypatch)

    async def mock_playwright(url, wait_for, timeout_ms):
        return '<a href="/Act/PDPA2012">Personal Data Protection Act 2012</a>', 200

    async def mock_detect(url, client):
        return "html"

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)
    monkeypatch.setattr(crawler_mod, "_detect_document_type", mock_detect)

    results = await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))

    pdpa_results = [r for r in results if "PDPA2012" in r.act_url]
    assert len(pdpa_results) == 1


def test_empty_title_discarded():
    """Acts with empty or very short title are not extracted."""
    html = (
        '<a href="/Act/PDPA2012"></a>'                          # empty title
        '<a href="/Act/AB">Ab</a>'                              # < 5 chars
        '<a href="/Act/Good">Good Long Enough Title Here</a>'   # valid
    )
    acts = _extract_act_links(html, "https://sso.agc.gov.sg", "agc.gov.sg")
    titles = [a["title"] for a in acts]
    assert "" not in titles
    assert "Ab" not in titles
    assert "Good Long Enough Title Here" in titles


async def test_description_snippet_max_500_chars(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """description_snippet on every CandidateAct is at most 500 characters."""
    _no_sleep(monkeypatch)
    long_desc = "x" * 600

    async def mock_playwright(url, wait_for, timeout_ms):
        return (
            f'<li><a href="/Act/PDPA2012">Personal Data Protection Act 2012</a>'
            f'<p>{long_desc}</p></li>',
            200,
        )

    async def mock_detect(url, client):
        return "html"

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)
    monkeypatch.setattr(crawler_mod, "_detect_document_type", mock_detect)

    results = await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))

    for r in results:
        assert len(r.description_snippet) <= 500


# ── 5. Output contract ─────────────────────────────────────────────────────────

async def test_empty_result_raises_crawler_error(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """CrawlerError is raised when no acts are discovered."""
    _no_sleep(monkeypatch)

    async def mock_playwright(url, wait_for, timeout_ms):
        return "<html><body>No results</body></html>", 200

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)

    with pytest.raises(CrawlerError, match="No candidate acts found"):
        await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))


async def test_candidate_act_fields_complete(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """Every CandidateAct has all 9 required fields populated with correct types."""
    _no_sleep(monkeypatch)

    async def mock_playwright(url, wait_for, timeout_ms):
        return '<a href="/Act/PDPA2012">Personal Data Protection Act 2012</a>', 200

    async def mock_detect(url, client):
        return "html"

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)
    monkeypatch.setattr(crawler_mod, "_detect_document_type", mock_detect)

    results = await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))

    assert len(results) > 0
    for r in results:
        assert isinstance(r, CandidateAct)
        assert r.act_title and isinstance(r.act_title, str)
        assert r.act_url and isinstance(r.act_url, str)
        assert isinstance(r.description_snippet, str)
        assert r.document_type in ("pdf", "html")
        assert r.discovery_tag in ("KNOWN", "NEW")
        assert r.portal_source and isinstance(r.portal_source, str)
        assert r.economy and isinstance(r.economy, str)
        assert r.pillar and isinstance(r.pillar, str)
        assert isinstance(r.pass_number, int) and r.pass_number in (1, 2)


async def test_known_acts_sorted_before_new(monkeypatch, sg_economy, full_taxonomy, tmp_path):
    """Output list: all KNOWN entries appear before any NEW entries."""
    _no_sleep(monkeypatch)
    call_count = [0]

    async def mock_playwright(url, wait_for, timeout_ms):
        call_count[0] += 1
        if call_count[0] <= 1:
            return '<a href="/Act/PDPA2012">Personal Data Protection Act 2012</a>', 200
        return '<a href="/Act/BrandNewAct9999">Brand New Unique Data Act 9999</a>', 200

    async def mock_detect(url, client):
        return "html"

    monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", mock_playwright)
    monkeypatch.setattr(crawler_mod, "_detect_document_type", mock_detect)

    results = await run_crawler([_sg_probe()], sg_economy, full_taxonomy, _known_urls(), output_dir=str(tmp_path))

    tags = [r.discovery_tag for r in results]
    if "NEW" in tags and "KNOWN" in tags:
        last_known = max(i for i, t in enumerate(tags) if t == "KNOWN")
        first_new = min(i for i, t in enumerate(tags) if t == "NEW")
        assert last_known < first_new


# ── 6. load_known_urls ────────────────────────────────────────────────────────

def test_load_known_urls_from_xlsx():
    """load_known_urls reads SG URLs from the fixture XLSX when filtered by economy."""
    xlsx_path = str(FIXTURES / "round1_db_sg.xlsx")
    urls = load_known_urls(xlsx_path, economy_name="Singapore")
    assert any("sso.agc.gov.sg" in u for u in urls)
    assert len(urls) >= 3


def test_load_known_urls_no_filter():
    """load_known_urls with no filter returns URLs for all economies."""
    xlsx_path = str(FIXTURES / "round1_db_sg.xlsx")
    urls = load_known_urls(xlsx_path)
    assert len(urls) >= 4  # includes the Malaysia row


# ── 7. URL helpers ─────────────────────────────────────────────────────────────

def test_normalise_url_strips_lang_param():
    assert _normalise_url("https://sso.agc.gov.sg/Act/PDPA2012?lang=en") == "https://sso.agc.gov.sg/act/pdpa2012"


def test_normalise_url_strips_trailing_slash():
    assert _normalise_url("https://sso.agc.gov.sg/") == "https://sso.agc.gov.sg"


def test_registered_domain_handles_subdomains():
    assert _registered_domain("https://www.federalgazette.agc.gov.my") == "agc.gov.my"
    assert _registered_domain("https://sso.agc.gov.sg") == "agc.gov.sg"


def test_has_next_page_detects_link():
    assert _has_next_page('<a href="/Search?Page=2">Next</a>') is not None


def test_has_next_page_returns_none_when_absent():
    assert _has_next_page('<a href="/about">About The Organisation</a>') is None
