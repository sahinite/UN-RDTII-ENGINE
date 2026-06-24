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
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator

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

    # Playwright / JS-rendering config (retained for compatibility, off the SSO hot path)
    js_required: bool = False               # set True when portal needs JS rendering
    playwright_wait_for: str | None = None  # CSS/XPath selector to wait for before parsing
    playwright_timeout_ms: int | None = None  # page-load timeout; None → module default
    follow_pagination: bool = False         # True → follow "Next" links at same BFS depth

    # ── Per-economy portal strategy fields ────────────────────────────────────
    # How to reach the portal without bot-blocks
    anti_bot: Literal["none", "header_spoof", "playwright_stealth"] = "none"
    # How to find pillar-relevant instruments
    discovery: Literal["index", "search", "search_js", "seed_only", "TBD"] = "TBD"
    # How to obtain complete document text
    fetch: Literal["pdf_endpoint", "html", "html_wholedoc", "html_js", "pdf_link", "TBD"] = "TBD"
    # URLs of in-force browse indexes (used when discovery: index)
    index_urls: list[str] = Field(default_factory=list)
    # Query-string suffix to rewrite act URL to its PDF view (used when fetch: pdf_endpoint)
    pdf_view_suffix: str | None = None
    # Escalation target when httpx-based fetch is blocked
    transport_fallback: Literal["playwright_stealth"] | None = None


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
    Load and validate an economy config by name or ISO code.

    Resolution order:
      1. Normalise: .strip().title()  → exact YAML filename match
      2. ISO code fallback: if input looks like a 2-3 letter code (e.g. "sg", "AU"),
         scan YAML files for a matching iso_code field
      3. Fuzzy match: suggest the closest economy name via difflib
      4. Unsupported: instruct the user to add a YAML file

    Raises:
        UnknownEconomyError   — file does not exist
        InvalidEconomyConfigError — file exists but fails schema validation
    """
    normalised = name.strip().title()
    yaml_path = _ECONOMIES_DIR / f"{normalised.lower()}.yaml"

    if not yaml_path.exists():
        # ISO code fallback (e.g. "sg" → Singapore, "AU" → Australia)
        stripped = name.strip()
        if re.fullmatch(r"[A-Za-z]{2,3}", stripped):
            cfg = load_economy_by_iso(stripped)
            if cfg is not None:
                return cfg

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
