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

from dotenv import load_dotenv
load_dotenv()

import argparse
import csv
import re
import sys
from datetime import datetime
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

def _find_csvs(output_dir: Path, economy: str, pillar: int | None) -> list[Path]:
    """
    Return the most-recent engine CSV(s) for economy+pillar.

    Matches the engine's `{EconomyName}_P{pillar}_{timestamp}.csv` naming
    case-insensitively (and legacy `{economy}_pillar{n}.csv`), picking the newest
    by modification time. With no pillar, returns the newest CSV for EACH of
    pillars 6 and 7 so the evaluation covers the economy's full output rather than
    comparing both pillars' ground truth against a single-pillar file.
    """
    if not output_dir.exists():
        return []
    econ = economy.lower()
    pillars = [pillar] if pillar else [6, 7]
    found: list[Path] = []
    for p in pillars:
        best: Path | None = None
        for f in output_dir.glob("*.csv"):
            low = f.name.lower()
            if low.startswith(econ + "_") and (
                f"_p{p}_" in low or f"_pillar{p}" in low or low == f"{econ}_pillar{p}.csv"
            ):
                if best is None or f.stat().st_mtime > best.stat().st_mtime:
                    best = f
        if best is not None:
            found.append(best)
    return found


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

    if csv_path is not None:
        csv_paths = [csv_path]
    else:
        csv_paths = _find_csvs(output_dir, economy, pillar)

    engine_rows: list[dict] = []
    for cp in csv_paths:
        engine_rows.extend(load_engine_output(cp))
    csv_path = ", ".join(str(c) for c in csv_paths) if csv_paths else None

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
        "new_provisions": [
            {
                "indicator_id": r.get("indicator_id", ""),
                "law_name": r.get("law_name", ""),
                "article": r.get("article", ""),
            }
            for r in genuine_new_provisions
        ],
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


def build_economy_report(
    sample_kit_dir: Path,
    economy: str,
    pillar: int | None,
    csv_path: Path | None,
    output_dir: Path,
) -> dict:
    """
    Group the evaluation by pillar. Runs evaluate() per pillar (P6, P7 — or just
    the requested one) and returns a report with a per-pillar section list plus an
    overall score (sum of the independent per-pillar /60 scores).
    """
    pillars = [pillar] if pillar else [6, 7]
    sections: list[dict] = []
    for p in pillars:
        # Honour an explicit --csv only when a single pillar is requested.
        cp = csv_path if (pillar and csv_path) else None
        sections.append(evaluate(sample_kit_dir, economy, p, cp, output_dir))
    return {
        "economy": economy,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pillars": sections,
        "overall": {
            "pillars_evaluated": [r["pillar"] for r in sections],
            "known_matched": sum(r["scores"]["known_matched"] for r in sections),
            "known_total": sum(r["scores"]["known_total"] for r in sections),
            "new_discovered": sum(r["scores"]["new_discovered"] for r in sections),
        },
    }


def _format_pillar_section(pr: dict) -> str:
    s = pr["scores"]
    L: list[str] = []
    L.append(f"  PILLAR {pr.get('pillar') or '?'}")
    L.append(f"    CSV input        : {pr['csv_path'] or '(no output found)'}")
    L.append(f"    {'─'*54}")
    L.append(f"    KNOWN indicators : {s['known_matched']}/{s['known_total']} matched")
    if pr.get("matched_known"):
        L.append(f"      ✓ matched : {', '.join(pr['matched_known'])}")
    if pr.get("missed_known"):
        L.append(f"      ✗ missed  : {', '.join(pr['missed_known'])}")
    L.append(f"    NEW provisions   : {s['new_discovered']} discovered")
    for np in pr.get("new_provisions", []):
        art = (np.get("article") or "").strip()
        L.append(f"      + [{np.get('indicator_id','')}] {np.get('law_name','')}"
                 + (f" — {art}" if art else ""))
    return "\n".join(L)


def _format_report(report: dict) -> str:
    # Grouped (per-pillar) report.
    if "pillars" in report:
        L = ["=" * 62, f"  RDTII EVALUATION REPORT — {report['economy']}", "=" * 62]
        for i, pr in enumerate(report["pillars"]):
            if i:
                L.append("  " + "─" * 58)
            L.append(_format_pillar_section(pr))
        ov = report["overall"]
        L.append("=" * 62)
        L.append(f"  SUMMARY          : {ov['known_matched']}/{ov['known_total']} known indicators matched"
                 f"  ·  {ov['new_discovered']} new provisions discovered")
        L.append("=" * 62)
        return "\n".join(L)
    # Flat single-pillar report (back-compat).
    return ("=" * 62 + "\n"
            + f"  RDTII EVALUATION REPORT — {report['economy']} | Pillar {report.get('pillar') or '?'}\n"
            + "=" * 62 + "\n" + _format_pillar_section(report) + "\n" + "=" * 62)


def _print_report(report: dict) -> None:
    print("\n" + _format_report(report) + "\n")


def _ascii(text: str) -> str:
    """Make text safe for fpdf2's latin-1 core fonts."""
    return (str(text).replace("✓", "[match]").replace("✗", "[miss]")
            .replace("—", "-").replace("–", "-").replace("─", "-")
            .encode("latin-1", "replace").decode("latin-1"))


def write_report_pdf(report: dict, path: Path) -> Path | None:
    """Render the grouped report as a formatted PDF (fpdf2). Returns None if fpdf
    is unavailable."""
    try:
        from fpdf import FPDF
    except ImportError:
        return None

    sections = report.get("pillars", [report])
    pdf = FPDF()
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def line(text: str, h: float = 6.0, bold: bool = False, size: int = 11,
             fill: tuple | None = None, fg: tuple = (0, 0, 0)) -> None:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B" if bold else "", size)
        pdf.set_text_color(*fg)
        if fill:
            pdf.set_fill_color(*fill)
        pdf.multi_cell(0, h, _ascii(text), new_x="LMARGIN", new_y="NEXT", fill=bool(fill))
        pdf.set_text_color(0, 0, 0)

    line("RDTII Evaluation Report", h=10, bold=True, size=18)
    line(f"Economy: {report.get('economy','')}", h=6, size=11, fg=(90, 90, 90))
    line(f"Generated: {datetime.now():%Y-%m-%d %H:%M}", h=6, size=11, fg=(90, 90, 90))
    pdf.ln(2)

    for pr in sections:
        s = pr["scores"]
        line(f"  Pillar {pr.get('pillar','?')}",
             h=9, bold=True, size=13, fill=(33, 73, 125), fg=(255, 255, 255))
        pdf.ln(1)
        line(f"KNOWN indicators: {s['known_matched']}/{s['known_total']} matched",
             h=6.5, bold=True, size=11)
        if pr.get("matched_known"):
            line("  Matched: " + ", ".join(pr["matched_known"]), h=5.5, size=10)
        if pr.get("missed_known"):
            line("  Missed: " + ", ".join(pr["missed_known"]), h=5.5, size=10, fg=(170, 0, 0))
        line(f"NEW provisions: {s['new_discovered']} discovered",
             h=6.5, bold=True, size=11)
        for np in pr.get("new_provisions", []):
            art = (np.get("article") or "").strip()
            line(f"  + [{np.get('indicator_id','')}] {np.get('law_name','')}" + (f" - {art}" if art else ""),
                 h=5.5, size=10)
        pdf.ln(3)

    ov = report.get("overall")
    if ov:
        line(f"Summary: {ov['known_matched']}/{ov['known_total']} known indicators matched"
             f"  -  {ov['new_discovered']} new provisions discovered",
             h=10, bold=True, size=12, fill=(225, 225, 225))

    pdf.output(str(path))
    return path


def write_report(report: dict, report_dir: Path) -> list[Path]:
    """Write the evaluation report as a formatted PDF (only)."""
    report_dir.mkdir(parents=True, exist_ok=True)
    econ = re.sub(r"[^A-Za-z0-9]+", "_", report["economy"]).strip("_") or "economy"
    ov = report.get("overall", {})
    pillars = ov.get("pillars_evaluated") or ([report.get("pillar")] if report.get("pillar") else [6, 7])
    pillar_tag = "P" + "-".join(str(p) for p in pillars)
    ts = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    stem = f"{econ}_evaluation_{pillar_tag}_{ts}"

    pdf_path = write_report_pdf(report, report_dir / f"{stem}.pdf")
    return [pdf_path] if pdf_path else []


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
    parser.add_argument(
        "--report-dir", default="outputs/reports",
        help="Directory to write the evaluation report (JSON + TXT)"
    )
    parser.add_argument(
        "--no-report-file", action="store_true",
        help="Print to console only; do not write a report file"
    )
    args = parser.parse_args()

    report = build_economy_report(
        sample_kit_dir=Path(args.sample_kit),
        economy=args.economy,
        pillar=args.pillar,
        csv_path=Path(args.csv) if args.csv else None,
        output_dir=Path(args.output_dir),
    )
    _print_report(report)

    if not args.no_report_file:
        paths = write_report(report, Path(args.report_dir))
        if paths:
            print(f"  PDF report       : {paths[0]}\n")
        else:
            print("  PDF report       : not written (fpdf2 unavailable — pip install fpdf2)\n")


if __name__ == "__main__":
    main()
