"""
Output models and exceptions for Z2-6.

OutputRecord  — typed view of an ExtractionResult row ready for CSV/JSON write.
OutputSchemaError — raised when a record fails pre-write validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ── Exceptions ─────────────────────────────────────────────────────────────────

class OutputSchemaError(Exception):
    """Raised when an output record violates the 13-column schema."""


class OutputWriteError(Exception):
    """Raised when writing CSV or JSON to disk fails."""


# ── 13-column CSV schema (exact order from OUTPUT_TEMPLATE_31MAY.xlsx row 4) ──

CSV_COLUMNS = [
    "economy",
    "law_name",
    "law_number_ref",
    "last_amended",
    "indicator_id",
    "article",
    "discovery_tag",
    "location_reference",
    "verbatim_snippet",
    "mapping_rationale",
    "source_url",
    "confidence",
    "notes",
]

# Columns that must not be None/empty.
# last_amended intentionally excluded — template says "blank if never amended".
_REQUIRED_COLUMNS = {
    "economy",
    "law_name",
    "indicator_id",
    "article",
    "discovery_tag",
    "verbatim_snippet",
    "source_url",
}


@dataclass
class OutputRecord:
    """
    All fields needed for one CSV row + JSON extended metadata.
    Built from ExtractionResult + ValidatedResult (Z2-5).
    """
    # 13 CSV columns
    economy: str
    law_name: str
    law_number_ref: Optional[str]
    last_amended: Optional[str]
    indicator_id: str
    article: str
    discovery_tag: str
    location_reference: Optional[str]
    verbatim_snippet: str
    mapping_rationale: Optional[str]
    source_url: str
    confidence: Optional[float]
    notes: Optional[str]

    # 6 JSON extended fields
    ocr_quality_cer: Optional[float]          # Character Error Rate from OCR stage
    processing_time_seconds: Optional[float]  # Wall-clock seconds for this document
    model_version: str                        # e.g. "claude-sonnet-4-20250514"
    raw_context_before: str                   # chunk context window (before)
    raw_context_after: str                    # chunk context window (after)
    verbatim_original: Optional[str]          # original-language text before translation
    archive_url: str                          # Wayback Machine URL or ""

    def as_csv_row(self) -> dict:
        """Return ordered dict matching CSV_COLUMNS exactly."""
        return {
            "economy": self.economy,
            "law_name": self.law_name,
            "law_number_ref": self.law_number_ref or "",
            "last_amended": self.last_amended or "",
            "indicator_id": self.indicator_id,
            "article": self.article,
            "discovery_tag": self.discovery_tag,
            "location_reference": self.location_reference or "",
            "verbatim_snippet": self.verbatim_snippet,
            "mapping_rationale": self.mapping_rationale or "",
            "source_url": self.source_url,
            "confidence": (
                f"{self.confidence:.2f}" if self.confidence is not None else ""
            ),
            "notes": self.notes or "",
        }

    def as_json_dict(self) -> dict:
        """Return full dict for JSON envelope (CSV fields + 6 extended fields)."""
        base = self.as_csv_row()
        base["confidence"] = self.confidence  # keep numeric in JSON
        base.update({
            "ocr_quality_cer": self.ocr_quality_cer,
            "processing_time_seconds": self.processing_time_seconds,
            "model_version": self.model_version,
            "raw_context_before": self.raw_context_before,
            "raw_context_after": self.raw_context_after,
            "verbatim_original": self.verbatim_original,
            "archive_url": self.archive_url,
        })
        return base
