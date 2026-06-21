"""
OCR Stage 2 cascade package. [Z2-5]

Stage 2 triggers automatically when Stage 1 CER >= 5%.
Provider cascade: Azure Document Intelligence → Mistral OCR.
Both providers are credentials-gated (ADR-024): if neither key is set,
Stage 1 text is returned with flag_for_review=True.

Public API:
    maybe_stage2_fallback(cer, image_bytes, stage1_text, zone1_result, economy_config)
        -> OCRResult
        Triggered per-page: runs Stage 2 only when cer >= CER_THRESHOLD (5%).

    run_ocr_stage2(raw_bytes, zone1_result, economy_config, stage1_cer, ...)
        -> FetchedDocument
        Full document-level Stage 2 entry point called by router.py.

    run_azure_di(image_bytes)    -> tuple[str, float]   — (text, cer)
    run_mistral_ocr(image_bytes) -> tuple[str, float]   — (text, cer)

Models:
    OCRResult — text, cer, engine_used, stage2_triggered
"""

from src.ocr.processor import (
    OCRResult,
    maybe_stage2_fallback,
    run_azure_di,
    run_mistral_ocr,
    run_ocr_stage2,
)

__all__ = [
    "maybe_stage2_fallback",
    "run_ocr_stage2",
    "run_azure_di",
    "run_mistral_ocr",
    "OCRResult",
]
