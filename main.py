"""
RDTII Extraction Engine — CLI entry point.

Usage:
    python main.py --economy Singapore --pillar 7
    python main.py --economy Singapore --pillar 6 --pdf path/to/law.pdf
    python main.py --economy Singapore --pillar 7 --output-dir outputs/ --format both

Wires together Zone 1 (Evidence Discovery) and Zone 2 (Intelligent Mapping).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from src.config.economy_config import InvalidEconomyConfigError, UnknownEconomyError, load_economy
from src.crawler.exceptions import ConfigError
from src.crawler.probe import load_taxonomy, validate_taxonomy


# ISO code lookup for economy names
_ECONOMY_ISO = {
    "singapore": "SG",
    "australia": "AU",
    "malaysia": "MY",
    "thailand": "TH",
    "india": "IN",
    "indonesia": "ID",
}


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="RDTII Extraction Engine — map regulatory provisions to RDTII indicators"
    )
    p.add_argument("--economy", required=True, help="Economy name (e.g. Singapore)")
    p.add_argument(
        "--pillar", required=True, type=int, choices=[6, 7],
        help="RDTII pillar number (6 or 7)"
    )
    p.add_argument(
        "--output-dir", default="outputs",
        help="Directory for CSV/JSON output (default: outputs/)"
    )
    p.add_argument(
        "--format", choices=["csv", "json", "both"], default="both",
        help="Output format (default: both)"
    )
    p.add_argument(
        "--pdf", default=None,
        help="Skip crawler: run Zone 2 directly on this PDF file"
    )
    return p


def run_pipeline(
    economy: str,
    pillar: int,
    output_dir: Path | str = "outputs",
    fmt: str = "both",
    pdf_path: str | None = None,
) -> dict:
    """
    Full end-to-end pipeline.

    Args:
        economy: Economy name (e.g. "Singapore")
        pillar: RDTII pillar (6 or 7)
        output_dir: Directory for output files
        fmt: "csv" | "json" | "both"
        pdf_path: If set, skip crawler and process this PDF directly

    Returns:
        write_outputs() summary dict
    """
    from src.fetcher.models import Zone1Result
    from src.fetcher.router import route
    from src.fetcher.translator import translate_document
    from src.mapping.llm_client import pin_active_provider
    from src.mapping.mapper import check_pdpa_gate, extract_provisions
    from src.output.cost_logger import CostLogger
    from src.output.models import OutputRecord
    from src.output.validator import validate_and_flag
    from src.output.writer import build_output_record, write_outputs
    from src.retrieval.rag import retrieve_batch

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Load economy config ─────────────────────────────────────────────────────
    try:
        economy_config = load_economy(economy)
    except UnknownEconomyError as exc:
        print(f"[ERROR] Unknown economy: {exc}", file=sys.stderr)
        sys.exit(1)
    except InvalidEconomyConfigError as exc:
        print(f"[ERROR] Invalid economy config: {exc}", file=sys.stderr)
        sys.exit(1)

    economy_iso = _ECONOMY_ISO.get(economy.lower(), economy[:2].upper())

    # ── Pin LLM provider once ───────────────────────────────────────────────────
    try:
        pinned = pin_active_provider()
        print(f"LLM provider: {pinned.provider_name}/{pinned.model}")
    except Exception as exc:
        print(f"[ERROR] No LLM provider available: {exc}", file=sys.stderr)
        sys.exit(1)

    # ── Zone 1: Evidence Discovery OR single-PDF mode ──────────────────────────
    if pdf_path:
        zone1_results = _build_zone1_from_pdf(pdf_path, economy_iso)
    else:
        zone1_results = _run_zone1(economy, pillar, economy_config)

    if not zone1_results:
        print("[ERROR] Zone 1 produced no documents.", file=sys.stderr)
        sys.exit(1)

    print(f"Zone 1: {len(zone1_results)} document(s) to process")

    # ── Zone 2: Intelligent Mapping ─────────────────────────────────────────────
    cost_logger = CostLogger(economy=economy, pillar=pillar, pdf_path=pdf_path or "")
    indicator_ids = [f"P{pillar}-I{i}" for i in range(1, 6)]
    all_records: list[OutputRecord] = []

    for i, z1 in enumerate(zone1_results):
        print(f"  [{i+1}/{len(zone1_results)}] {z1.act_title} ({z1.url})")
        t_doc = time.monotonic()

        # Fetch + route
        try:
            fetched = route(z1, economy_config)
        except Exception as exc:
            print(f"    Fetch/route failed: {exc}", file=sys.stderr)
            continue

        docs = fetched if isinstance(fetched, list) else [fetched]

        for doc in docs:
            # Translate if needed
            try:
                translated = translate_document(doc, economy_config)
            except Exception:
                translated = doc  # use untranslated on failure

            # RAG
            try:
                rag_results = retrieve_batch(indicator_ids, translated)
            except Exception as exc:
                print(f"    RAG failed: {exc}", file=sys.stderr)
                continue

            # LLM extraction
            try:
                results, llm_cost = extract_provisions(rag_results, translated)
            except Exception as exc:
                print(f"    LLM extraction failed: {exc}", file=sys.stderr)
                continue

            # Validate + archive + confidence flag
            try:
                validated = validate_and_flag(results)
            except Exception as exc:
                print(f"    Validation failed: {exc}", file=sys.stderr)
                validated = []

            elapsed = time.monotonic() - t_doc
            cer = getattr(doc, "cer_score", None)

            for vr in validated:
                record = build_output_record(
                    vr,
                    ocr_quality_cer=cer,
                    processing_time_seconds=elapsed,
                )
                all_records.append(record)

            # Record LLM cost
            for _, call_data in llm_cost.per_indicator.items():
                cost_logger.record_llm_call(
                    provider=call_data.get("provider", "unknown"),
                    model=call_data.get("model", "unknown"),
                    input_tokens=call_data.get("input_tokens", 0),
                    output_tokens=call_data.get("output_tokens", 0),
                    latency_ms=call_data.get("latency_ms", 0),
                )

    # ── PDPA gate (Singapore only) ──────────────────────────────────────────────
    if economy_iso == "SG" and pillar == 7:
        p7_high = [r for r in all_records
                   if r.indicator_id.startswith("P7") and (r.confidence or 0.0) >= 0.80]
        if not p7_high:
            print(
                "[WARN] PDPA gate: no P7 provision with confidence >= 0.80 found. "
                "Verify PDPA text was extracted correctly before expanding to other economies."
            )

    # ── Write outputs ───────────────────────────────────────────────────────────
    cost_logger.save(log_dir=Path("logs"))

    summary = write_outputs(
        records=all_records,
        output_dir=output_dir,
        economy=economy,
        pillar=pillar,
        skip_invalid=True,
    )

    return summary


def _build_zone1_from_pdf(pdf_path: str, economy_iso: str) -> list:
    """Build minimal Zone1Result list from a local PDF path."""
    from src.fetcher.models import Zone1Result

    path = Path(pdf_path)
    if not path.exists():
        print(f"[ERROR] PDF not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    return [Zone1Result(
        url=path.resolve().as_uri(),
        economy=economy_iso,
        act_title=path.stem.replace("_", " ").replace("-", " ").title(),
        discovery_tag="KNOWN",
        archive_url="",
    )]


def _run_zone1(economy: str, pillar: int, economy_config) -> list:
    """Run Zone 1 pipeline: probe → crawl → currency check → rank."""
    from src.fetcher.models import Zone1Result

    try:
        from src.crawler.probe import run_probe
        from src.crawler.crawler import run_crawler, load_known_urls
        from src.crawler.currency import run_currency_check
        from src.crawler.ranker import run_ranker
        from src.crawler.seed_loader import load_seed_data
    except ImportError as exc:
        print(f"[WARN] Zone 1 module not available ({exc}), using empty document list.")
        return []

    economy_iso = _ECONOMY_ISO.get(economy.lower(), economy[:2].upper())
    taxonomy = load_taxonomy("taxonomy.json")

    _ROUND1_DB = "data/sample_kit/ESCAP-RDTII-2.1_ Round 1 Database.xlsx"

    # Probe portals
    try:
        probe_results = asyncio.run(run_probe(economy_config, taxonomy))
    except Exception as exc:
        print(f"[WARN] Probe failed: {exc}", file=sys.stderr)
        return []

    active_probes = [p for p in probe_results if p.is_active]
    if not active_probes:
        print(f"[WARN] No active portals found for {economy}.", file=sys.stderr)
        return []

    # Load known URLs for Pass 1 seeding
    known_urls = load_known_urls(_ROUND1_DB, economy_name=economy)

    # Crawl for candidate acts (Pass 1 KNOWN + Pass 2 NEW)
    try:
        candidate_acts = asyncio.run(
            run_crawler(probe_results, economy_config, taxonomy, known_urls)
        )
    except Exception as exc:
        print(f"[WARN] Crawler failed: {exc}", file=sys.stderr)
        return []

    # Currency check
    try:
        currency_results = asyncio.run(run_currency_check(candidate_acts))
    except Exception as exc:
        print(f"[WARN] Currency check failed: {exc}", file=sys.stderr)
        currency_results = candidate_acts  # use raw candidates as fallback

    # Load seed data + rank
    try:
        seed_data = load_seed_data(
            economy_iso=economy_iso,
            pillar=f"P{pillar}",
            round1_db_path=_ROUND1_DB,
        )
        ranked = run_ranker(currency_results, seed_data, taxonomy, economy_config, output_dir="logs")
    except Exception as exc:
        print(f"[WARN] Ranker failed: {exc}", file=sys.stderr)
        ranked = currency_results  # fall back to all currency results

    # Convert to Zone1Result
    zone1_results = []
    for act in ranked:
        url = getattr(act, "act_url", None) or getattr(act, "url", "")
        title = getattr(act, "act_title", "") or getattr(act, "title", "Unknown")
        tag = getattr(act, "discovery_tag", "KNOWN")
        archive = getattr(act, "archive_url", "")
        zone1_results.append(Zone1Result(
            url=url,
            economy=economy_iso,
            act_title=title,
            discovery_tag=tag,
            archive_url=archive or "",
        ))

    return zone1_results


def main() -> None:
    # ── Startup: load and validate taxonomy.json ───────────────────────────────
    try:
        taxonomy = load_taxonomy("taxonomy.json")
        validate_taxonomy(taxonomy)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    except ConfigError as exc:
        print(f"[ERROR] taxonomy.json validation failed: {exc}", file=sys.stderr)
        sys.exit(1)

    parser = _build_argparser()
    args = parser.parse_args()

    print(f"\nRDTII Engine | Economy: {args.economy} | Pillar: {args.pillar}")
    print(f"Output dir: {args.output_dir} | Format: {args.format}")
    if args.pdf:
        print(f"PDF mode: {args.pdf}")
    print()

    summary = run_pipeline(
        economy=args.economy,
        pillar=args.pillar,
        output_dir=Path(args.output_dir),
        fmt=args.format,
        pdf_path=args.pdf,
    )

    print(f"\nDone. {summary['written']} records written.")


if __name__ == "__main__":
    main()
