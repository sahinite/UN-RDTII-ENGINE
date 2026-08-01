"""
Economy YAML adapter — the pydantic EconomyConfig model + load_economy() loader/validator.

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

_OCR_ENGINE_BY_SCRIPT: dict[str, str] = {
    "latin": "tesseract",
    "asian": "paddleocr",
}

ScriptType = Literal["latin", "asian"]
OcrEngine = Literal["tesseract", "paddleocr", "azure", "mistral_ocr"]
TranslationProvider = Literal["deepl", "google"]


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
    # How to find pillar-relevant instruments. Default "auto": an undeclared portal
    # is crawled best-effort (SPA/SSR auto-detected). Set "TBD" to deliberately skip.
    discovery: Literal["index", "api", "sitemap", "auto", "search", "search_js", "seed_only", "TBD"] = "auto"
    # How to obtain complete document text
    fetch: Literal["pdf_endpoint", "api_versioned_pdf", "html", "html_wholedoc", "html_js", "pdf_link", "auto", "TBD"] = "TBD"
    # URLs of in-force browse indexes (used when discovery: index)
    index_urls: list[str] = Field(default_factory=list)
    # Case-insensitive substrings that identify an act/instrument link on this
    # portal's browse index, matched against each link's path+query (used when
    # discovery: index). Keeps portal-shape knowledge in config, not code —
    # e.g. Singapore SSO ["/Act/", "/SL/"], AGC LOM ["act-detail.php"], JPDP
    # ["/akta/"]. When empty, the parser falls back to direct document links
    # (.pdf/.doc/.docx) — a format-based default with no economy-specific tokens.
    index_link_pattern: list[str] = Field(default_factory=list)
    # sitemap.xml URL for a JS-rendered portal with no crawlable HTML index
    # (used when discovery: sitemap) — e.g. pdpc.gov.sg
    sitemap_url: str | None = None
    # Query-string suffix to rewrite act URL to its PDF view (used when fetch: pdf_endpoint)
    pdf_view_suffix: str | None = None
    # Optional CSS selector hint for fetch: pdf_link, used ONLY when the standard
    # embed/iframe/anchor cascade can't find the PDF on a portal's act page. Most
    # portals need no selector — the resolver handles <embed>, pdf.js viewers and
    # .pdf anchors generically. Declarative escape hatch, not per-portal code.
    pdf_link_selector: str | None = None
    # OData/JSON API base + collection (used when discovery: api / fetch: api_versioned_pdf)
    # e.g. api_base="https://api.prod.legislation.gov.au/v1", api_collection="Act"
    api_base: str | None = None
    api_collection: str | None = None
    # Path suffix appended after the resolved version dates for api_versioned_pdf,
    # e.g. "text/original/pdf" → {url}/{titleId}/{start}/{start}/text/original/pdf
    pdf_path_suffix: str | None = None
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
    script_type: ScriptType
    languages: list[str]

    # Portals — unlimited, no manual pillar tagging
    portals: list[Portal]

    # OCR — derived from script_type; override only for Stage-2 engines
    ocr_engine_override: OcrEngine | None = None

    # Calendar quirks
    be_year_conversion: bool = False  # Buddhist Era → Gregorian (Thailand etc.)

    # Optional overrides — None means use the global cascade / default
    llm_override: str | None = None
    translation_provider: TranslationProvider | None = None

    # Redirect a Round 1 seed URL to a cleaner authoritative source. Round 1 often
    # cites stale mirrors (dead links, law-firm/NGO copies); this maps such a URL to
    # its canonical primary (e.g. an AGC act-detail page) so the pipeline fetches
    # the authoritative text and the output records the correct provenance. Keyed by
    # the seed URL, valued by the replacement URL — pure config data, applied
    # generically by discover(); no economy-specific logic lives in code.
    seed_url_remap: dict[str, str] = Field(default_factory=dict)

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("iso_code", mode="before")
    @classmethod
    def validate_iso_code(cls, value: object) -> object:
        code = str(value).strip()
        if code and not re.fullmatch(r"[A-Za-z]{2,3}", code):
            raise ValueError(f"'{value}' is not a valid ISO 3166-1 alpha-2/3 code")
        return code.upper() if code else ""

    @field_validator("languages", mode="before")
    @classmethod
    def validate_languages(cls, value: object) -> object:
        if not isinstance(value, list) or not value:
            raise ValueError("languages must contain at least one language code")
        for code in value:
            if not re.fullmatch(r"[a-z]{2,3}", str(code)):
                raise ValueError(
                    f"'{code}' is not a valid ISO 639 language code (2–3 lowercase letters)"
                )
        return value

    @field_validator("portals")
    @classmethod
    def require_portals(cls, portals: list[Portal]) -> list[Portal]:
        if not portals:
            raise ValueError("portals must contain at least one portal entry")
        return portals

    # ── Computed property ─────────────────────────────────────────────────────

    @property
    def ocr_engine(self) -> str:
        """Effective OCR engine: override if set, else derived from script_type."""
        return self.ocr_engine_override or _OCR_ENGINE_BY_SCRIPT[self.script_type]


# ── Loader ─────────────────────────────────────────────────────────────────────


def _available_economy_names() -> list[str]:
    """Return title-cased economy names from all YAML files in the economies dir."""
    return [
        p.stem.title()
        for p in _ECONOMIES_DIR.glob("*.yaml")
        if p.stem.lower() != "readme"
    ]


def _load_config_file(yaml_path: Path, economy_name: str) -> EconomyConfig:
    """Read one economy YAML file and convert it into a validated config."""
    try:
        raw_config = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise InvalidEconomyConfigError(
            economy_name, f"YAML parse error: {exc}"
        ) from exc

    try:
        return EconomyConfig.model_validate(raw_config)
    except ValidationError as exc:
        raise InvalidEconomyConfigError(economy_name, str(exc)) from exc


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
    normalized_name = name.strip().title()
    yaml_path = _ECONOMIES_DIR / f"{normalized_name.lower()}.yaml"

    if not yaml_path.exists():
        # ISO code fallback (e.g. "sg" → Singapore, "AU" → Australia)
        short_code = name.strip()
        if re.fullmatch(r"[A-Za-z]{2,3}", short_code):
            config = load_economy_by_iso(short_code)
            if config is not None:
                return config

        available = _available_economy_names()
        matches = difflib.get_close_matches(normalized_name, available, n=1, cutoff=0.6)
        suggestion = matches[0] if matches else ""
        raise UnknownEconomyError(normalized_name, suggestion)

    return _load_config_file(yaml_path, normalized_name)


def load_economy_by_iso(iso_code: str) -> EconomyConfig | None:
    """
    Scan economies/*.yaml and return the config whose iso_code matches.
    Returns None if no matching YAML is found.
    """
    normalized_code = iso_code.strip().upper()
    for yaml_path in _ECONOMIES_DIR.glob("*.yaml"):
        if yaml_path.stem.lower() == "readme":
            continue
        try:
            config = _load_config_file(yaml_path, yaml_path.stem.title())
            if config.iso_code == normalized_code:
                return config
        except InvalidEconomyConfigError:
            pass
    return None
