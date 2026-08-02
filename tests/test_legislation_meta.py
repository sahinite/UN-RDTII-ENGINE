"""Unit tests for the legislation citation metadata extractor. [PRD 86ey22k30 — fix #2]"""

from __future__ import annotations

from src.fetcher.extractors.legislation_meta import derive_act_title, extract_legislation_meta


def test_act_number_and_revised_edition_from_cover():
    cover = "PERSONAL DATA PROTECTION ACT 2012\n2020 Ed.\nAct 26 of 2012\n..."
    ref, amended = extract_legislation_meta(
        cover,
        "https://sso.agc.gov.sg/Act/PDPA2012?ViewType=Pdf",
        "Personal Data Protection Act 2012",
    )
    assert ref == "Act 26 of 2012"
    assert amended is None


def test_revised_edition_only_falls_back_to_ref():
    cover = "SOME ACT\n2018 Rev. Ed.\n"
    ref, amended = extract_legislation_meta(cover, "https://sso.agc.gov.sg/Act/XYZ")
    assert ref == "2018 Rev. Ed."
    assert amended is None


def test_url_docdate_is_fallback_for_last_amended_year():
    # Version URLs are the fallback and the output schema requires a year.
    cover = "Active Mobility Regulations 2019\n2019 Ed."
    ref, amended = extract_legislation_meta(
        cover, "https://sso.agc.gov.sg/SL/AMA2017-S15-2019?DocDate=20260529&ViewType=Pdf"
    )
    assert amended == "2026"


def test_sso_history_uses_principal_act_and_latest_effective_amendment_year():
    history = """
    Personal Data Protection Act 2012
    05 Dec 2025
    Amended by
    Act 19 of 2025
    01 Oct 2022
    Amended by
    Act 40 of 2020
    02 Jan 2013
    Act 26 of 2012
    Current version as at 01 Aug 2026
    """
    ref, amended = extract_legislation_meta(
        history,
        "https://sso.agc.gov.sg/Act/PDPA2012?WholeDoc=1",
        "Personal Data Protection Act 2012",
    )
    assert ref == "Act 26 of 2012"
    assert amended == "2025"


def test_malaysia_act_number_and_explicit_latest_amendment():
    cover = """
    LAWS OF MALAYSIA
    ONLINE VERSION OF UPDATED TEXT OF REPRINT
    Act 807
    SERVICE TAX ACT 2018
    As at 1 January 2023
    Latest amendment made by Act A1672 which came into operation on 1 January 2023
    """
    ref, amended = extract_legislation_meta(
        cover, "https://www.hasil.gov.my/service-tax-act-2018.pdf", "Service Tax Act 2018"
    )
    assert ref == "Act 807"
    assert amended == "2023"


def test_malaysia_unamended_act_keeps_last_amended_blank():
    cover = "LAWS OF MALAYSIA\nAct 854\nCYBER SECURITY ACT 2024\nDate of Royal Assent 18 June 2024"
    ref, amended = extract_legislation_meta(cover, "https://lom.agc.gov.my/Act%20854.pdf")
    assert ref == "Act 854"
    assert amended is None


def test_australia_compilation_cover_and_versioned_url():
    cover = """
    Criminal Code Act 1995
    No. 12, 1995
    Compilation No. 173
    Compilation date: 14 March 2026
    Includes amendments: Act No. 7, 2026
    """
    ref, amended = extract_legislation_meta(
        cover,
        "https://www.legislation.gov.au/C2004A03712/2026-03-14/2026-03-14/text/original/pdf",
        "Criminal Code Act 1995",
    )
    assert ref == "No. 12, 1995"
    assert amended == "2026"


def test_title_parenthetical_act_reference_is_supported():
    ref, amended = extract_legislation_meta(
        "Personal data protection guidance without a cover reference.",
        "https://www.pdp.gov.my/guidance",
        "Personal Data Protection Act (Act 709)",
    )
    assert ref == "Act 709"
    assert amended is None


def test_current_as_at_is_not_treated_as_last_amended():
    ref, amended = extract_legislation_meta(
        "Some Act 2020\nCurrent version as at 1 August 2026",
        "https://example.gov/some-act",
        "Some Act 2020",
    )
    assert ref is None
    assert amended is None


def test_guideline_does_not_inherit_number_of_referenced_enabling_act():
    text = (
        "PERSONAL DATA PROTECTION GUIDELINES\nCROSS BORDER PERSONAL DATA TRANSFER\n"
        + ("scope and responsibilities\n" * 70)
        + "These guidelines are issued under Act 709."
    )
    ref, amended = extract_legislation_meta(
        text,
        "https://www.pdp.gov.my/cross-border-guidelines.pdf",
        "Personal Data Protection Guidelines on Cross-Border Transfer of Personal Data",
    )
    assert ref is None
    assert amended is None


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
