"""Unit tests for the legislation citation metadata extractor. [PRD 86ey22k30 — fix #2]"""

from __future__ import annotations

from src.fetcher.extractors.legislation_meta import derive_act_title, extract_legislation_meta


def test_act_number_and_revised_edition_from_cover():
    cover = "PERSONAL DATA PROTECTION ACT 2012\n2020 Ed.\nAct 26 of 2012\n..."
    ref, amended = extract_legislation_meta(cover, "https://sso.agc.gov.sg/Act/PDPA2012?ViewType=Pdf")
    assert ref == "Act 26 of 2012"
    assert amended == "2020"


def test_revised_edition_only_falls_back_to_ref():
    cover = "SOME ACT\n2018 Rev. Ed.\n"
    ref, amended = extract_legislation_meta(cover, "https://sso.agc.gov.sg/Act/XYZ")
    assert ref == "2018 Rev. Ed."
    assert amended == "2018"


def test_url_docdate_is_preferred_for_last_amended():
    # DocDate (the document's version date) overrides the cover's edition year.
    cover = "Active Mobility Regulations 2019\n2019 Ed."
    ref, amended = extract_legislation_meta(
        cover, "https://sso.agc.gov.sg/SL/AMA2017-S15-2019?DocDate=20260529&ViewType=Pdf"
    )
    assert amended == "2026-05-29"


def test_non_sso_returns_none():
    ref, amended = extract_legislation_meta("Just some prose with no citation.", "https://example.com/doc")
    assert ref is None
    assert amended is None


def test_empty_text_is_safe():
    assert extract_legislation_meta("", "https://example.com") == (None, None)


# ── derive_act_title ────────────────────────────────────────────────────────────

def test_derive_title_from_sso_cover_page():
    cover = ("THE STATUTES OF THE REPUBLIC OF SINGAPORE\nCOMPANIES ACT 1967\n"
             "2020 REVISED EDITION\n")
    assert derive_act_title(cover, "https://sso.agc.gov.sg/act/coa1967") == "Companies Act 1967"


def test_derive_title_skips_boilerplate_marker_lines():
    # "THE STATUTES OF..." contains no marker; "REVISED EDITION" is skipped.
    cover = "THE STATUTES OF THE REPUBLIC OF SINGAPORE\nINCOME TAX ACT 1947\n2020 REVISED EDITION"
    assert derive_act_title(cover) == "Income Tax Act 1947"


def test_derive_title_falls_back_to_url_slug():
    # No marker line at all → readable slug from the URL, never empty.
    assert derive_act_title("prose with no title", "https://sso.agc.gov.sg/act/ba1970") == "BA1970"


def test_derive_title_empty_when_nothing_usable():
    assert derive_act_title("", "") == ""
