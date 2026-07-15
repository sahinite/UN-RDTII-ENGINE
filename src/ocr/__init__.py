"""
OCR cascade package.

Cloud-first cascade (router entry point):
    run_ocr_cloud(raw_bytes, zone1_result, economy_config) -> FetchedDocument | None
        Mistral (whole-doc → per-page retry) → Azure DI (if configured) → LLM-vision.
        Returns None when no cloud tier is configured/succeeds → router falls to the
        local Tesseract/Paddle floor.

Per-page helpers:
    run_azure_di(image_bytes)    -> tuple[str, float]   — (text, cer)
    run_mistral_ocr(image_bytes) -> tuple[str, float]   — (text, cer)
"""

from src.ocr.processor import (
    run_azure_di,
    run_mistral_ocr,
    run_ocr_cloud,
)

__all__ = [
    "run_ocr_cloud",
    "run_azure_di",
    "run_mistral_ocr",
]
