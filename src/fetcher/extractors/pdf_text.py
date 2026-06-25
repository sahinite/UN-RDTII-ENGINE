"""
Text-native PDF extraction via pdfplumber. [Z2-1 ST2]

Called by router.py when doc_type == TEXT_PDF.
Reclassifies to SCANNED_PDF and raises ReclassifyToScannedError
when >30% of pages yield empty text.
"""

from __future__ import annotations

import io
import re
import time
from typing import TYPE_CHECKING

import pdfplumber

from src.fetcher.extractors.legislation_meta import extract_legislation_meta
from src.fetcher.logger import get_logger
from src.fetcher.models import CostLogEntry, FetchedDocument

if TYPE_CHECKING:
    from src.config.economy_config import EconomyConfig
    from src.fetcher.models import Zone1Result

logger = get_logger("pdf_text")


# ── Custom exceptions ──────────────────────────────────────────────────────────

class ExtractionError(Exception):
    pass


class ReclassifyToScannedError(Exception):
    """Raised when a TEXT_PDF turns out to be mostly scanned."""
    def __init__(self, url: str, pages_with_text: int, total: int) -> None:
        self.url = url
        self.pages_with_text = pages_with_text
        self.total = total
        super().__init__(f"Reclassified as SCANNED_PDF ({pages_with_text}/{total} pages had text): {url}")


# ── Section hierarchy ──────────────────────────────────────────────────────────

_L1_PATTERN = re.compile(r"^(PART|CHAPTER|DIVISION)\s+[IVX\d]+", re.IGNORECASE)
_L2_PATTERN = re.compile(r"^\d+\.\s+[A-Z]")
_L3_PATTERN = re.compile(r"^\d+[A-Z]\.\s+")


def extract_section_hierarchy(full_text: str) -> list[dict]:
    sections: list[dict] = []
    current: dict | None = None
    current_lines: list[str] = []
    current_level = 0

    for line in full_text.splitlines():
        stripped = line.strip()
        if not stripped:
            if current is not None:
                current_lines.append("")
            continue

        level = 0
        if stripped.isupper() or _L1_PATTERN.match(stripped):
            level = 1
        elif _L2_PATTERN.match(stripped):
            level = 2
        elif _L3_PATTERN.match(stripped):
            level = 3

        if level and (current is None or level <= current_level):
            if current is not None:
                current["text"] = "\n".join(current_lines).strip()
                sections.append(current)
            current = {"level": level, "title": stripped, "text": "", "page": None}
            current_lines = []
            current_level = level
        else:
            if current is not None:
                current_lines.append(stripped)

    if current is not None:
        current["text"] = "\n".join(current_lines).strip()
        sections.append(current)

    return sections


# ── Main extractor ─────────────────────────────────────────────────────────────

def extract_text_pdf(
    raw_bytes: bytes,
    zone1_result: "Zone1Result",
    economy_config: "EconomyConfig",
) -> FetchedDocument:
    start = time.monotonic()
    table_pages: list[int] = []

    try:
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            page_count = len(pdf.pages)
            pages_text: list[dict] = []
            empty_count = 0

            for i, page in enumerate(pdf.pages):
                text = page.extract_text(x_tolerance=3, y_tolerance=3) or ""

                # Table extraction
                tables = page.extract_tables()
                if tables:
                    table_pages.append(i + 1)
                    logger.debug({"event": "table_found", "page": i + 1, "url": zone1_result.url})
                    for table in tables:
                        rows = ["\t".join(str(cell or "") for cell in row) for row in table]
                        text += "\n" + "\n".join(rows)

                if text.strip():
                    pages_text.append({"page": i + 1, "text": text})
                else:
                    empty_count += 1

    except Exception as exc:
        exc_name = type(exc).__name__
        if "password" in exc_name.lower() or "Password" in str(exc):
            from src.fetcher.router import DownloadError
            raise DownloadError(f"PDF is password-protected: {zone1_result.url}") from exc
        raise ExtractionError(f"Failed to extract PDF: {zone1_result.url} — {exc}") from exc

    # Reclassify if >30% pages have no text
    if page_count > 0 and (empty_count / page_count) > 0.30:
        logger.warning({
            "event": "reclassified_to_scanned",
            "url": zone1_result.url,
            "pages_with_text": page_count - empty_count,
            "total_pages": page_count,
            "economy": zone1_result.economy,
        })
        raise ReclassifyToScannedError(zone1_result.url, page_count - empty_count, page_count)

    full_text = "\n\n".join(p["text"] for p in pages_text)
    elapsed_ms = (time.monotonic() - start) * 1000

    if full_text and len(full_text) < 100 and page_count > 1:
        logger.warning({
            "event": "low_text_yield",
            "url": zone1_result.url,
            "text_length": len(full_text),
            "economy": zone1_result.economy,
        })

    section_hierarchy = extract_section_hierarchy(full_text)
    law_number_ref, last_amended = extract_legislation_meta(full_text, zone1_result.url)

    cost_log = CostLogEntry(
        engine="pdfplumber",
        pages=page_count,
        cost_usd=0.0,
        processing_time_ms=elapsed_ms,
        table_found=bool(table_pages),
        table_pages=table_pages,
    )

    doc = FetchedDocument(
        source_url=zone1_result.url,
        resolved_url=zone1_result.url,
        economy=zone1_result.economy,
        act_title=zone1_result.act_title,
        discovery_tag=zone1_result.discovery_tag,  # type: ignore[arg-type]
        archive_url=zone1_result.archive_url,
        doc_type="TEXT_PDF",
        extraction_method="pdfplumber",
        page_count=page_count,
        raw_text=full_text,
        section_hierarchy=section_hierarchy,
        flag_for_review=len(full_text) < 100 and page_count > 1,
        flag_reason="low_text_yield" if len(full_text) < 100 and page_count > 1 else None,
        cost_log_entry=cost_log,
        law_number_ref=law_number_ref,
        last_amended=last_amended,
    )
    doc.validate()

    logger.info({
        "event": "fetch_document_validated",
        "url": zone1_result.url,
        "extraction_method": "pdfplumber",
        "text_length": len(full_text),
        "flag_for_review": doc.flag_for_review,
        "economy": zone1_result.economy,
    })
    return doc
