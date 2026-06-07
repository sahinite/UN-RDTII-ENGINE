"""
Segment + translate. [Z2-2]

- Splits consolidated multi-act volumes (e.g. 200+ pages / multiple act
  headers, like the sample Niue Legislation) into individually citable acts
- Applies 3-layer DeepL translation (search keywords -> act titles -> full
  text), Google Translate as fallback
- Persists BOTH verbatim_original and translation (feedback item Q35)

TODO: def segment(raw_text) -> list[ActSegment]
TODO: def translate(text, source_lang) -> TranslationResult  # original + translated
"""
