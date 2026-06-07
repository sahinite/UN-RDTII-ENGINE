"""
Economy YAML adapter — schema + loader.

Implements ClickUp stories:
  [Z1-1.1] EconomyConfig data model (pydantic)
  [Z1-1.2] load_economy(name) loader + validator

Usage:
    from src.config.economy_config import load_economy
    cfg = load_economy("singapore")
"""

# TODO [Z1-1.1]: define EconomyConfig (pydantic model)
#   fields: economy_name, script_type, languages, ocr_engine, portals, llm_override, ...
#   - portals: list[Portal] (unlimited length, no manual pillar tagging field)
#   - ocr_engine default derived from script_type (latin -> tesseract, asian -> paddleocr)

# TODO [Z1-1.2]: def load_economy(name: str) -> EconomyConfig
#   - read economies/{name}.yaml
#   - validate against EconomyConfig
#   - raise clear, named errors on missing/invalid fields (no generic stack traces)
