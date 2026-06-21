"""
Article-level chunking with legal boundary detection. [Z2-3 ST1]

chunk_document() accepts a TranslatedDocument (from Z2-2) or a plain FetchedDocument
and returns a list[Chunk], one per article/section.  Every Chunk carries a
LocationReference so downstream citations are verifiable.

Splitting strategy (in priority order):
1. Use section_hierarchy if it contains ≥2 entries with meaningful text → each
   entry with non-empty "text" becomes one Chunk.
2. Fall back to regex-based article boundary splitting on raw_text when
   section_hierarchy is thin (common for OCR'd PDFs).
3. If the document is very short (< 3 paragraphs), treat the whole text as a
   single chunk so we never return an empty list.
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Union

from src.retrieval.models import Chunk, LocationReference

if TYPE_CHECKING:
    from src.fetcher.models import FetchedDocument, TranslatedDocument

# ── Article boundary regexes (covers SG PDPA-style and generic English acts) ──

_ARTICLE_BOUNDARY = re.compile(
    r"""
    (?:^|\n)                            # start of string or newline
    (?:
        (?:Section|Article|Regulation|Rule|Clause)\s+(\d+[A-Z]?(?:\(\d+\))?)[.\s—–-] |
        ^(\d+[A-Z]?)\.\s+[A-Z]         # "12.  Heading…" at line start
    )
    """,
    re.VERBOSE | re.IGNORECASE | re.MULTILINE,
)

_PART_HEADING = re.compile(
    r"^(PART|CHAPTER|DIVISION|SCHEDULE)\s+([IVXivx]+|\d+)(?:\s[—–-]\s*(.+))?",
    re.IGNORECASE | re.MULTILINE,
)

_MIN_CHUNK_CHARS = 80       # discard fragments shorter than this
_MAX_CHUNK_CHARS = 6_000    # split oversized chunks by paragraph


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


def _split_text_by_regex(raw_text: str) -> list[tuple[str, str]]:
    """
    Returns [(article_number, text)] pairs by regex-splitting raw_text at article headers.
    """
    splits: list[tuple[str, str]] = []
    positions: list[tuple[int, str]] = []

    for m in _ARTICLE_BOUNDARY.finditer(raw_text):
        art_num = (m.group(1) or m.group(2) or "").strip()
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


def _split_oversize(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    """Split a chunk that's too large by paragraph boundary."""
    if len(text) <= max_chars:
        return [text]
    paragraphs = re.split(r"\n{2,}", text)
    parts: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                parts.append(current)
            current = para
    if current:
        parts.append(current)
    return parts or [text[:max_chars]]


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

    # Article references from Z2-2 (if available)
    article_refs = getattr(doc, "article_references", [])

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

            for sub_text in _split_oversize(entry_text):
                if len(sub_text) < _MIN_CHUNK_CHARS:
                    continue
                chunk_id = _make_chunk_id(act_title, loc.article_number, seq)
                chunks.append(Chunk(
                    chunk_id=chunk_id,
                    text=sub_text,
                    location_reference=loc,
                    doc_source_url=source_url,
                ))
                seq += 1

        if chunks:
            return chunks

    # ── Strategy 2: regex-based article splitting on raw_text ─────────────────
    # Build a lookup from article_number → ArticleReference for provenance
    ref_by_num: dict[str, object] = {}
    for ref in article_refs:
        ref_by_num[ref.article_number] = ref

    current_part = ""
    for art_num, art_text in _split_text_by_regex(text):
        for sub_text in _split_oversize(art_text):
            if len(sub_text) < _MIN_CHUNK_CHARS:
                continue
            # Detect part context from text
            part_m = _PART_HEADING.search(sub_text[:200])
            if part_m:
                current_part = f"{part_m.group(1).upper()} {part_m.group(2).upper()}"

            # Look up page from ArticleReference if available
            ref = ref_by_num.get(art_num)
            page = getattr(ref, "page", None) if ref else None

            loc = LocationReference(
                act_title=act_title,
                part=current_part,
                article_number=art_num,
                page=page,
            )
            chunk_id = _make_chunk_id(act_title, art_num, seq)
            chunks.append(Chunk(
                chunk_id=chunk_id,
                text=sub_text,
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
