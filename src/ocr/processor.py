"""
OCR two-stage cascade. [Z2-1 Stage 1, Z2-5 Stage 2]

Stage 1 (language-based, automatic from economy YAML):
    Tesseract  -> Latin-script economies
    PaddleOCR  -> Asian-script economies
Stage 2 (quality-based fallback, automatic):
    triggers when CER >= 5% -> Azure Document Intelligence or Mistral OCR

No manual OCR engine choice at runtime — fully automatic cascade.

TODO: def run_ocr(image, economy_cfg) -> OCRResult  (text, cer, engine_used)
TODO: def maybe_fallback(stage1_result) -> OCRResult  (Stage 2 if CER >= 0.05)
"""
