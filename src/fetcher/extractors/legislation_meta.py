"""
Legislation citation metadata extractor. [PRD: 86ey22k30 — fix #2]

Parses `law_number_ref` (act number / revised edition) and `last_amended`
(version date / revised-edition year) from a statute's cover-page text and its
source URL. Designed against Singapore Statutes Online (SSO) conventions but
written defensively so it returns None rather than guessing on other portals.

SSO conventions used:
  - Acts carry "Act <n> of <year>" and a revised edition "<year> Ed." on the cover.
  - Document URLs carry the version date as ?DocDate=YYYYMMDD (or ?ValidDate=...).
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Optional

# "Act 26 of 2012"
_ACT_NO_RE = re.compile(r"\bAct\s+(\d+)\s+of\s+(\d{4})\b", re.IGNORECASE)
# Revised edition marker, e.g. "2020 Ed." / "2020 Rev. Ed." / "2020 Revised Edition"
_REV_ED_RE = re.compile(r"\b(\d{4})\s*(?:Rev\.?\s*)?(?:Ed\.?|Edition)\b", re.IGNORECASE)

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


def _docdate_from_url(source_url: str) -> Optional[str]:
    """Return version date from ?DocDate / ?ValidDate as YYYY-MM-DD (or YYYY)."""
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(source_url).query)
    except Exception:  # noqa: BLE001
        return None
    raw = (q.get("DocDate") or q.get("ValidDate") or [None])[0]
    if not raw or not raw[:4].isdigit():
        return None
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw[:4]


def extract_legislation_meta(
    text: str,
    source_url: str,
) -> tuple[Optional[str], Optional[str]]:
    """
    Return (law_number_ref, last_amended), each None when not confidently found.

    last_amended prefers the document's version date from the URL (most precise),
    then falls back to the revised-edition year on the cover.
    """
    head = (text or "")[:4000]  # citation data lives on the cover / first page
    law_number_ref: Optional[str] = None
    last_amended: Optional[str] = None

    m = _ACT_NO_RE.search(head)
    if m:
        law_number_ref = f"Act {m.group(1)} of {m.group(2)}"

    rev = _REV_ED_RE.search(head)
    if rev:
        last_amended = rev.group(1)
        if not law_number_ref:
            law_number_ref = f"{rev.group(1)} Rev. Ed."

    # A URL version date is the strongest "as-of/last-amended" signal — prefer it.
    docdate = _docdate_from_url(source_url)
    if docdate:
        last_amended = docdate

    return law_number_ref, last_amended
