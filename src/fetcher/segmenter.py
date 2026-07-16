"""
Consolidated volume segmentation via PyMuPDF.

Splits multi-act consolidated volumes by act boundary before extraction.
Translation is handled by src/fetcher/translator.py.

# LICENCE-NOTE: PyMuPDF (AGPL) — single controlled point of use, pending
# replacement with a permissively-licensed alternative per tech plan.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.fetcher.logger import get_logger
from src.fetcher.models import ActSegment

if TYPE_CHECKING:
    from src.config.economy_config import EconomyConfig

logger = get_logger("segmenter")

# ── Act header patterns ────────────────────────────────────────────────────────

ACT_HEADER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(Act|Law|Ordinance|Chapter|Regulation|Order|Notification)\s+[\d]+", re.IGNORECASE),
    re.compile(r"^(พระราชบัญญัติ|กฎกระทรวง)"),
    re.compile(r"^(Undang-Undang|Peraturan)\s+", re.IGNORECASE),
]

_MIN_SEGMENT_PAGES = 3
_FIXED_FALLBACK_SIZE = 50


def _matches_act_header(text: str) -> bool:
    for pattern in ACT_HEADER_PATTERNS:
        if pattern.search(text.strip()):
            return True
    return False


# ── Boundary detection ─────────────────────────────────────────────────────────

def find_act_boundaries(raw_bytes: bytes, extra_patterns: list[str] | None = None) -> list[int]:
    """
    Returns 0-based page indices where a new act starts.
    Always includes page 0 as the first boundary.
    """
    try:
        import fitz
    except ImportError as exc:
        from src.fetcher.extractors.ocr_stage1 import DependencyError
        raise DependencyError("PyMuPDF not installed; run: pip install pymupdf") from exc

    extra_compiled: list[re.Pattern[str]] = []
    if extra_patterns:
        for p in extra_patterns:
            try:
                extra_compiled.append(re.compile(p, re.IGNORECASE))
            except re.error:
                pass

    doc = fitz.open(stream=raw_bytes, filetype="pdf")
    total_pages = len(doc)
    boundaries: list[int] = [0]

    for page_idx in range(total_pages):
        page = doc[page_idx]
        page_height = page.rect.height
        blocks = page.get_text("dict")["blocks"]  # type: ignore[attr-defined]

        for block in blocks:
            if block.get("type") != 0:  # text block
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    font_size = span.get("size", 0)
                    text = span.get("text", "").strip()
                    origin_y = span.get("origin", [0, 0])[1]

                    if font_size < 14 or not text:
                        continue
                    # Must be in top 20% of page
                    if origin_y > page_height * 0.20:
                        continue

                    if _matches_act_header(text):
                        if page_idx not in boundaries:
                            boundaries.append(page_idx)
                    else:
                        for pat in extra_compiled:
                            if pat.search(text):
                                if page_idx not in boundaries:
                                    boundaries.append(page_idx)

    return sorted(boundaries)


# ── PDF slicing ────────────────────────────────────────────────────────────────

def slice_pdf(raw_bytes: bytes, boundaries: list[int]) -> list[bytes]:
    try:
        import fitz
    except ImportError as exc:
        from src.fetcher.extractors.ocr_stage1 import DependencyError
        raise DependencyError("PyMuPDF not installed; run: pip install pymupdf") from exc

    doc = fitz.open(stream=raw_bytes, filetype="pdf")
    total = len(doc)
    slices: list[bytes] = []

    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else total
        sub = fitz.open()
        sub.insert_pdf(doc, from_page=start, to_page=end - 1)
        slices.append(sub.tobytes())

    return slices


# ── Title extraction ───────────────────────────────────────────────────────────

def extract_segment_title(segment_bytes: bytes, fallback: str = "Unknown Act") -> str:
    try:
        import fitz
        doc = fitz.open(stream=segment_bytes, filetype="pdf")
        if len(doc) == 0:
            return fallback
        page = doc[0]
        blocks = page.get_text("dict")["blocks"]  # type: ignore[attr-defined]
        best_text = ""
        best_size = 0.0
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = span.get("size", 0)
                    text = span.get("text", "").strip()
                    if size > best_size and text:
                        best_size = size
                        best_text = text
        return best_text or fallback
    except Exception:
        return fallback


# ── Main entry point ───────────────────────────────────────────────────────────

def segment_volume(raw_bytes: bytes, economy_config: "EconomyConfig") -> list[ActSegment]:
    try:
        import fitz
        doc = fitz.open(stream=raw_bytes, filetype="pdf")
        total_pages = len(doc)
    except Exception as exc:
        from src.fetcher.extractors.ocr_stage1 import DependencyError
        raise DependencyError("PyMuPDF not installed; run: pip install pymupdf") from exc

    extra_patterns = getattr(economy_config, "volume_header_patterns", [])
    source_url = ""  # Populated by router when calling

    boundaries = find_act_boundaries(raw_bytes, extra_patterns=extra_patterns or [])

    # Fallback: no meaningful boundaries found
    boundary_fallback = False
    if len(boundaries) <= 1:
        logger.info({
            "event": "boundary_fallback",
            "boundary_fallback": True,
            "method": "fixed_50_pages",
            "total_pages": total_pages,
            "url": source_url,
            "economy": economy_config.economy_name,
        })
        boundary_fallback = True
        boundaries = list(range(0, total_pages, _FIXED_FALLBACK_SIZE))

    sliced = slice_pdf(raw_bytes, boundaries)
    segments: list[ActSegment] = []
    seg_idx = 0

    for i, (start_page, seg_bytes) in enumerate(zip(boundaries, sliced)):
        end_page = (boundaries[i + 1] - 1) if i + 1 < len(boundaries) else (total_pages - 1)
        seg_pages = end_page - start_page + 1

        # Merge very short segments into previous
        if seg_pages < _MIN_SEGMENT_PAGES and segments:
            prev = segments[-1]
            # Extend previous segment's raw_bytes
            try:
                import fitz
                merged = fitz.open()
                merged.insert_pdf(fitz.open(stream=prev.raw_bytes, filetype="pdf"))
                merged.insert_pdf(fitz.open(stream=seg_bytes, filetype="pdf"))
                segments[-1] = ActSegment(
                    segment_index=prev.segment_index,
                    act_title=prev.act_title,
                    start_page=prev.start_page,
                    end_page=end_page,
                    raw_bytes=merged.tobytes(),
                    economy=economy_config.economy_name,
                    source_url=source_url,
                )
                logger.info({
                    "event": "short_segment_merged",
                    "short_segment_merged": True,
                    "pages": seg_pages,
                    "url": source_url,
                    "economy": economy_config.economy_name,
                })
            except Exception:
                pass
            continue

        title = extract_segment_title(seg_bytes, fallback=f"Segment {seg_idx}")
        if boundary_fallback:
            title = f"Segment {seg_idx}"

        segments.append(ActSegment(
            segment_index=seg_idx,
            act_title=title,
            start_page=start_page,
            end_page=end_page,
            raw_bytes=seg_bytes,
            economy=economy_config.economy_name,
            source_url=source_url,
        ))
        seg_idx += 1

        logger.info({
            "event": "segment_completed",
            "source_url": source_url,
            "segment_index": seg_idx - 1,
            "pages": seg_pages,
            "url": source_url,
            "economy": economy_config.economy_name,
        })

    if len(segments) == 1:
        logger.info({
            "event": "single_act_in_volume",
            "single_act_in_volume": True,
            "url": source_url,
            "economy": economy_config.economy_name,
        })

    logger.info({
        "event": "volume_segmented",
        "source_url": source_url,
        "total_pages": total_pages,
        "segments_found": len(segments),
        "boundaries": boundaries,
        "url": source_url,
        "economy": economy_config.economy_name,
    })
    return segments
