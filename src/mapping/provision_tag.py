"""
Provision-level discovery tag resolution. [86ey13cyh Decision 2]

resolve_provision_tag() — pure function for KNOWN/NEW at provision level.
infer_article_anchor()  — heuristic: article string → URL anchor fragment.
"""

from __future__ import annotations

import re
from typing import Optional

from src.crawler.crawler import _normalise_url as normalise_url


def infer_article_anchor(article: str) -> Optional[str]:
    """
    Infer a URL anchor fragment from an article reference string.

    Examples:
        "Section 26"   → "#pr26-"
        "Art. 26(2)"   → "#pr26-"
        "s. 12A"       → "#pr12a-"
        "Reg. 4(1)(b)" → "#pr4-"

    Returns None if no section number can be reliably extracted.
    """
    m = re.search(r"\b(\d+[A-Za-z]?)", article)
    if not m:
        return None
    raw = m.group(1)
    return f"#pr{raw.lower()}-"


def resolve_provision_tag(
    source_url: str,
    article_anchor: Optional[str],
    doc_discovery_tag: str,
    known_provisions: set[str],
) -> tuple[str, bool]:
    """
    Resolve discovery_tag at provision level.

    Returns (tag, flag_unresolvable) where:
      - tag is "KNOWN" or "NEW"
      - flag_unresolvable is True when no anchor could be inferred and
        we fell back to doc-level tag
    """
    if doc_discovery_tag == "NEW":
        return "NEW", False

    if not article_anchor:
        return doc_discovery_tag, True  # fallback + flag

    candidate = normalise_url(source_url + article_anchor)
    tag = "KNOWN" if candidate in known_provisions else "NEW"
    return tag, False
