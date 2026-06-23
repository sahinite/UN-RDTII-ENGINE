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

import difflib
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

    def __init__(self, name: str, suggestion: str = "") -> None:
        self.name = name
        if suggestion:
            msg = (
                f"Unknown economy '{name}'. Did you mean: {suggestion}? "
                f"Expected file: economies/{name.lower()}.yaml"
            )
        else:
            msg = (
                f"Economy '{name}' is not supported yet. "
                f"To add it, create economies/{name.lower()}.yaml — "
                f"see '## Adding a new economy' in README for the required fields."
            )
        super().__init__(msg)


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
    type: Literal["primary", "secondary"] = "primary"
    search_url_pattern: str | None = None

    # Playwright / JS-rendering config (all optional — static portals omit these)
    js_required: bool = False               # set True when portal needs JS rendering
    playwright_wait_for: str | None = None  # CSS/XPath selector to wait for before parsing
    playwright_timeout_ms: int | None = None  # page-load timeout; None → module default
    follow_pagination: bool = False         # True → follow "Next" links at same BFS depth


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
    iso_code: str = ""  # ISO 3166-1 alpha-2 code, e.g. "SG", "VN" — required in production YAMLs
    un_name: str = ""   # UN official name for CSV output — required in production YAMLs
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

    @field_validator("iso_code", mode="before")
    @classmethod
    def iso_code_valid(cls, v: object) -> object:
        s = str(v).strip()
        if s and not re.fullmatch(r"[A-Za-z]{2,3}", s):
            raise ValueError(f"'{v}' is not a valid ISO 3166-1 alpha-2/3 code")
        return s.upper() if s else ""

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


def _available_economy_names() -> list[str]:
    """Return title-cased economy names from all YAML files in the economies dir."""
    return [
        p.stem.title()
        for p in _ECONOMIES_DIR.glob("*.yaml")
        if p.stem.lower() != "readme"
    ]


def load_economy(name: str) -> EconomyConfig:
    """
    Load and validate an economy config by name.

    Normalises input with .strip().title() before lookup so "singapore" and
    "  Singapore  " both resolve correctly.  On failure, uses difflib to suggest
    the closest matching economy name in the error message.

    Looks for  economies/{name.lower()}.yaml  relative to the project root.

    Raises:
        UnknownEconomyError   — file does not exist
        InvalidEconomyConfigError — file exists but fails schema validation
    """
    normalised = name.strip().title()
    yaml_path = _ECONOMIES_DIR / f"{normalised.lower()}.yaml"

    if not yaml_path.exists():
        available = _available_economy_names()
        matches = difflib.get_close_matches(normalised, available, n=1, cutoff=0.6)
        suggestion = matches[0] if matches else ""
        raise UnknownEconomyError(normalised, suggestion)

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise InvalidEconomyConfigError(normalised, f"YAML parse error: {exc}") from exc

    try:
        return EconomyConfig.model_validate(raw)
    except ValidationError as exc:
        raise InvalidEconomyConfigError(normalised, str(exc)) from exc


def load_economy_by_iso(iso_code: str) -> EconomyConfig | None:
    """
    Scan economies/*.yaml and return the config whose iso_code matches.
    Returns None if no matching YAML is found.
    """
    for yaml_path in _ECONOMIES_DIR.glob("*.yaml"):
        if yaml_path.stem.lower() == "readme":
            continue
        try:
            cfg = load_economy(yaml_path.stem)
            if cfg.iso_code == iso_code.upper():
                return cfg
        except (UnknownEconomyError, InvalidEconomyConfigError):
            pass
    return None
