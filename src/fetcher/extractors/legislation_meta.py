"""Deterministic legislation citation-metadata extraction.

The output schema expects an optional official ``law_number_ref`` and the year
of the most recent amendment (blank when the source does not establish one).
Government portals express those values differently, so this module recognises
the cover/history conventions used by Singapore, Australia, and Malaysia while
returning ``None`` instead of guessing from a law's enactment year.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Optional

# Official-number forms seen in the submission template and configured portals.
_ACT_OF_YEAR_RE = re.compile(
    r"\bAct\s+(?P<number>[A-Z]?\d+)\s+of\s+(?P<year>\d{4})\b",
    re.IGNORECASE,
)
_NO_COMMA_YEAR_RE = re.compile(
    r"\b(?:Act\s+)?No\.?\s*(?P<number>\d+)\s*,\s*(?P<year>\d{4})\b",
    re.IGNORECASE,
)
_NO_OF_YEAR_RE = re.compile(
    r"\bNo\.?\s*(?P<number>\d+)\s+of\s+(?P<year>\d{4})\b",
    re.IGNORECASE,
)
_LAW_NO_RE = re.compile(
    r"\bLaw\s+No\.?\s*(?P<number>[A-Z0-9.-]+(?:/\d{2,4})?)\b",
    re.IGNORECASE,
)
_BE_REF_RE = re.compile(r"\bB\.?\s*E\.?\s*(?P<year>25\d{2})\b", re.IGNORECASE)
# Malaysia uses "Act 709" / "Act A1727".  Restrict numeric-only references to
# 1-3 digits so a title such as "Privacy Act 1988" is never mistaken for a law
# number; A-prefixed amendment references may have four digits.
_SIMPLE_ACT_RE = re.compile(
    r"\bAct\s+(?P<number>[A-Z]\d{1,4}|\d{1,3})\b(?!\s+of\b)",
    re.IGNORECASE,
)
# Revised edition marker, e.g. "2020 Ed." / "2020 Rev. Ed." / "2020 Revised Edition"
_REV_ED_RE = re.compile(r"\b(\d{4})\s*(?:Rev\.?\s*)?(?:Ed\.?|Edition)\b", re.IGNORECASE)

_MONTH_DATE = (
    r"(?:\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)\s+\d{4})"
)
_EFFECTIVE_AMENDMENT_RE = re.compile(
    rf"(?P<date>{_MONTH_DATE})\s+Amended\s+by\b", re.IGNORECASE
)
_AMENDED_BY_ACT_RE = re.compile(
    r"\bAmended\s+by\s+Act\s+[A-Z]?\d+\s+of\s+(?P<year>\d{4})\b",
    re.IGNORECASE,
)
_EXPLICIT_AMENDMENT_RE = re.compile(
    r"(?:latest\s+amendment\s+made\s+by|includes\s+amendments?\s*:|"
    r"incorporat(?:es|ing)\s+all\s+amendments\s+up\s+to(?:\s+and\s+including)?|"
    r"last\s+amended(?:\s+on)?)"
    r"(?P<context>.{0,260})",
    re.IGNORECASE | re.DOTALL,
)

# Words that mark a line as a legislation title across jurisdictions.
_TITLE_MARKERS = ("ACT", "CODE", "ORDINANCE", "DECREE", "LAW", "STATUTE",
                  "REGULATION", "RULES", "BILL")
# Cover-page boilerplate that contains a marker word but is NOT the title —
# includes gazette enactment formulae ("The following Act was passed…").
_TITLE_SKIP = ("STATUTES OF", "REVISED EDITION", "LAW REVISION", "REPUBLIC OF",
               "PREPARED AND PUBLISHED", "UNDER THE AUTHORITY", "TABLE OF",
               "ARRANGEMENT OF", "AN ACT TO", "CHAPTER", "FOLLOWING ACT",
               "PASSED BY PARLIAMENT", "ASSENTED TO", "BE IT ENACTED", "ENACTED BY")


def derive_act_title(text: str, source_url: str = "") -> str:
    """
    Best-effort act title from a statute's cover page, for when Zone 1 supplied
    none (e.g. URL-only seeds, NEW discoveries). Without a title the record's
    law_name is empty and the whole provision is dropped at validation.

    Scans the first lines for a short title line carrying a legislation marker
    word (ACT/CODE/ORDINANCE/...), skipping known boilerplate. Falls back to a
    readable slug from the URL so law_name is never empty. Economy-agnostic;
    returns "" only when there is genuinely nothing to use.
    """
    for raw in text.splitlines()[:25]:
        line = raw.strip()
        if not (3 < len(line) < 100):
            continue
        upper = line.upper()
        if any(skip in upper for skip in _TITLE_SKIP):
            continue
        if any(re.search(rf"\b{m}\b", upper) for m in _TITLE_MARKERS):
            # Normalise SHOUTING cover-page titles to Title Case.
            return line.title() if line.isupper() else line

    if source_url:
        slug = source_url.rstrip("/").split("/")[-1].split("?")[0]
        if slug:
            return slug.upper()
    return ""


def _year_from_version_url(source_url: str) -> Optional[str]:
    """Return a version year encoded by a government legislation URL."""
    try:
        parsed = urllib.parse.urlparse(source_url)
        q = urllib.parse.parse_qs(parsed.query)
    except Exception:  # noqa: BLE001
        return None
    raw = (q.get("DocDate") or q.get("ValidDate") or [None])[0]
    if raw and len(raw) >= 4 and raw[:4].isdigit():
        return raw[:4]

    # Australia FRL versioned PDFs repeat the compilation start date in the path:
    # /C2004A03712/2026-06-04/2026-06-04/text/original/pdf
    dates = re.findall(r"/(\d{4})-\d{2}-\d{2}(?=/)", parsed.path)
    if len(dates) >= 2 and dates[-1] == dates[-2]:
        return dates[-1]
    return None


def _title_year(act_title: str) -> Optional[str]:
    years = re.findall(r"\b(?:19|20)\d{2}\b", act_title or "")
    return years[-1] if years else None


def _is_amending_reference(text: str, start: int) -> bool:
    """True when an Act-of-year match is labelled as an amending instrument."""
    return bool(re.search(r"Amended\s+by\s*$", text[max(0, start - 40):start], re.IGNORECASE))


def _extract_law_number_ref(text: str, act_title: str) -> Optional[str]:
    """Extract the principal instrument's official number without using an amendment."""
    title_year = _title_year(act_title)

    # Singapore: history panels contain many amending Acts.  Prefer the
    # non-amending Act whose year matches the principal law's title year.
    act_of_year = list(_ACT_OF_YEAR_RE.finditer(text))
    candidates = [m for m in act_of_year if not _is_amending_reference(text, m.start())]
    if title_year:
        same_year = [m for m in candidates if m.group("year") == title_year]
        if same_year:
            # Legislative-history entries commonly place the number and title on
            # one line. Prefer that strongest association when the portal's main
            # history panel is outside the first screenful of a very long act.
            title_terms = {
                word.lower()
                for word in re.findall(r"[A-Za-z]{4,}", act_title or "")
                if word.lower() not in {"statutes", "online", "singapore"}
            }
            title_linked = []
            min_title_terms = 1 if len(title_terms) == 1 else 2
            for candidate in same_year:
                context = text[candidate.start():candidate.end() + 180].lower()
                if (
                    title_terms
                    and len(title_terms.intersection(re.findall(r"[a-z]{4,}", context)))
                    >= min_title_terms
                ):
                    title_linked.append(candidate)
            m = (title_linked or same_year)[0]
            return f"Act {m.group('number').upper()} of {m.group('year')}"
    elif candidates:
        m = candidates[0]
        return f"Act {m.group('number').upper()} of {m.group('year')}"

    # Australian compilation cover, e.g. "No. 12, 1995".
    m = _NO_COMMA_YEAR_RE.search(text)
    if m:
        return f"No. {m.group('number')}, {m.group('year')}"
    m = _NO_OF_YEAR_RE.search(text)
    if m:
        return f"No. {m.group('number')} of {m.group('year')}"
    m = _LAW_NO_RE.search(text)
    if m:
        return f"Law No. {m.group('number')}"
    m = _BE_REF_RE.search(text)
    if m:
        return f"B.E. {m.group('year')}"

    # A parenthesised reference in the supplied title is especially reliable.
    title_ref = re.search(r"\(\s*Act\s+([A-Z]?\d{1,4})\s*\)", act_title or "", re.IGNORECASE)
    if title_ref:
        return f"Act {title_ref.group(1).upper()}"

    # Malaysia cover pages put the principal reference immediately after
    # "LAWS OF MALAYSIA".  Searching only the opening cover block avoids later
    # cross-references such as "Companies Act 1965 [Act 125]".
    m = _SIMPLE_ACT_RE.search(text[:1000])
    if m:
        return f"Act {m.group('number').upper()}"
    return None


def _extract_last_amended(text: str, source_url: str) -> Optional[str]:
    """Extract the most recent evidenced amendment/effective year."""
    metadata_region = text[:100_000]

    # SSO history supplies the effective date immediately before "Amended by".
    effective_years = [
        re.search(r"\d{4}$", m.group("date")).group(0)  # type: ignore[union-attr]
        for m in _EFFECTIVE_AMENDMENT_RE.finditer(metadata_region)
    ]
    if effective_years:
        return max(effective_years)

    # Explicit cover statements used by AU/MY compilations and reprints.
    explicit_years: list[str] = []
    for m in _EXPLICIT_AMENDMENT_RE.finditer(metadata_region[:20_000]):
        explicit_years.extend(re.findall(r"\b(?:19|20)\d{2}\b", m.group("context")))
    if explicit_years:
        return max(explicit_years)

    # If an SSO-like history omits effective dates, the amending instrument year
    # is still explicit evidence and is safer than the page's generic "as at" date.
    amending_act_years = [
        m.group("year") for m in _AMENDED_BY_ACT_RE.finditer(metadata_region)
    ]
    if amending_act_years:
        return max(amending_act_years)

    # A version URL is a final fallback.  It is deliberately weaker than cover
    # and history evidence and yields only the schema-required year.
    return _year_from_version_url(source_url)


def extract_legislation_meta(
    text: str,
    source_url: str,
    act_title: str = "",
) -> tuple[Optional[str], Optional[str]]:
    """
    Return (law_number_ref, last_amended), each None when not confidently found.

    ``law_number_ref`` is optional. ``last_amended`` is a four-digit year and is
    left blank when neither the document nor a version URL establishes an
    amendment/compilation version.
    """
    document_text = text or ""
    head = document_text[:100_000]
    # Principal citations can live in a legislative-history appendix after a
    # very long body (e.g. Singapore Companies/Employment Acts), so scan the full
    # text for the reference while keeping amendment-date work bounded below.
    law_number_ref = _extract_law_number_ref(document_text, act_title)
    last_amended = _extract_last_amended(document_text, source_url)

    # A revised edition is a useful reference fallback, but its edition year is
    # not by itself proof of the most recent amendment year.
    if not law_number_ref:
        rev = _REV_ED_RE.search(head[:10_000])
        if rev:
            law_number_ref = f"{rev.group(1)} Rev. Ed."

    return law_number_ref, last_amended
