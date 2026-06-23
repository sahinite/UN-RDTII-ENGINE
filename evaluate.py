"""
Compares engine output against the Round 1 sample kit. [Z2-6 ST6]

Usage:
    python evaluate.py --sample-kit data/sample_kit/ --economy Singapore
    python evaluate.py --sample-kit data/sample_kit/ --economy Singapore --pillar 6
    python evaluate.py --sample-kit data/sample_kit/ --economy Singapore --csv outputs/singapore_pillar7.csv

Outputs a comparison report:
- Which KNOWN provisions were matched vs. missed
- Which NEW provisions were independently discovered
- Accuracy score (0-40 for KNOWN, 0-20 for NEW, total 60)
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path


# ── Sample Kit Loader ──────────────────────────────────────────────────────────

def _indicator_id_from_sample_kit(pillar_id, indicator_id_raw) -> str | None:
    """
    Convert sample kit Pillar_ID + Indicator_ID to our P6-I1 format.
    e.g. pillar=6, indicator=6.4 → P6-I4
    """
    try:
        ind_float = float(indicator_id_raw)
        # Extract the sub-indicator digit: 6.4 → 4, 7.1 → 1
        decimal_part = round((ind_float % 1) * 10)
        if decimal_part == 0:
            # Whole number like 6 or 7 — section header, skip
            return None
        return f"P{int(pillar_id)}-I{decimal_part}"
    except (TypeError, ValueError):
        return None


def _normalise_for_key(s: str) -> str:
    """Lowercase, collapse whitespace — for (law_name, article) key comparison."""
    return re.sub(r"\s+", " ", s.strip().lower())


def load_sample_kit(
    sample_kit_dir: Path, economy: str, pillar: int | None = None
) -> dict[str, list[dict]]:
    """
    Load ground-truth provisions from the Round 1 sample kit XLSX.
    Returns dict keyed by indicator_id (e.g. "P6-I4") → list of provision dicts.
    """
    try:
        import openpyxl
    except ImportError:
        print("Error: openpyxl required — pip install openpyxl", file=sys.stderr)
        sys.exit(1)

    kit_path = sample_kit_dir / "ESCAP-RDTII-2.1_ Round 1 Database.xlsx"
    if not kit_path.exists():
        candidates = list(sample_kit_dir.glob("*.xlsx"))
        if not candidates:
            print(f"Error: No .xlsx found in {sample_kit_dir}", file=sys.stderr)
            sys.exit(1)
        kit_path = candidates[0]

    wb = openpyxl.load_workbook(str(kit_path))

    # Find economy sheet (case-insensitive)
    sheet_name = None
    for name in wb.sheetnames:
        if name.lower() == economy.lower():
            sheet_name = name
            break
    if not sheet_name:
        print(
            f"Error: Economy '{economy}' not found in sample kit. "
            f"Available: {wb.sheetnames}",
            file=sys.stderr,
        )
        sys.exit(1)

    ws = wb[sheet_name]
    provisions: dict[str, list[dict]] = {}
    current_pillar = None

    for row in ws.iter_rows(min_row=2, values_only=True):
        p_id = row[0]
        i_id = row[1]

        if p_id is not None:
            try:
                current_pillar = int(p_id)
            except (TypeError, ValueError):
                continue

        if pillar is not None and current_pillar != pillar:
            continue

        if current_pillar not in (6, 7):
            continue

        # Skip section header rows
        if i_id is None or isinstance(i_id, str):
            continue

        ind_key = _indicator_id_from_sample_kit(current_pillar, i_id)
        if not ind_key:
            continue

        law_name = row[3] if len(row) > 3 else None
        references = row[7] if len(row) > 7 else None

        provision = {
            "indicator_id": ind_key,
            "pillar": current_pillar,
            "raw_score": row[2],
            "law_name": str(law_name).strip() if law_name else "",
            "references": str(references).strip() if references else "",
        }

        provisions.setdefault(ind_key, []).append(provision)

    return provisions


def _load_known_provision_keys(
    sample_kit_dir: Path, economy: str, pillar: int | None = None
) -> set[tuple[str, str]]:
    """
    Build (normalised_law_name, normalised_article_ref) pairs from sample kit.
    Used to distinguish genuinely NEW provisions from KNOWN ones in evaluate().
    """
    try:
        import openpyxl
    except ImportError:
        return set()

    kit_path = sample_kit_dir / "ESCAP-RDTII-2.1_ Round 1 Database.xlsx"
    if not kit_path.exists():
        candidates = list(sample_kit_dir.glob("*.xlsx"))
        if not candidates:
            return set()
        kit_path = candidates[0]

    wb = openpyxl.load_workbook(str(kit_path))
    sheet_name = None
    for name in wb.sheetnames:
        if name.lower() == economy.lower():
            sheet_name = name
            break
    if not sheet_name:
        return set()

    ws = wb[sheet_name]
    keys: set[tuple[str, str]] = set()
    current_pillar = None

    for row in ws.iter_rows(min_row=2, values_only=True):
        p_id = row[0]
        if p_id is not None:
            try:
                current_pillar = int(p_id)
            except (TypeError, ValueError):
                continue

        if pillar is not None and current_pillar != pillar:
            continue
        if current_pillar not in (6, 7):
            continue

        law_name = str(row[3]).strip() if len(row) > 3 and row[3] else ""
        references = str(row[7]).strip() if len(row) > 7 and row[7] else ""

        if not law_name:
            continue

        # Parse article references from the References cell (anchor URLs → article numbers)
        parts = re.split(r"[;\n]", references)
        for part in parts:
            part = part.strip()
            anchor_match = re.search(r"#pr(\d+[a-zA-Z]?)-?", part, re.IGNORECASE)
            if anchor_match:
                article_ref = f"section {anchor_match.group(1)}"
                keys.add((_normalise_for_key(law_name), article_ref))
            elif part.startswith("http"):
                # Base URL — add law name with empty article to mark law as known
                keys.add((_normalise_for_key(law_name), ""))

    return keys


# ── Engine Output Loader ───────────────────────────────────────────────────────

def load_engine_output(csv_path: Path) -> list[dict]:
    """Load engine output CSV and return list of row dicts."""
    if not csv_path or not csv_path.exists():
        return []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


# ── Evaluation ─────────────────────────────────────────────────────────────────

def _find_best_csv(output_dir: Path, economy: str, pillar: int | None) -> Path | None:
    """Locate the most recent engine CSV for economy+pillar."""
    if pillar:
        candidate = output_dir / f"{economy.lower()}_pillar{pillar}.csv"
        if candidate.exists():
            return candidate
    for p in (6, 7):
        candidate = output_dir / f"{economy.lower()}_pillar{p}.csv"
        if candidate.exists():
            return candidate
    for f in sorted(output_dir.glob(f"{economy.lower()}*.csv")):
        return f
    return None


def evaluate(
    sample_kit_dir: Path,
    economy: str,
    pillar: int | None = None,
    csv_path: Path | None = None,
    output_dir: Path = Path("outputs"),
) -> dict:
    """
    Compare engine output against Round 1 ground truth.
    Returns evaluation report dict.
    """
    ground_truth = load_sample_kit(sample_kit_dir, economy, pillar)
    known_provision_keys = _load_known_provision_keys(sample_kit_dir, economy, pillar)

    if csv_path is None:
        csv_path = _find_best_csv(output_dir, economy, pillar)

    engine_rows = load_engine_output(csv_path) if csv_path else []

    engine_by_indicator: dict[str, list[dict]] = {}
    for row in engine_rows:
        ind = row.get("indicator_id", "").strip()
        if ind:
            engine_by_indicator.setdefault(ind, []).append(row)

    known_indicators = set(ground_truth.keys())
    engine_indicators = set(engine_by_indicator.keys())

    matched_known = known_indicators & engine_indicators
    missed_known = known_indicators - engine_indicators

    known_score = (
        (len(matched_known) / len(known_indicators) * 40)
        if known_indicators
        else 0.0
    )

    # Decision 4: NEW score — provision-level comparison
    # Count rows tagged "NEW" whose (law_name, article) is not in the known provision keys
    genuine_new_provisions: list[dict] = []
    for row in engine_rows:
        if row.get("discovery_tag", "").strip().upper() != "NEW":
            continue
        law_key = _normalise_for_key(row.get("law_name", ""))
        art_key = _normalise_for_key(row.get("article", ""))
        # Check against both exact article key and the bare law-level key
        art_num_match = re.search(r"\d+", art_key)
        art_num_key = f"section {art_num_match.group()}" if art_num_match else art_key
        if (law_key, art_num_key) not in known_provision_keys and (law_key, "") not in known_provision_keys:
            genuine_new_provisions.append(row)

    new_score = min(len(genuine_new_provisions) * 4, 20)
    total_score = known_score + new_score

    return {
        "economy": economy,
        "pillar": pillar,
        "csv_path": str(csv_path) if csv_path else None,
        "ground_truth_indicators": sorted(known_indicators),
        "engine_indicators": sorted(engine_indicators),
        "matched_known": sorted(matched_known),
        "missed_known": sorted(missed_known),
        "genuine_new_provisions": len(genuine_new_provisions),
        "scores": {
            "known_matched": len(matched_known),
            "known_total": len(known_indicators),
            "known_score": round(known_score, 1),
            "new_discovered": len(genuine_new_provisions),
            "new_score": round(new_score, 1),
            "total_score": round(total_score, 1),
            "max_score": 60.0,
        },
    }


def _print_report(report: dict) -> None:
    econ = report["economy"]
    pillar = report.get("pillar") or "6+7"
    s = report["scores"]
    print(f"\n{'='*60}")
    print(f"  EVALUATION REPORT — {econ} | Pillar {pillar}")
    print(f"{'='*60}")
    print(f"  CSV input        : {report['csv_path'] or '(no output found)'}")
    print(f"  {'─'*56}")
    print(f"  KNOWN indicators")
    print(f"    Ground truth   : {s['known_total']}")
    print(f"    Matched        : {s['known_matched']}")
    print(f"    Score          : {s['known_score']:.1f} / 40.0")
    if report["missed_known"]:
        print(f"    Missed         : {', '.join(report['missed_known'])}")
    print(f"  {'─'*56}")
    print(f"  NEW provisions   : {s['new_discovered']} genuine new provisions")
    print(f"    Score          : {s['new_score']:.1f} / 20.0")
    print(f"  {'─'*56}")
    print(f"  TOTAL SCORE      : {s['total_score']:.1f} / {s['max_score']:.1f}")
    print(f"{'='*60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate engine output against Round 1 sample kit"
    )
    parser.add_argument(
        "--sample-kit", required=True, help="Path to sample kit directory"
    )
    parser.add_argument("--economy", required=True, help="Economy name")
    parser.add_argument(
        "--pillar", type=int, choices=[6, 7], default=None,
        help="Filter to specific pillar (omit for both)"
    )
    parser.add_argument(
        "--csv", default=None,
        help="Path to engine output CSV (auto-detected if omitted)"
    )
    parser.add_argument(
        "--output-dir", default="outputs",
        help="Output directory for auto-detection"
    )
    args = parser.parse_args()

    report = evaluate(
        sample_kit_dir=Path(args.sample_kit),
        economy=args.economy,
        pillar=args.pillar,
        csv_path=Path(args.csv) if args.csv else None,
        output_dir=Path(args.output_dir),
    )
    _print_report(report)


if __name__ == "__main__":
    main()
