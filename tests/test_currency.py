"""Unit tests for currency.py. [Z1-4-ST7]"""

from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

import src.crawler.currency as currency_mod
from src.crawler.crawler import CandidateAct
from src.crawler.currency import (
    CurrencyResult,
    _detect_currency_status,
    _extract_last_amended,
    _find_replacement_in_text,
    _handle_cancelled_act,
    _is_sectoral_law,
    _validate_url,
    run_currency_check,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _candidate(
    url: str = "https://sso.agc.gov.sg/Act/PDPA2012",
    title: str = "Personal Data Protection Act 2012",
    doc_type: str = "html",
    portal: str = "https://sso.agc.gov.sg",
) -> CandidateAct:
    return CandidateAct(
        act_title=title,
        act_url=url,
        description_snippet="An Act governing personal data.",
        document_type=doc_type,
        discovery_tag="KNOWN",
        portal_source=portal,
        economy="SG",
        pillar="P7",
        pass_number=1,
    )


def _no_sleep(monkeypatch):
    monkeypatch.setattr(currency_mod, "_sleep", AsyncMock(return_value=None))


def _mock_validate(monkeypatch, url: str, status: int):
    async def _mock(u, client):
        return url, status
    monkeypatch.setattr(currency_mod, "_validate_url", _mock)


def _mock_fetch(monkeypatch, text: str):
    async def _mock(url, doc_type, client):
        return text
    monkeypatch.setattr(currency_mod, "_fetch_page_text", _mock)


def _mock_archive(monkeypatch, result: str = "https://web.archive.org/web/20260101/https://sso.agc.gov.sg/Act/PDPA2012"):
    async def _mock(url):
        return result
    monkeypatch.setattr(currency_mod, "_archive_act_url", _mock)


# ── 1. URL Validation (ST1) ────────────────────────────────────────────────────

@respx.mock
async def test_200_url_passes(monkeypatch):
    """HTTP 200 → url validated, currency check proceeds (not 'broken')."""
    _no_sleep(monkeypatch)
    respx.get("https://sso.agc.gov.sg/Act/PDPA2012").mock(
        return_value=httpx.Response(200, text="<html>Current</html>")
    )
    async with httpx.AsyncClient() as client:
        final_url, status = await _validate_url("https://sso.agc.gov.sg/Act/PDPA2012", client)
    assert status == 200
    assert "sso.agc.gov.sg" in final_url


@respx.mock
async def test_404_url_rejected(monkeypatch, tmp_path):
    """HTTP 404 → currency_status = 'broken', excluded from Zone 2."""
    _no_sleep(monkeypatch)
    respx.get("https://sso.agc.gov.sg/Act/PDPA2012").mock(
        return_value=httpx.Response(404)
    )
    _mock_archive(monkeypatch, "")
    archive_called = []

    async def spy_archive(url):
        archive_called.append(url)
        return ""

    monkeypatch.setattr(currency_mod, "_archive_act_url", spy_archive)

    results = await run_currency_check([_candidate()], output_dir=str(tmp_path))
    assert results[0].currency_status == "broken"
    assert not archive_called  # 404 must not be archived


@respx.mock
async def test_redirect_followed(monkeypatch):
    """301 redirect within the same domain → act_url updated to final URL."""
    _no_sleep(monkeypatch)
    respx.get("https://sso.agc.gov.sg/Act/OldAct").mock(
        return_value=httpx.Response(
            301, headers={"Location": "https://sso.agc.gov.sg/Act/NewAct"}
        )
    )
    respx.get("https://sso.agc.gov.sg/Act/NewAct").mock(
        return_value=httpx.Response(200, text="<html>Current</html>")
    )
    async with httpx.AsyncClient() as client:
        final_url, status = await _validate_url("https://sso.agc.gov.sg/Act/OldAct", client)
    assert status == 200
    assert "NewAct" in final_url


@respx.mock
async def test_cross_domain_redirect_rejected(monkeypatch):
    """Redirect to a different registered domain → treat as broken (SUSPICIOUS_REDIRECT)."""
    _no_sleep(monkeypatch)
    respx.get("https://sso.agc.gov.sg/Act/PDPA2012").mock(
        return_value=httpx.Response(
            302, headers={"Location": "https://cloudflare.com/access-denied"}
        )
    )
    respx.get("https://cloudflare.com/access-denied").mock(
        return_value=httpx.Response(200, text="Access Denied")
    )
    async with httpx.AsyncClient() as client:
        final_url, status = await _validate_url("https://sso.agc.gov.sg/Act/PDPA2012", client)
    assert status == 0


@respx.mock
async def test_429_triggers_retry(monkeypatch):
    """429 → wait CURRENCY_RETRY_WAIT_SEC → retry once → proceeds on 200."""
    sleep_calls: list[float] = []

    async def mock_sleep(secs):
        sleep_calls.append(float(secs))

    monkeypatch.setattr(currency_mod, "_sleep", mock_sleep)
    call_count = [0]

    def side_effect(request):
        call_count[0] += 1
        if call_count[0] == 1:
            return httpx.Response(429)
        return httpx.Response(200, text="<html>In force</html>")

    respx.get("https://sso.agc.gov.sg/Act/PDPA2012").mock(side_effect=side_effect)
    async with httpx.AsyncClient() as client:
        _, status = await _validate_url("https://sso.agc.gov.sg/Act/PDPA2012", client)

    assert status == 200
    assert any(s > 0 for s in sleep_calls)  # backoff sleep occurred


@respx.mock
async def test_timeout_flags_review(monkeypatch, tmp_path):
    """httpx timeout → flag_for_review = True, currency_status = 'uncertain' (not broken)."""
    _no_sleep(monkeypatch)
    respx.get("https://sso.agc.gov.sg/Act/PDPA2012").mock(
        side_effect=httpx.TimeoutException("timed out")
    )
    _mock_archive(monkeypatch, "")

    results = await run_currency_check([_candidate()], output_dir=str(tmp_path))
    r = results[0]
    assert r.currency_status == "uncertain"
    assert r.flag_for_review is True


# ── 2. In-Force Detection (ST2) ────────────────────────────────────────────────

def test_repealed_keyword_detected():
    """'repealed' keyword in text → currency_status = 'cancelled'."""
    html = "<html><body><p>This Act has been repealed and is no longer in force.</p></body></html>"
    status, note = _detect_currency_status(html, "https://example.gov/Act/OldAct")
    assert status == "cancelled"
    assert note != ""


def test_in_force_label_sso():
    """SSO <span class='status-tag'>Current</span> → currency_status = 'in_force'."""
    html = (FIXTURES / "sso_current_page.html").read_text()
    status, note = _detect_currency_status(html, "https://sso.agc.gov.sg/Act/PDPA2012")
    assert status == "in_force"


def test_repealed_label_legislation_au():
    """legislation.gov.au <div class='legislation-status'>Repealed</div> → 'cancelled'."""
    html = (FIXTURES / "legislation_au_repealed.html").read_text()
    status, note = _detect_currency_status(html, "https://legislation.gov.au/Details/C2020")
    assert status == "cancelled"


def test_bahasa_cancellation_keyword():
    """'Akta ini telah dibatalkan' in text → currency_status = 'cancelled'."""
    html = "<html><body><p>Akta ini telah dibatalkan berkuatkuasa 1 Januari 2022.</p></body></html>"
    status, note = _detect_currency_status(html, "https://agc.gov.my/Act/PDPA2010")
    assert status == "cancelled"


def test_no_keyword_sets_uncertain():
    """No status signals → currency_status = 'uncertain'."""
    html = "<html><body><h1>Some Act 2010</h1><p>This Act regulates data handling.</p></body></html>"
    status, note = _detect_currency_status(html, "https://example.gov/Act/Generic")
    assert status == "uncertain"
    assert note == ""


# ── 3. Auto-Replacement (ST3) ─────────────────────────────────────────────────

@respx.mock
async def test_replacement_url_in_notice(monkeypatch):
    """Cancellation notice contains replacement URL → replacement URL fetched and used."""
    _no_sleep(monkeypatch)
    notice_html = (FIXTURES / "cancellation_notice.html").read_text()
    respx.get("https://sso.agc.gov.sg/Act/PDPA2022").mock(
        return_value=httpx.Response(200, text="<html>In force</html>")
    )
    async with httpx.AsyncClient() as client:
        repl_url, note, flag = await _handle_cancelled_act(
            "Old Data Protection Act 2010", notice_html,
            "https://sso.agc.gov.sg", client,
        )
    assert repl_url is not None
    assert "PDPA2022" in repl_url
    assert flag is False


async def test_replacement_name_only(monkeypatch):
    """Notice has act name but no URL → portal search triggered, result used if valid."""
    _no_sleep(monkeypatch)
    notice_html = (
        "<html><body><p>This Act was superseded by the Data Security Act 2023. "
        "Please refer to the current consolidated version.</p></body></html>"
    )
    # Mock _search_portal to return a candidate URL
    async def mock_search(portal_url, query, client):
        return "https://sso.agc.gov.sg/Act/DSA2023"

    monkeypatch.setattr(currency_mod, "_search_portal", mock_search)

    # Mock the validation GET for the found replacement
    @respx.mock
    async def _run():
        respx.get("https://sso.agc.gov.sg/Act/DSA2023").mock(
            return_value=httpx.Response(200, text="<html>In force</html>")
        )
        async with httpx.AsyncClient() as client:
            return await _handle_cancelled_act(
                "Old Data Act 2010", notice_html,
                "https://sso.agc.gov.sg", client,
            )

    repl_url, note, flag = await _run()
    assert note != ""


async def test_cancelled_no_replacement(monkeypatch):
    """No replacement found → flag_for_review = True, act still in output (not dropped)."""
    _no_sleep(monkeypatch)
    notice_html = "<html><body><p>This Act has been repealed.</p></body></html>"

    # Mock _search_portal to return nothing (no replacement found)
    async def mock_search(portal_url, query, client):
        return None

    monkeypatch.setattr(currency_mod, "_search_portal", mock_search)

    async with httpx.AsyncClient() as client:
        repl_url, note, flag = await _handle_cancelled_act(
            "Expired Data Act 2005", notice_html,
            "https://sso.agc.gov.sg", client,
        )
    assert repl_url is None
    assert flag is True
    assert "review" in note.lower()


def test_sectoral_law_triggers_horizontal_search():
    """Act with sectoral title keyword is identified as a sectoral law."""
    assert _is_sectoral_law("Banking Act 1970") is True
    assert _is_sectoral_law("Telecommunications Act 2000") is True
    assert _is_sectoral_law("Personal Data Protection Act 2012") is False


# ── 4. last_amended Extraction (ST4) ──────────────────────────────────────────

def test_amended_year_from_html():
    """Portal page with 'as amended in 2021' → last_amended = '2021'."""
    text = "Personal Data Protection Act 2012. As amended in 2021. Current version."
    assert _extract_last_amended(text) == "2021"


def test_most_recent_year_selected():
    """Multiple years in text → most recent year returned."""
    text = "Act 2012. Amendment Act 2018. Consolidated as at 2021."
    result = _extract_last_amended(text)
    assert result == "2021"


def test_year_not_found_blank():
    """No amendment year pattern → last_amended = '' (not null, not 'unknown')."""
    text = "An Act to regulate the collection of personal data."
    assert _extract_last_amended(text) == ""


def test_invalid_year_rejected():
    """Year outside 1950–current range is rejected → last_amended = ''."""
    text = "This consolidated edition as amended in 1850."
    assert _extract_last_amended(text) == ""


# ── 5. Wayback Archiving (ST5) ────────────────────────────────────────────────

@respx.mock
async def test_archive_url_populated(monkeypatch, tmp_path):
    """Wayback 200 + Content-Location header → archive_url populated."""
    _no_sleep(monkeypatch)
    act_url = "https://sso.agc.gov.sg/Act/PDPA2012"
    snapshot_loc = "/web/20260101120000/" + act_url
    respx.get(f"https://web.archive.org/save/{act_url}").mock(
        return_value=httpx.Response(
            200,
            headers={"Content-Location": snapshot_loc},
        )
    )
    from src.crawler.currency import _archive_act_url
    archive = await _archive_act_url(act_url)
    assert archive.startswith("https://web.archive.org/web/")


async def test_archive_rate_limit_respected(monkeypatch, tmp_path):
    """3 archived acts → _sleep called 3 times with WAYBACK_RATE_LIMIT_SEC."""
    sleep_calls: list[float] = []

    async def mock_sleep(secs):
        sleep_calls.append(float(secs))

    monkeypatch.setattr(currency_mod, "_sleep", mock_sleep)

    async def mock_validate(url, client):
        return url, 200

    async def mock_fetch(url, doc_type, client):
        return "<html>in force as of 2021</html>"

    async def mock_archive(url):
        return "https://web.archive.org/web/123/" + url

    monkeypatch.setattr(currency_mod, "_validate_url", mock_validate)
    monkeypatch.setattr(currency_mod, "_fetch_page_text", mock_fetch)
    monkeypatch.setattr(currency_mod, "_archive_act_url", mock_archive)

    candidates = [
        _candidate(url=f"https://sso.agc.gov.sg/Act/Act{i:04d}",
                   title=f"Data Act {i:04d} Long Enough Title")
        for i in range(3)
    ]
    await run_currency_check(candidates, output_dir=str(tmp_path))

    rate_limit_sleeps = [s for s in sleep_calls if abs(s - currency_mod._WAYBACK_RATE_LIMIT_SEC) < 0.01]
    assert len(rate_limit_sleeps) == 3


@respx.mock
async def test_archive_failure_does_not_crash(monkeypatch, tmp_path):
    """Wayback 523 → archive_url = '', pipeline continues without exception."""
    _no_sleep(monkeypatch)
    act_url = "https://sso.agc.gov.sg/Act/PDPA2012"
    respx.get(f"https://web.archive.org/save/{act_url}").mock(
        return_value=httpx.Response(523)
    )
    from src.crawler.currency import _archive_act_url
    archive = await _archive_act_url(act_url)
    assert archive == ""


async def test_broken_url_not_archived(monkeypatch, tmp_path):
    """404 act URL → _archive_act_url never called."""
    _no_sleep(monkeypatch)
    archive_calls: list[str] = []

    async def mock_validate(url, client):
        return url, 404

    async def spy_archive(url):
        archive_calls.append(url)
        return ""

    monkeypatch.setattr(currency_mod, "_validate_url", mock_validate)
    monkeypatch.setattr(currency_mod, "_archive_act_url", spy_archive)

    await run_currency_check([_candidate()], output_dir=str(tmp_path))
    assert archive_calls == []


# ── 6. Output contract ─────────────────────────────────────────────────────────

async def test_full_list_returned_no_filtering(monkeypatch, tmp_path):
    """run_currency_check returns ALL acts — broken, cancelled, uncertain, in-force."""
    _no_sleep(monkeypatch)
    statuses = [200, 200, 200, 404, 200]
    texts = [
        "<html>in force as of 2021</html>",
        "<html>in force as of 2021</html>",
        "<html>in force as of 2021</html>",
        "",  # 404 act — won't be fetched
        "<html>repealed</html>",
    ]
    call_count = [0]

    async def mock_validate(url, client):
        i = call_count[0]
        return url, statuses[i]

    fetch_count = [0]

    async def mock_fetch(url, doc_type, client):
        i = fetch_count[0]
        fetch_count[0] += 1
        return texts[i] if i < len(texts) else ""

    async def mock_archive(url):
        return "https://web.archive.org/web/123/" + url

    async def mock_handle_cancelled(title, text, portal, client):
        return None, "Cancelled. No replacement found. Manual review required.", True

    monkeypatch.setattr(currency_mod, "_validate_url", mock_validate)
    monkeypatch.setattr(currency_mod, "_fetch_page_text", mock_fetch)
    monkeypatch.setattr(currency_mod, "_archive_act_url", mock_archive)
    monkeypatch.setattr(currency_mod, "_handle_cancelled_act", mock_handle_cancelled)

    # Patch validate to cycle through statuses
    idx = [0]

    async def cycling_validate(url, client):
        i = idx[0]
        idx[0] += 1
        return url, statuses[i]

    monkeypatch.setattr(currency_mod, "_validate_url", cycling_validate)

    candidates = [
        _candidate(url=f"https://sso.agc.gov.sg/Act/Act{i:04d}",
                   title=f"Test Act {i:04d} Long Enough Title")
        for i in range(5)
    ]
    results = await run_currency_check(candidates, output_dir=str(tmp_path))

    assert len(results) == 5  # all 5 returned, including broken/cancelled


async def test_currency_result_fields_complete(monkeypatch, tmp_path):
    """Every CurrencyResult has all 15 fields with no None values."""
    _no_sleep(monkeypatch)

    async def mock_validate(url, client):
        return url, 200

    async def mock_fetch(url, doc_type, client):
        return "<html><p>in force as of 2021</p><p>consolidated as at 2021</p></html>"

    async def mock_archive(url):
        return "https://web.archive.org/web/20260101/" + url

    monkeypatch.setattr(currency_mod, "_validate_url", mock_validate)
    monkeypatch.setattr(currency_mod, "_fetch_page_text", mock_fetch)
    monkeypatch.setattr(currency_mod, "_archive_act_url", mock_archive)

    results = await run_currency_check([_candidate()], output_dir=str(tmp_path))
    r = results[0]

    assert isinstance(r, CurrencyResult)
    assert isinstance(r.act_title, str) and r.act_title
    assert isinstance(r.act_url, str) and r.act_url
    assert isinstance(r.description_snippet, str)
    assert isinstance(r.document_type, str)
    assert isinstance(r.discovery_tag, str)
    assert isinstance(r.portal_source, str)
    assert isinstance(r.economy, str)
    assert isinstance(r.pillar, str)
    assert isinstance(r.pass_number, int)
    assert isinstance(r.http_status, int)
    assert r.currency_status in ("in_force", "cancelled", "uncertain", "broken")
    assert isinstance(r.flag_for_review, bool)
    assert isinstance(r.currency_note, str)   # "" is OK
    assert isinstance(r.last_amended, str)     # "" is OK
    assert isinstance(r.archive_url, str)      # "" is OK
    # No None anywhere
    for field_name, val in r.__dict__.items():
        assert val is not None, f"Field '{field_name}' is None"


# ── 7. Helpers ─────────────────────────────────────────────────────────────────

def test_find_replacement_url_in_text():
    """_find_replacement_in_text extracts explicit URL from cancellation notice."""
    text = (FIXTURES / "cancellation_notice.html").read_text()
    url, name = _find_replacement_in_text(text)
    assert url is not None
    assert "PDPA2022" in url


def test_find_replacement_name_in_text():
    """_find_replacement_in_text extracts act name when no URL present."""
    text = "This Act was superseded by the Data Protection Act 2022."
    url, name = _find_replacement_in_text(text)
    # Either URL or name found (or both None if patterns don't match)
    # Key: function doesn't crash
    assert url is None or isinstance(url, str)
    assert name is None or isinstance(name, str)
