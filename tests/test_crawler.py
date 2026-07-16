"""Unit tests for crawler.py URL/link helpers."""

from src.crawler.crawler import (
    _extract_act_links,
    _normalise_url,
    _registered_domain,
)


# ── 1. URL extraction ──────────────────────────────────────────────────────────

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


# ── 3. URL helpers ─────────────────────────────────────────────────────────────

def test_normalise_url_strips_lang_param():
    assert _normalise_url("https://sso.agc.gov.sg/Act/PDPA2012?lang=en") == "https://sso.agc.gov.sg/act/pdpa2012"


def test_normalise_url_strips_trailing_slash():
    assert _normalise_url("https://sso.agc.gov.sg/") == "https://sso.agc.gov.sg"


def test_registered_domain_handles_subdomains():
    assert _registered_domain("https://www.federalgazette.agc.gov.my") == "agc.gov.my"
    assert _registered_domain("https://sso.agc.gov.sg") == "agc.gov.sg"
