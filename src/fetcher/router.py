"""
Fetch + route + OCR Stage 1 (language-based). [Z2-1]

Routes by document type:
  text PDF  -> pdfplumber
  HTML      -> BeautifulSoup
  scanned   -> OCR Stage 1, selected ONLY from economy YAML script_type
               (tesseract = latin script, paddleocr = asian script)
No manual per-run OCR engine selection.

TODO: def fetch_and_route(candidate_act, economy_cfg) -> ExtractedText
"""
