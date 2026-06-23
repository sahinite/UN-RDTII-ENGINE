"""
Seed Data Loader — Round 1 DB + Sample CSV Parser. [Z1-5-ST1]

Builds normalised KNOWN/NEW lookup sets from two authoritative reference files:
  - ESCAPRDTII2_1__Round_1_Database.xlsx   (Round 1 known acts per economy/pillar)
  - Sample_governemnt_portals_Pillar_6_7.csv  (portal act references)

Single source of truth for what counts as "known" — everything else is NEW.
"""

import csv
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from src.crawler.crawler import _normalise_url as normalise_url  # shared — do not duplicate

logger = logging.getLogger(__name__)

# ── Economy ISO mapping (mirrors crawler.py) ───────────────────────────────────

_ECONOMY_ALIASES: dict[str, str] = {
    "singapore": "SG", "sg": "SG",
    "malaysia": "MY", "my": "MY",
    "thailand": "TH", "th": "TH",
    "australia": "AU", "au": "AU",
    "indonesia": "ID", "id": "ID",
    "vietnam": "VN", "vn": "VN",
    "philippines": "PH", "ph": "PH",
    "cambodia": "KH", "kh": "KH",
    "myanmar": "MM", "mm": "MM",
}

# Pillar values recognised per canonical pillar string
_PILLAR_MATCH: dict[str, frozenset[str]] = {
    "P6": frozenset({"p6", "6", "pillar 6", "pillar6", "p6+p7"}),
    "P7": frozenset({"p7", "7", "pillar 7", "pillar7", "p6+p7"}),
    "P6+P7": frozenset({"p6", "p7", "6", "7", "pillar 6", "pillar 7", "pillar6", "pillar7", "p6+p7"}),
}


# ── Normalisation helpers ──────────────────────────────────────────────────────

def normalise_title(title: str) -> str:
    """Strip trailing year suffix; lowercase + collapse whitespace for fuzzy matching."""
    title = re.sub(r"\s+\d{4}\s*$", "", title.strip())
    return re.sub(r"\s+", " ", title.lower().strip())


def _normalise_economy(raw: str) -> str:
    return _ECONOMY_ALIASES.get(raw.lower().strip(), raw.strip().upper()[:2])


def _pillar_matches(raw_pillar: str, target: str) -> bool:
    if raw_pillar.lower().strip() in _PILLAR_MATCH.get(target, frozenset()):
        return True
    # Generic fallback for any pillar not in the static map (e.g. P8, P9)
    m = re.match(r"^P(\d+)$", target, re.IGNORECASE)
    if m:
        n = m.group(1)
        generic = {f"p{n}", n, f"pillar {n}", f"pillar{n}"}
        return raw_pillar.lower().strip() in generic
    return False


# ── Output contract ────────────────────────────────────────────────────────────

@dataclass
class SeedData:
    known_urls: set[str] = field(default_factory=set)
    known_titles: set[str] = field(default_factory=set)
    known_provisions: set[str] = field(default_factory=set)  # anchor-level URLs e.g. "sso.agc.gov.sg/act/pdpa2012#pr26-"
    economy: str = ""
    pillar: str = ""


# ── Column finder helpers ──────────────────────────────────────────────────────

def _find_col(headers: list[str], candidates: list[str]) -> int | None:
    for candidate in candidates:
        for i, h in enumerate(headers):
            if candidate in h:
                return i
    return None


def _find_csv_col(fields_lower: dict[str, str], candidates: list[str]) -> str | None:
    for candidate in candidates:
        for lower_key, orig_key in fields_lower.items():
            if candidate in lower_key:
                return orig_key
    return None


# ── Loaders ────────────────────────────────────────────────────────────────────

def _extract_anchor_urls(raw: str) -> list[str]:
    """Split a cell value on ';' and newlines, return strings containing '#'."""
    parts = re.split(r"[;\n]", raw)
    return [p.strip() for p in parts if "#" in p.strip()]


def _load_round1_db(path: str, economy_iso: str, pillar: str, seed: SeedData) -> int:
    count = 0
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        raw_headers = next(rows, None)
        if not raw_headers:
            wb.close()
            return 0

        headers = [str(h).strip().lower() if h else "" for h in raw_headers]
        col_economy = _find_col(headers, ["economy", "country"])
        col_title   = _find_col(headers, ["act title", "title", "act_title"])
        col_url     = _find_col(headers, ["url", "act_url", "link"])
        col_pillar  = _find_col(headers, ["pillar", "pillar.name"])
        col_refs    = _find_col(headers, ["references", "reference"])

        for row in rows:
            if not any(row):
                continue
            row_economy = str(row[col_economy] or "").strip() if col_economy is not None else ""
            row_pillar  = str(row[col_pillar]  or "").strip() if col_pillar  is not None else ""
            row_url     = str(row[col_url]     or "").strip() if col_url     is not None else ""
            row_title   = str(row[col_title]   or "").strip() if col_title   is not None else ""
            row_refs    = str(row[col_refs]    or "").strip() if col_refs    is not None else ""
            # Fallback: col index 7 is the References column per spec
            if not row_refs and len(row) > 7:
                row_refs = str(row[7] or "").strip()

            if _normalise_economy(row_economy) != economy_iso:
                continue
            if not _pillar_matches(row_pillar, pillar):
                continue
            if not row_url or row_url.lower() in ("none", "n/a", ""):
                continue

            seed.known_urls.add(normalise_url(row_url))
            count += 1
            if row_title:
                seed.known_titles.add(normalise_title(row_title))

            # Anchor-level provision URLs from References column
            for anchor_url in _extract_anchor_urls(row_refs):
                seed.known_provisions.add(normalise_url(anchor_url))

        wb.close()
    except Exception as exc:
        logger.error("Failed to load Round 1 DB from %s: %s", path, exc)
    return count


def _load_sample_csv(path: str, economy_iso: str, pillar: str, seed: SeedData) -> int:
    count = 0
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return 0

            fields_lower = {k.strip().lower(): k for k in reader.fieldnames}
            country_col = _find_csv_col(fields_lower, ["country", "economy"])
            pillar_col  = _find_csv_col(fields_lower, ["pillar.name", "pillar", "pillar_name"])
            refs_col    = _find_csv_col(fields_lower, ["references", "reference", "urls", "url"])
            title_col   = _find_csv_col(fields_lower, ["act title", "title", "act_title"])

            for row in reader:
                row_economy = str(row.get(country_col or "", "") or "").strip()
                row_pillar  = str(row.get(pillar_col  or "", "") or "").strip()

                if _normalise_economy(row_economy) != economy_iso:
                    continue
                if pillar_col and not _pillar_matches(row_pillar, pillar):
                    continue

                refs_raw = str(row.get(refs_col or "", "") or "").strip()
                for raw_url in re.split(r"[;\n]", refs_raw):
                    raw_url = raw_url.strip()
                    if not raw_url or not raw_url.lower().startswith("http"):
                        continue
                    seed.known_urls.add(normalise_url(raw_url))
                    count += 1
                    if "#" in raw_url:
                        seed.known_provisions.add(normalise_url(raw_url))

                if title_col:
                    raw_title = str(row.get(title_col, "") or "").strip()
                    if raw_title:
                        seed.known_titles.add(normalise_title(raw_title))

    except Exception as exc:
        logger.error("Failed to load Sample CSV from %s: %s", path, exc)
    return count


# ── Public API ─────────────────────────────────────────────────────────────────

def load_seed_data(
    economy_iso: str,
    pillar: str,
    round1_db_path: str | None = None,
    sample_csv_path: str | None = None,
) -> SeedData:
    """
    Build SeedData from Round 1 DB xlsx and/or Sample CSV.

    Logs a warning (no crash) if no URLs found for the target economy — valid
    for first-run economies with no Round 1 data.
    """
    seed = SeedData(economy=economy_iso, pillar=pillar)
    db_count = csv_count = 0

    if round1_db_path and Path(round1_db_path).exists():
        db_count = _load_round1_db(round1_db_path, economy_iso, pillar, seed)

    if sample_csv_path and Path(sample_csv_path).exists():
        csv_count = _load_sample_csv(sample_csv_path, economy_iso, pillar, seed)

    if not seed.known_urls:
        logger.warning("WARN: No seed data found for %s %s", economy_iso, pillar)
    else:
        logger.info(
            "[SEED] %s %s: %d known URLs loaded from Round 1 DB; %d from Sample CSV",
            economy_iso, pillar, db_count, csv_count,
        )

    return seed
