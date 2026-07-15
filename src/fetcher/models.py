"""
Shared dataclasses for Zone 2: FetchedDocument, CostLogEntry, Zone1Result,
TranslatedDocument, ArticleReference.

FetchedDocument is the routing contract between router.py, all extractors,
and downstream Zone 2 modules (RAG, mapper, output writer).
TranslatedDocument wraps FetchedDocument with 3-layer translation output.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Literal, Optional


# ── Zone 1 handoff ─────────────────────────────────────────────────────────────

@dataclass
class Zone1Result:
    """Output from Zone 1 handed to Zone 2 router."""
    url: str
    economy: str          # ISO code e.g. "SG", "AU", "MY"
    act_title: str
    discovery_tag: str    # "KNOWN" or "NEW"
    archive_url: str      # Wayback Machine snapshot URL


# ── Cost tracking ──────────────────────────────────────────────────────────────

@dataclass
class CostLogEntry:
    engine: str                          # "pdfplumber" | "tesseract" | "paddleocr" | "beautifulsoup"
    pages: Optional[int]                 # None for HTML
    cost_usd: float                      # 0.0 for all local/free engines
    processing_time_ms: float
    cer_score: Optional[float] = None
    has_embedded_images: bool = False
    reclassified: bool = False           # True if TEXT_PDF was reclassified to SCANNED
    boundary_fallback: bool = False      # True if segmenter used fixed-50-page fallback
    table_found: bool = False
    table_pages: list[int] = field(default_factory=list)


# ── Main output contract ────────────────────────────────────────────────────────

@dataclass
class FetchedDocument:
    # Identity
    source_url: str
    resolved_url: str
    economy: str
    act_title: str
    discovery_tag: Literal["KNOWN", "NEW"]
    archive_url: str

    # Document metadata
    doc_type: Literal["TEXT_PDF", "SCANNED_PDF", "HTML", "IMAGE", "DOCX", "UNKNOWN"]
    extraction_method: Literal["pdfplumber", "tesseract", "paddleocr", "beautifulsoup", "azure_di", "mistral_ocr", "python_docx"]
    page_count: Optional[int]            # None for HTML

    # Extracted content
    raw_text: str
    section_hierarchy: list[dict]        # [{"level", "title", "text", "anchor"?}]

    # HTML-specific: section title → anchor URL
    location_reference_map: dict[str, str] = field(default_factory=dict)

    # OCR-specific
    cer_score: Optional[float] = None

    # Segmentation
    is_segment: bool = False
    segment_index: Optional[int] = None

    # Quality flags
    flag_for_review: bool = False
    flag_reason: Optional[str] = None

    # Cost tracking (required by hackathon rubric)
    cost_log_entry: Optional[CostLogEntry] = None

    # Legislation citation metadata (parsed from the cover page + source URL)
    law_number_ref: Optional[str] = None   # e.g. "Act 26 of 2012" / "2020 Rev. Ed."
    last_amended: Optional[str] = None     # e.g. "2020" or "2026-05-29" (version date)

    def validate(self) -> None:
        if not self.raw_text:
            raise ValueError(f"raw_text is empty for {self.source_url}")
        # file:// is allowed for locally-provided PDFs (main.py --pdf).
        if not re.match(r"^(https?|file)://", self.source_url):
            raise ValueError(f"source_url is not a valid HTTP/HTTPS/file URL: {self.source_url}")
        if self.discovery_tag not in ("KNOWN", "NEW"):
            raise ValueError(f"discovery_tag must be KNOWN or NEW, got: {self.discovery_tag!r}")
        if self.doc_type == "UNKNOWN":
            raise ValueError(f"doc_type UNKNOWN should have raised UnsupportedDocTypeError before reaching FetchedDocument")
        if self.cost_log_entry is None:
            raise ValueError(f"cost_log_entry is required for hackathon cost tracking")


# ── Segmenter output ───────────────────────────────────────────────────────────

@dataclass
class ActSegment:
    segment_index: int
    act_title: str
    start_page: int
    end_page: int           # inclusive
    raw_bytes: bytes
    economy: str
    source_url: str


# ── Article reference ────────────────────────────────────────────

@dataclass
class ArticleReference:
    """A citable (act_title, part, article_number) tuple within a segment."""
    act_title: str
    part: str           # e.g. "PART I", "CHAPTER 2" — empty string if none
    article_number: str # e.g. "1", "5A", "12(1)"
    heading: str        # Full heading text as it appears in the document
    text_anchor: str    # HTML anchor id or empty string for PDF


# ── Translation cost tracking ───────────────────────────────────────────

@dataclass
class TranslationCostEntry:
    source_language: str
    provider: str       # "deepl" | "google" | "none" | "failed"
    chars_translated: int
    cost_usd: float     # $0.0 for Google (free tier); $20/1M chars for DeepL Pro


# ── Translated document contract ───────────────────────────────────

@dataclass
class TranslatedDocument:
    """
    Wraps a FetchedDocument with the 3-layer translation output (Z2-2).

    verbatim_original always holds the source-language text so the
    output JSON can populate both verbatim_original and verbatim_snippet.
    """
    fetched: FetchedDocument
    source_language: str
    translated_text: str             # Layer 3 output (equals raw_text when English)
    act_title_translated: str        # Layer 2 output
    keywords_translated: list        # Layer 1 output (list[str])
    verbatim_original: str           # Always the original-language raw_text
    translation_provider: str        # "deepl" | "google" | "none" | "failed"
    translation_cost_entry: TranslationCostEntry
    be_year_conversions: list        # [(be_str, ce_int)] — empty for non-BE economies
    article_references: list = field(default_factory=list)  # list[ArticleReference]

    # ── Proxy attributes delegating to wrapped FetchedDocument ─────────────────
    # Required so mapper._build_doc_metadata() and main.py getattr() calls
    # return real values instead of empty defaults on bilingual documents.

    @property
    def source_url(self) -> str:
        return self.fetched.source_url

    @property
    def economy(self) -> str:
        return self.fetched.economy

    @property
    def act_title(self) -> str:
        return self.fetched.act_title

    @property
    def discovery_tag(self) -> str:
        return self.fetched.discovery_tag

    @property
    def raw_text(self) -> str:
        return self.fetched.raw_text

    @property
    def law_number_ref(self) -> Optional[str]:
        return self.fetched.law_number_ref

    @property
    def last_amended_year(self) -> Optional[str]:
        return self.fetched.last_amended

    @property
    def source_pdf_path(self) -> None:
        return None


# ── Serialisation helper ────────────────────────────────────────────────────────

def to_dict(doc: FetchedDocument) -> dict:
    """JSON-serialisable dict of FetchedDocument, raw_text excluded (too large)."""
    d = dataclasses.asdict(doc)
    d.pop("raw_text", None)
    return d
