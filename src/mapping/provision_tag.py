"""
Provision-level discovery tag resolution. [86ey13cyh Decision 2]

resolve_provision_tag() — pure function for KNOWN/NEW at provision level.
infer_article_anchor()  — heuristic: article string → URL anchor fragment.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Optional

from src.crawler.crawler import _normalise_url as normalise_url
from src.crawler.seed_loader import normalise_title

# Query params that are PDF/HTML render artefacts, not part of a provision's
# identity. The pdf_endpoint fetch appends ?ViewType=Pdf, but known_provisions
# store the clean act URL — strip these before anchor matching so a genuinely
# known provision (e.g. Telecommunications Act s.3) is not mis-tagged NEW.
_RENDER_QUERY_PARAMS = {"viewtype"}


def _strip_render_params(url: str) -> str:
    parts = urllib.parse.urlparse(url)
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query)
            if k.lower() not in _RENDER_QUERY_PARAMS]
    return urllib.parse.urlunparse(parts._replace(query=urllib.parse.urlencode(kept)))


def infer_section_token(article: str) -> Optional[str]:
    """
    Extract the bare section number token from an article reference string,
    lowercased. This is the identity used for act+section KNOWN matching against
    Round 1's prose section citations (which give no anchor URL).

    Examples:
        "Section 26"    → "26"
        "s. 12A"        → "12a"
        "Section 11(3)" → "11"   (subparagraph is dropped)
    """
    m = re.search(r"\b(\d+[A-Za-z]?)", article)
    return m.group(1).lower() if m else None


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
    token = infer_section_token(article)
    return f"#pr{token}-" if token else None


def resolve_provision_tag(
    source_url: str,
    article_anchor: Optional[str],
    doc_discovery_tag: str,
    known_provisions: set[str],
    law_name: str = "",
    article: str = "",
    known_sections: "dict[str, set[str]] | None" = None,
) -> tuple[str, bool]:
    """
    Resolve discovery_tag at provision level.

    A provision is KNOWN when Round 1 documented it and the run found it — matched
    two ways, either satisfies KNOWN (indicator-agnostic, per the Round 2 rubric):
      1. anchor-URL match: the reconstructed act-URL + #anchor is in
         ``known_provisions`` (Round 1 References that carried a #section anchor).
      2. act+section match: (normalised act title, section number) is in
         ``known_sections`` — this is what catches Round 1's *prose* citations
         ("Section 199", "Section 11(3)") which have no anchor URL at all and are
         the common case for Pillar 7.

    Returns (tag, flag_unresolvable) where flag_unresolvable is True only when we
    had neither an anchor nor a section token to test and fell back to doc tag.
    """
    if doc_discovery_tag == "NEW":
        return "NEW", False

    section_token = infer_section_token(article) if article else None

    known_by_section = bool(
        known_sections and section_token and law_name
        and section_token in known_sections.get(normalise_title(law_name), set())
    )

    known_by_anchor = False
    if article_anchor:
        candidate = normalise_url(_strip_render_params(source_url) + article_anchor)
        known_by_anchor = candidate in known_provisions

    if known_by_section or known_by_anchor:
        return "KNOWN", False

    # No positive match. If we had neither an anchor nor a section token, we
    # couldn't actually test the provision → fall back to the act's doc-level tag.
    if not article_anchor and not section_token:
        return doc_discovery_tag, True

    return "NEW", False
