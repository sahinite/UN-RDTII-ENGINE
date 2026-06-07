"""
Economy YAML adapter — schema + loader.

Implements ClickUp stories:
  [Z1-1.1] EconomyConfig data model (pydantic)
  [Z1-1.2] load_economy(name) loader + validator

Usage:
    from src.config.economy_config import load_economy
    cfg = load_economy("singapore")
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, HttpUrl, ValidationError, field_validator

# ── Paths ─────────────────────────────────────────────────────────────────────

_ECONOMIES_DIR = Path(__file__).parent.parent.parent / "economies"

# ── Exceptions ─────────────────────────────────────────────────────────────────


class UnknownEconomyError(Exception):
    """Raised when no YAML file exists for the requested economy name."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"No economy config found for '{name}'. "
            f"Expected file: economies/{name.lower()}.yaml"
        )


class InvalidEconomyConfigError(Exception):
    """Raised when an economy YAML exists but fails schema validation."""

    def __init__(self, name: str, details: str) -> None:
        self.name = name
        super().__init__(f"Invalid economy config for '{name}': {details}")


# ── Sub-models ─────────────────────────────────────────────────────────────────

_SCRIPT_TO_OCR: dict[str, str] = {
    "latin": "tesseract",
    "asian": "paddleocr",
}

_SUPPORTED_SCRIPT_TYPES = Literal["latin", "asian"]
_SUPPORTED_OCR_ENGINES = Literal["tesseract", "paddleocr", "azure", "mistral_ocr"]
_SUPPORTED_TRANSLATION_PROVIDERS = Literal["deepl", "google"]


class Portal(BaseModel):
    """A single government portal entry — name + URL, no pillar tagging."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url: HttpUrl


# ── EconomyConfig ──────────────────────────────────────────────────────────────


class EconomyConfig(BaseModel):
    """
    Per-economy adapter config. Single source of truth for everything
    downstream modules need to know about an economy.

    ocr_engine is derived automatically from script_type; set
    ocr_engine_override only to force a Stage-2 engine.
    """

    model_config = ConfigDict(extra="forbid")

    # Core identity
    economy_name: str
    script_type: _SUPPORTED_SCRIPT_TYPES
    languages: list[str]

    # Portals — unlimited, no manual pillar tagging
    portals: list[Portal]

    # OCR — derived from script_type; override only for Stage-2 engines
    ocr_engine_override: _SUPPORTED_OCR_ENGINES | None = None

    # Calendar quirks
    be_year_conversion: bool = False  # Buddhist Era → Gregorian (Thailand etc.)

    # Optional overrides — None means use the global cascade / default
    llm_override: str | None = None
    translation_provider: _SUPPORTED_TRANSLATION_PROVIDERS | None = None

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("languages", mode="before")
    @classmethod
    def languages_valid(cls, v: object) -> object:
        if not isinstance(v, list) or len(v) == 0:
            raise ValueError("languages must contain at least one language code")
        for code in v:
            if not re.fullmatch(r"[a-z]{2,3}", str(code)):
                raise ValueError(
                    f"'{code}' is not a valid ISO 639 language code (2–3 lowercase letters)"
                )
        return v

    @field_validator("portals")
    @classmethod
    def portals_non_empty(cls, v: list[Portal]) -> list[Portal]:
        if not v:
            raise ValueError("portals must contain at least one portal entry")
        return v

    # ── Computed property ─────────────────────────────────────────────────────

    @property
    def ocr_engine(self) -> str:
        """Effective OCR engine: override if set, else derived from script_type."""
        return self.ocr_engine_override or _SCRIPT_TO_OCR[self.script_type]


# ── Loader ─────────────────────────────────────────────────────────────────────


def load_economy(name: str) -> EconomyConfig:
    """
    Load and validate an economy config by name.

    Looks for  economies/{name.lower()}.yaml  relative to the project root.

    Raises:
        UnknownEconomyError   — file does not exist
        InvalidEconomyConfigError — file exists but fails schema validation
    """
    yaml_path = _ECONOMIES_DIR / f"{name.lower()}.yaml"

    if not yaml_path.exists():
        raise UnknownEconomyError(name)

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise InvalidEconomyConfigError(name, f"YAML parse error: {exc}") from exc

    try:
        return EconomyConfig.model_validate(raw)
    except ValidationError as exc:
        raise InvalidEconomyConfigError(name, str(exc)) from exc
