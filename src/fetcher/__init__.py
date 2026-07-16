"""
Zone 2 fetch, route, segment, and translate package.

Public API:
    route(zone1_result, economy_config) -> FetchedDocument | list[FetchedDocument]
        Download + detect type + extract text (PDF/HTML/OCR).
        Returns a list when a consolidated volume is segmented.

    translate_document(doc, economy_config) -> TranslatedDocument
        3-layer translation pipeline (Layer 1 keywords, Layer 2 title, Layer 3 body).
        English economies return immediately without any API calls.

Models:
    Zone1Result        — handoff from Zone 1 to Zone 2
    FetchedDocument    — single Zone 2 output contract (all extractors return this)
    TranslatedDocument — FetchedDocument + translated text + cost entry
    CostLogEntry       — per-document OCR/extraction cost record

Exceptions:
    DownloadError, UnsupportedDocTypeError
"""

from src.fetcher.router import (
    DownloadError,
    UnsupportedDocTypeError,
    route,
)
from src.fetcher.translator import translate_document
from src.fetcher.models import (
    CostLogEntry,
    FetchedDocument,
    TranslatedDocument,
    Zone1Result,
)

__all__ = [
    # Pipeline entry points
    "route",
    "translate_document",
    # Models
    "Zone1Result",
    "FetchedDocument",
    "TranslatedDocument",
    "CostLogEntry",
    # Exceptions
    "DownloadError",
    "UnsupportedDocTypeError",
]
