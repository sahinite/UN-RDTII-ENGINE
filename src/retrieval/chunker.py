"""
Subsection-aware chunking with legal boundary detection.

chunk_document() accepts a TranslatedDocument (from Z2-2) or a plain FetchedDocument
and returns a list[Chunk].  Every Chunk carries a LocationReference so downstream
citations are verifiable.

Splitting strategy (in priority order):
1. Use section_hierarchy if it contains ≥2 entries with meaningful text → each
   entry's text is split to target size at subsection boundaries.
2. Fall back to regex-based article boundary splitting on raw_text when
   section_hierarchy is thin (common for OCR'd PDFs).
3. If the document is very short, treat the whole text as a single chunk so we
   never return an empty list.

Why subsection-aware (vs one chunk per section): a section like PDPA s.11 packs
its DPO clause s.11(3) inside a ~3.6k-char block. As one chunk, that clause's
signal is diluted, the embedding ranks low, and the prompt-budget trim drops it
before the LLM ever sees it. Splitting each section into ~1.2k-char,
subsection-preferring chunks — each prefixed with its parent heading so the
embedding carries the provision's location — gives buried clauses a sharp,
retrievable embedding. The number of chunks reaching the LLM stays bounded by
RERANK_TOP_N (the reranker, not chunk size, is the gatekeeper).
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Union

from src.retrieval.models import Chunk, LocationReference

if TYPE_CHECKING:
    from src.fetcher.models import FetchedDocument, TranslatedDocument

# ── Article boundary regexes (covers SG PDPA-style and generic English acts) ──

_ARTICLE_BOUNDARY = re.compile(
    r"""
    (?:^|\n)                            # start of string or newline
    (?:
        (?:Section|Article|Regulation|Rule|Clause)\s+(\d+[A-Z]{0,3}(?:\(\d+\))?)[.\s—–-] |
        ^(\d+[A-Z]{0,3})\.\s+[A-Z(—–-]  |  # "12.  Heading…" or SSO whole-doc "26.⏎—(1)…" (period style)
        ^(\d+[A-Z]{0,3})\ +[A-Z]          # "6A  Heading…"   (AU compilation / space style)
    )
    """,
    re.VERBOSE | re.IGNORECASE | re.MULTILINE,
)

_PART_HEADING = re.compile(
    r"^(PART|CHAPTER|DIVISION|SCHEDULE)\s+([IVXivx]+|\d+)(?:\s[—–-]\s*(.+))?",
    re.IGNORECASE | re.MULTILINE,
)

# Split a section's body before a numbered subsection marker — (1), (2), (3A) —
# but ONLY when the marker follows a sentence terminator or line break. This
# splits real subsections while leaving inline cross-references like "s. 5(1)"
# intact (those are preceded by a digit, not by [.;:\n]).
_SUBSECTION_BOUNDARY = re.compile(r"(?<=[.;:\n])\s*(?=\(\d+[A-Za-z]?\)\s)")

_MIN_CHUNK_CHARS = 80                                              # discard fragments shorter than this
_TARGET_CHUNK_CHARS = int(os.getenv("CHUNK_TARGET_CHARS", "1200"))  # preferred chunk size (chars)
_MAX_CHUNK_CHARS = int(os.getenv("CHUNK_MAX_CHARS", "1800"))        # hard cap → force-split oversized units

# When section_hierarchy chunking retains less than this fraction of the source
# text, the hierarchy is untrustworthy (e.g. AU legislation.gov.au compilations,
# whose running page-headers masquerade as Part/Division entries so real section
# bodies get dropped). Below the threshold we discard the hierarchy result and
# fall back to raw-text regex splitting, which recovers the full body.
_HIERARCHY_MIN_COVERAGE = float(os.getenv("CHUNK_HIERARCHY_MIN_COVERAGE", "0.5"))

# ── Page-furniture / table-of-contents noise (repeats every page in PDFs) ──────
# These lines are never citable provision text. Left in, they (a) let the dense,
# keyword-rich TOC out-rank real body prose in retrieval and (b) fragment sections
# at every page break (the "Section 3A" running header is a false article split).
_TOC_LEADER = re.compile(r"\.{4,}\s*\d+\s*$")               # "Short title ......... 12"
_RUNNING_SECTION_HDR = re.compile(r"^Section\s+\d+[A-Z]{0,3}$", re.IGNORECASE)
_COMPILATION_FURNITURE = re.compile(
    r"^(Compilation No\.|Compilation date|Includes amendments|Authorised Version|"
    r"Prepared by the Office|Registered:|About this compilation|No table of contents)",
    re.IGNORECASE,
)


def _strip_page_furniture(text: str, act_title: str = "") -> str:
    """Remove repeating page headers/footers and TOC leader lines from raw text.

    Only used on the raw-text (regex) chunking path, where hierarchy chunking has
    been rejected — for PDFs whose per-page furniture would otherwise pollute the
    chunk set. Blank lines are preserved so paragraph structure survives.
    """
    act = act_title.strip()
    kept: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            kept.append(line)
            continue
        if _TOC_LEADER.search(s) or _RUNNING_SECTION_HDR.match(s) or _COMPILATION_FURNITURE.match(s):
            continue
        if act:
            if s == act:
                continue
            # page footer "<n> <Act Title>" or header "<Act Title> <n>"
            m = re.match(r"^(\d{1,4})\s+(.*)$", s)
            if m and m.group(2).strip() == act:
                continue
            m = re.match(r"^(.*?)\s+(\d{1,4})$", s)
            if m and m.group(1).strip() == act:
                continue
        kept.append(line)
    return "\n".join(kept)


def _make_chunk_id(act_title: str, article_number: str, seq: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", act_title.lower())[:40]
    art = re.sub(r"[^a-z0-9]+", "_", article_number.lower()) if article_number else str(seq)
    return f"{slug}__{art}__{seq:04d}"


def _location_from_hierarchy(
    entry: dict,
    act_title: str,
    current_part: str,
) -> LocationReference:
    title: str = (entry.get("title") or "").strip()
    # Extract article number from title
    num = ""
    m = re.match(r"^(\d+[A-Z]?(?:\(\d+\))?)[.\s]", title)
    if m:
        num = m.group(1)
    else:
        m2 = re.match(
            r"^(?:Section|Article|Regulation|Rule|Clause|s\.)\s+(\d+[A-Z]?(?:\(\d+\))?)",
            title, re.IGNORECASE,
        )
        if m2:
            num = m2.group(1)
    return LocationReference(
        act_title=act_title,
        part=current_part,
        article_number=num,
        page=entry.get("page"),
    )


def _split_text_by_regex(raw_text: str, act_title: str = "") -> list[tuple[str, str]]:
    """
    Returns [(article_number, text)] pairs by regex-splitting raw_text at article headers.

    Strips repeating page furniture/TOC noise first (act_title enables footer
    detection) so section boundaries are not fragmented by per-page running headers.
    """
    raw_text = _strip_page_furniture(raw_text, act_title)

    splits: list[tuple[str, str]] = []
    positions: list[tuple[int, str]] = []

    for m in _ARTICLE_BOUNDARY.finditer(raw_text):
        art_num = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        positions.append((m.start(), art_num))

    if not positions:
        return [("", raw_text)]

    # First block: preamble before first article
    preamble = raw_text[: positions[0][0]].strip()
    if len(preamble) >= _MIN_CHUNK_CHARS:
        splits.append(("preamble", preamble))

    for i, (start, art_num) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(raw_text)
        text = raw_text[start:end].strip()
        splits.append((art_num, text))

    return splits


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Last-resort split of an oversized unit by paragraph, then by sentence."""
    if len(text) <= max_chars:
        return [text]
    out: list[str] = []
    for para in re.split(r"\n{2,}", text):
        if len(para) <= max_chars:
            if para.strip():
                out.append(para.strip())
            continue
        current = ""
        for sent in re.split(r"(?<=[.;])\s+", para):
            if len(current) + len(sent) + 1 <= max_chars:
                current = (current + " " + sent).strip()
            else:
                if current:
                    out.append(current)
                current = sent
        if current:
            out.append(current)
    return out or [text[:max_chars]]


def _split_to_target(
    text: str,
    target: int = _TARGET_CHUNK_CHARS,
    max_chars: int = _MAX_CHUNK_CHARS,
) -> list[str]:
    """
    Split a section body into ~target-sized chunks at subsection boundaries.

    Sections at or below `target` stay whole. Larger sections are broken at
    numbered-subsection markers and the resulting units greedily packed up to
    `target`; any single unit exceeding `max_chars` is hard-split further. This
    keeps a buried subsection (e.g. a DPO clause) in a small, sharply-embedded
    chunk rather than diluted inside one large section block.
    """
    text = text.strip()
    if len(text) <= target:
        return [text] if text else []

    units: list[str] = []
    for unit in _SUBSECTION_BOUNDARY.split(text):
        unit = unit.strip()
        if not unit:
            continue
        if len(unit) > max_chars:
            units.extend(_hard_split(unit, max_chars))
        else:
            units.append(unit)

    chunks: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
        elif len(current) + len(unit) + 1 <= target:
            current = current + "\n" + unit
        else:
            chunks.append(current)
            current = unit
    if current:
        chunks.append(current)
    return chunks


def _heading_prefix(act_title: str, part: str, article_number: str, title: str = "") -> str:
    """One-line parent-heading prefix so each subsection chunk carries its location."""
    bits = [act_title.strip()]
    if part:
        bits.append(part.strip())
    if title:
        bits.append(title.strip())
    elif article_number:
        bits.append(f"Section {article_number}")
    return " — ".join(b for b in bits if b)


def _prefixed(prefix: str, body: str) -> str:
    """Prepend the heading prefix unless the body already opens with it."""
    body = body.strip()
    if prefix and not body.startswith(prefix):
        return f"{prefix}\n\n{body}"
    return body


def chunk_document(
    doc: Union["FetchedDocument", "TranslatedDocument"],
) -> list[Chunk]:
    """
    Split a document into article-level Chunks with location references.

    Accepts both FetchedDocument and TranslatedDocument (unwraps .fetched).
    Returns at least one Chunk even for very short documents.
    """
    # Unwrap TranslatedDocument (check isinstance to avoid MagicMock pollution)
    try:
        from src.fetcher.models import TranslatedDocument as _TD
        is_translated = isinstance(doc, _TD)
    except ImportError:
        is_translated = False

    if is_translated:
        fetched = doc.fetched  # type: ignore[union-attr]
        text: str = doc.translated_text or fetched.raw_text  # type: ignore[union-attr]
        act_title: str = doc.act_title_translated or fetched.act_title  # type: ignore[union-attr]
    else:
        fetched = doc
        text = fetched.raw_text
        act_title = fetched.act_title
    source_url: str = fetched.source_url
    hierarchy: list[dict] = fetched.section_hierarchy or []

    chunks: list[Chunk] = []
    seq = 0

    # ── Strategy 1: section_hierarchy with embedded text ──────────────────────
    hierarchy_with_text = [
        e for e in hierarchy
        if (e.get("text") or "").strip() and len((e.get("text") or "").strip()) >= _MIN_CHUNK_CHARS
    ]

    if len(hierarchy_with_text) >= 2:
        current_part = ""
        for entry in hierarchy_with_text:
            title: str = (entry.get("title") or "").strip()
            part_m = _PART_HEADING.match(title)
            if part_m:
                current_part = f"{part_m.group(1).upper()} {part_m.group(2).upper()}"
                continue

            entry_text = (entry.get("text") or "").strip()
            loc = _location_from_hierarchy(entry, act_title, current_part)
            prefix = _heading_prefix(act_title, current_part, loc.article_number, title)

            for sub_text in _split_to_target(entry_text):
                if len(sub_text) < _MIN_CHUNK_CHARS:
                    continue
                chunk_id = _make_chunk_id(act_title, loc.article_number, seq)
                chunks.append(Chunk(
                    chunk_id=chunk_id,
                    text=_prefixed(prefix, sub_text),
                    location_reference=loc,
                    doc_source_url=source_url,
                ))
                seq += 1

        # Accept the hierarchy result only if it retained most of the document.
        # A low ratio means the hierarchy is untrustworthy (running page-headers
        # parsed as Part/Division entries, real section bodies dropped) — fall
        # through to raw-text splitting instead of shipping a 3%-coverage chunk set.
        covered = sum(len(c.text) for c in chunks)
        if chunks and covered >= _HIERARCHY_MIN_COVERAGE * len(text):
            return chunks
        chunks = []
        seq = 0

    # ── Strategy 2: regex-based article splitting on raw_text ─────────────────
    current_part = ""
    for art_num, art_text in _split_text_by_regex(text, act_title):
        # Detect part context once per section (from its head), not per sub-chunk.
        part_m = _PART_HEADING.search(art_text[:200])
        if part_m:
            current_part = f"{part_m.group(1).upper()} {part_m.group(2).upper()}"

        loc = LocationReference(
            act_title=act_title,
            part=current_part,
            article_number=art_num,
        )
        prefix = _heading_prefix(act_title, current_part, art_num)

        for sub_text in _split_to_target(art_text):
            if len(sub_text) < _MIN_CHUNK_CHARS:
                continue
            chunk_id = _make_chunk_id(act_title, art_num, seq)
            chunks.append(Chunk(
                chunk_id=chunk_id,
                text=_prefixed(prefix, sub_text),
                location_reference=loc,
                doc_source_url=source_url,
            ))
            seq += 1

    # ── Strategy 3: whole-document fallback ───────────────────────────────────
    if not chunks:
        chunks.append(Chunk(
            chunk_id=_make_chunk_id(act_title, "", 0),
            text=text.strip() or "(empty document)",
            location_reference=LocationReference(
                act_title=act_title, part="", article_number="", page=None,
            ),
            doc_source_url=source_url,
        ))

    return chunks
