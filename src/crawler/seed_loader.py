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


def canonical_pillar(target) -> str:
    """Normalise any caller-supplied pillar to canonical 'P<n>' (or 'P6+P7').

    Accepts every form the pipeline and callers pass in the wild: 7, "7", "P7",
    "p7", "pillar 7", "Pillar7", and combined "P6+P7" / "6+7". Returns "" for a
    blank/unparseable value. This is what makes seed loading generic — a caller
    that passes "7" instead of "P7" must not silently get zero seeds.
    """
    t = str(target or "").strip().lower()
    if not t:
        return ""
    if "+" in t:  # combined pillars, e.g. "p6+p7" / "6 + 7"
        nums = re.findall(r"\d+", t)
        if nums:
            return "+".join(f"P{int(n)}" for n in nums)
    m = re.search(r"\d+", t)
    return f"P{int(m.group())}" if m else t.upper()


def _pillar_matches(raw_pillar: str, target: str) -> bool:
    canon = canonical_pillar(target)
    if raw_pillar.lower().strip() in _PILLAR_MATCH.get(canon, frozenset()):
        return True
    # Generic fallback for any single pillar not in the static map (e.g. P8, P9)
    m = re.match(r"^P(\d+)$", canon)
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
    # normalised act title → set of section-number tokens Round 1 cited in prose
    # (e.g. {"personal data protection act": {"11", "25"}}). Round 1 identifies
    # most known provisions by prose section number, not anchored URLs, so this is
    # what drives provision-level KNOWN tagging. See [[known-provision-matching-gap]].
    known_sections: dict[str, set[str]] = field(default_factory=dict)
    # engine indicator id ("P7-I3") → {normalised act → section tokens} — the SAME
    # prose sections but partitioned by the indicator Round 1 filed them under.
    # Drives seed-guided retrieval so the right section reaches the right indicator
    # (e.g. Employment s.95 → I3 retention, not I5 access). [[indicator-drift]]
    known_sections_by_indicator: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    # indicator_id (raw DB form, e.g. "7.3") → normalised act titles that are the
    # Round 1 ground-truth seed acts for that indicator. Drives indicator-aware
    # act selection so one indicator's many seed acts (P7-I3 has 5) don't crowd
    # the others out of the ZONE2_MAX_ACTS cap.
    known_titles_by_indicator: dict[str, set[str]] = field(default_factory=dict)
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


# Section citations in Round 1 prose: "Section 199", "Sec. 26", "s. 12A", "§ 47".
# Capture the number + optional single letter suffix; the (subparagraph) is dropped
# so it matches infer_section_token() on the extraction side.
_SECTION_RE = re.compile(r"(?:section|sec\.?|s\.|§)\s*(\d+[A-Za-z]?)\b", re.IGNORECASE)


def _extract_section_tokens(raw: str) -> set[str]:
    """All section-number tokens cited in prose, lowercased (e.g. {"199", "11"})."""
    return {m.group(1).lower() for m in _SECTION_RE.finditer(raw or "")}


def _db_indicator_to_engine(raw_indic: str) -> str | None:
    """Convert a Round 1 DB indicator id ("7.3") to the engine form ("P7-I3").

    RDTII numbers indicators "<pillar>.<n>"; the engine/taxonomy uses "P<pillar>-I<n>".
    Returns None when the cell is blank or unparseable (e.g. a pillar-level "7").
    """
    m = re.match(r"\s*(\d+)\.(\d+)", raw_indic or "")
    return f"P{int(m.group(1))}-I{int(m.group(2))}" if m else None


def _extract_ref_urls(raw: str) -> list[str]:
    """All http(s) URLs in a References cell (split on ';' and newlines).

    Round 1 has no dedicated URL column — act URLs live in the References cell
    (e.g. "https://sso.agc.gov.sg/Act/CoA1967"). Harvesting these is what lets us
    fetch every ground-truth act, not only the ones the browse index surfaces.
    """
    parts = re.split(r"[;\n]", raw)
    return [p.strip() for p in parts if p.strip().lower().startswith("http")]


_NON_ECONOMY_SHEETS = {"consolidated", "methodology", "rdtii 2.1 methodology"}


def _find_economy_sheet(wb, economy_iso: str, economy_name: str | None):
    """
    Return the worksheet for this economy, or None if the DB has no per-economy
    sheet. Prefers a sheet whose name matches the economy name (reliable, avoids
    2-letter collisions like India/Indonesia), then a normalised-ISO match. The
    DB is laid out one sheet per economy (R1 also has a 'Consolidated' sheet,
    which differs from the per-economy sheets and is NOT what evaluate() reads).
    """
    candidates = [s for s in wb.sheetnames if s.strip().lower() not in _NON_ECONOMY_SHEETS]
    if economy_name:
        for s in candidates:
            if s.strip().lower() == economy_name.strip().lower():
                return wb[s]
    target = _normalise_economy(economy_iso)
    matches = [s for s in candidates if _normalise_economy(s) == target]
    if len(matches) == 1:
        return wb[matches[0]]
    return None


def _load_round1_db(path: str, economy_iso: str, pillar: str, seed: SeedData,
                    economy_name: str | None = None) -> int:
    count = 0
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        # Read the economy's OWN sheet (consistent with evaluate.py + works for
        # the Round 2 DB which has no Consolidated sheet). Fall back to the active
        # sheet (R1 Consolidated, Country-filtered) only if no economy sheet.
        econ_ws = _find_economy_sheet(wb, economy_iso, economy_name)
        ws = econ_ws if econ_ws is not None else wb.active
        rows = ws.iter_rows(values_only=True)
        raw_headers = next(rows, None)
        if not raw_headers:
            wb.close()
            return 0

        headers = [str(h).strip().lower() if h else "" for h in raw_headers]
        col_economy = _find_col(headers, ["economy", "country"])
        # A true per-economy sheet has NO Country column (R1 'Singapore', R2
        # 'India'); a sheet that HAS one is multi-economy and must be filtered,
        # even if its name happened to match the economy.
        per_economy_sheet = col_economy is None
        col_title   = _find_col(headers, ["act title", "title", "act_title", "act and/or practice", "act and/or"])
        col_url     = _find_col(headers, ["url", "act_url", "link"])
        col_pillar  = _find_col(headers, ["pillar_id", "pillar", "pillar.name"])
        col_refs    = _find_col(headers, ["references", "reference"])
        col_indic   = _find_col(headers, ["indicator_id", "indicator"])
        # Round 1 cites known provisions by prose section number in the comment /
        # coverage columns (e.g. "According to Section 199, every company …").
        col_comment = _find_col(headers, ["impact or comments", "comments", "comment"])
        col_cover   = _find_col(headers, ["coverage"])

        for row in rows:
            if not any(row):
                continue
            row_economy = str(row[col_economy] or "").strip() if col_economy is not None else ""
            row_pillar  = str(row[col_pillar]  or "").strip() if col_pillar  is not None else ""
            row_url     = str(row[col_url]     or "").strip() if col_url     is not None else ""
            row_title   = str(row[col_title]   or "").strip() if col_title   is not None else ""
            row_indic   = str(row[col_indic]   or "").strip() if col_indic   is not None else ""
            row_refs    = str(row[col_refs]    or "").strip() if col_refs    is not None else ""
            # Fallback: col index 7 is the References column per spec
            if not row_refs and len(row) > 7:
                row_refs = str(row[7] or "").strip()

            # Country filter applies only to the Consolidated sheet; a per-economy
            # sheet has no Country column and is already economy-scoped.
            if not per_economy_sheet and _normalise_economy(row_economy) != _normalise_economy(economy_iso):
                continue
            if not _pillar_matches(row_pillar, pillar):
                continue

            has_url = row_url and row_url.lower() not in ("none", "n/a", "")
            if has_url:
                seed.known_urls.add(normalise_url(row_url))
                count += 1
            row_act_norms: list[str] = []
            if row_title:
                # Round 1 cells often pack several acts into one "act and/or
                # practice" string joined by ';' (e.g. "Personal Data Protection
                # Act 2012; Guide to ...; Advisory Guidelines ..."). Split so each
                # act becomes its own matchable known title — otherwise the PDPA
                # never matches a browse-index title.
                for part in re.split(r"[;\n]", row_title):
                    part = part.strip()
                    if part:
                        norm = normalise_title(part)
                        row_act_norms.append(norm)
                        seed.known_titles.add(norm)
                        if row_indic:
                            seed.known_titles_by_indicator.setdefault(row_indic, set()).add(norm)
                if not has_url:
                    count += 1  # count title-only rows so we know seeds loaded

            # Harvest prose section numbers from this row (title + comment +
            # coverage) and attribute them to every act named in the row. Round 1
            # gives no anchor URLs for these, so this is the primary KNOWN signal.
            row_comment = str(row[col_comment] or "").strip() if col_comment is not None else ""
            row_cover   = str(row[col_cover]  or "").strip() if col_cover  is not None else ""
            row_sections = _extract_section_tokens(" ".join((row_title, row_comment, row_cover)))
            if row_sections and row_act_norms:
                eng_indic = _db_indicator_to_engine(row_indic)
                for act_norm in row_act_norms:
                    seed.known_sections.setdefault(act_norm, set()).update(row_sections)
                    # Partition by indicator too (skips pillar-level/blank rows), so
                    # seed-guided retrieval knows which indicator each section serves.
                    if eng_indic:
                        seed.known_sections_by_indicator.setdefault(eng_indic, {}).setdefault(
                            act_norm, set()).update(row_sections)

            # URLs from the References column: bare act URLs → known_urls (so the
            # act is fetched even if the browse index never surfaces it); anchored
            # (#) URLs → known_provisions for provision-level KNOWN tagging.
            for ref_url in _extract_ref_urls(row_refs):
                if "#" in ref_url:
                    seed.known_provisions.add(normalise_url(ref_url))
                    bare = ref_url.split("#", 1)[0]
                    if bare:
                        seed.known_urls.add(normalise_url(bare))
                else:
                    seed.known_urls.add(normalise_url(ref_url))
                count += 1

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

                if _normalise_economy(row_economy) != _normalise_economy(economy_iso):
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
                        for part in re.split(r"[;\n]", raw_title):
                            part = part.strip()
                            if part:
                                seed.known_titles.add(normalise_title(part))

    except Exception as exc:
        logger.error("Failed to load Sample CSV from %s: %s", path, exc)
    return count


# ── Public API ─────────────────────────────────────────────────────────────────

def load_seed_data(
    economy_iso: str,
    pillar: str,
    round1_db_path: str | None = None,
    sample_csv_path: str | None = None,
    economy_name: str | None = None,
) -> SeedData:
    """
    Build SeedData from Round 1 DB xlsx and/or Sample CSV.

    economy_name (e.g. "Singapore") is used to select the economy's own sheet in
    the DB; falls back to ISO matching, then the Consolidated sheet. Logs a
    warning (no crash) if no URLs found for the target economy — valid for
    first-run economies with no Round 1 data.
    """
    seed = SeedData(economy=economy_iso, pillar=pillar)
    db_count = csv_count = 0

    if round1_db_path and Path(round1_db_path).exists():
        db_count = _load_round1_db(round1_db_path, economy_iso, pillar, seed, economy_name)

    if sample_csv_path and Path(sample_csv_path).exists():
        csv_count = _load_sample_csv(sample_csv_path, economy_iso, pillar, seed)

    if not seed.known_urls and not seed.known_titles:
        logger.warning("WARN: No seed data found for %s %s", economy_iso, pillar)
    else:
        logger.info(
            "[SEED] %s %s: %d known URLs loaded from Round 1 DB; %d from Sample CSV; "
            "%d act(s) with prose sections",
            economy_iso, pillar, db_count, csv_count, len(seed.known_sections),
        )

    return seed
