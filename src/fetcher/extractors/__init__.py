"""
OCR Stage 1 extractors package.

Public API:
    pdf_to_images(raw_bytes, dpi)       -> list[bytes]         — render PDF pages to PNG bytes
    assemble_pages(page_texts)          -> str                  — join page texts with page markers
    run_tesseract(image_bytes, lang)    -> tuple[str, float]   — (text, cer) Latin-script OCR
    run_paddleocr(image_bytes, lang)    -> tuple[str, float]   — (text, cer) Asian-script OCR
    get_ocr_engine(economy_config)      -> "tesseract"|"paddleocr"
    extract_ocr_stage1(raw_bytes, zone1_result, economy_config) -> FetchedDocument
        Full Stage 1 pipeline: render → preprocess → OCR → assemble → CER check

Exceptions:
    OCRQualityError — raised when CER >= 5%, signalling Stage 2 escalation
    ExtractionError — raised on unrecoverable OCR failure
"""

from src.fetcher.extractors.ocr_stage1 import (
    OCRQualityError,
    assemble_pages,
    extract_ocr_stage1,
    get_ocr_engine,
    pdf_to_images,
    run_paddleocr,
    run_tesseract,
)

__all__ = [
    "pdf_to_images",
    "assemble_pages",
    "run_tesseract",
    "run_paddleocr",
    "get_ocr_engine",
    "extract_ocr_stage1",
    "OCRQualityError",
]
