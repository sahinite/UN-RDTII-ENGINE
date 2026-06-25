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
