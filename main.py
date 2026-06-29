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
from collections import defaultdict
from pathlib import Path

import logging
from dotenv import load_dotenv
load_dotenv()  # load .env before any provider/config imports read os.environ

from src.cli.progress import Progress
from src.config.economy_config import InvalidEconomyConfigError, UnknownEconomyError, load_economy
from src.crawler.exceptions import ConfigError
from src.crawler.probe import load_taxonomy, validate_taxonomy
from src.mapping.exceptions import PDPAGateError

logger = logging.getLogger("main")

# Round 1 ground-truth DB. Lives under data/database/; data/sample_kit/ kept as a
# fallback for older checkouts. Resolved at call time so a moved file is found
# rather than silently disabling seed loading (which breaks KNOWN tagging).
_ROUND1_DB_CANDIDATES = (
    "data/database/ESCAP-RDTII-2.1_ Round 1 Database.xlsx",
    "data/sample_kit/ESCAP-RDTII-2.1_ Round 1 Database.xlsx",
)


def _resolve_round1_db() -> str | None:
    for path in _ROUND1_DB_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="RDTII Extraction Engine — map regulatory provisions to RDTII indicators"
    )
    p.add_argument("--economy", required=True, help="Economy name (e.g. Singapore)")
    p.add_argument(
        "--pillar", required=True, type=int,
        help="RDTII pillar number (e.g. 6, 7, 8)"
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
        economy: Economy name (e.g. "Singapore", "Viet Nam")
        pillar: RDTII pillar number
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
    from src.retrieval.config import load_taxonomy as _load_taxonomy
    from src.retrieval.rag import retrieve_batch

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    from src.cli.progress import set_progress
    p = Progress()
    set_progress(p)

    # ── Load economy config ─────────────────────────────────────────────────────
    p.step(f"Loading economy config — {economy}")
    try:
        economy_config = load_economy(economy)
    except UnknownEconomyError as exc:
        p.fail(f"Unknown economy: {exc}")
        sys.exit(1)
    except InvalidEconomyConfigError as exc:
        p.fail(f"Invalid economy config: {exc}")
        sys.exit(1)
    economy_iso = economy_config.iso_code
    p.done(f"Economy config — {economy} ({economy_iso})")

    # ── Pin LLM provider once ───────────────────────────────────────────────────
    p.step("Connecting to LLM provider")
    try:
        pinned = pin_active_provider()
        p.done(f"LLM provider — {pinned.provider_name}/{pinned.model}")
    except Exception as exc:
        p.fail(f"No LLM provider available: {exc}")
        sys.exit(1)

    # ── Load seed data ──────────────────────────────────────────────────────────
    from src.crawler.seed_loader import load_seed_data as _load_seed
    _ROUND1_DB = _resolve_round1_db()
    if _ROUND1_DB is None:
        p.warn("Round 1 DB not found in data/database/ or data/sample_kit/ — KNOWN tagging disabled")
    p.step("Loading Round 1 seed data")
    try:
        _seed = _load_seed(
            economy_iso=economy_iso,
            pillar=f"P{pillar}",
            round1_db_path=_ROUND1_DB,
            economy_name=economy_config.economy_name,
        )
        known_provisions = _seed.known_provisions
        p.done(f"Seed data — {len(_seed.known_titles)} known acts, {len(known_provisions)} provisions")
    except Exception as exc:
        p.warn(f"Seed data unavailable ({exc}) — continuing without")
        known_provisions = set()

    # ── Zone 1: Evidence Discovery OR single-PDF mode ──────────────────────────
    if pdf_path:
        p.step(f"Loading PDF — {pdf_path}")
        zone1_results = _build_zone1_from_pdf(pdf_path, economy_iso)
        p.done(f"PDF loaded — {zone1_results[0].act_title}")
    else:
        zone1_results = _run_zone1(economy, pillar, economy_config, p)

    if not zone1_results:
        p.fail("Zone 1 produced no documents")
        sys.exit(1)

    p.info(f"Zone 1 complete — {len(zone1_results)} document(s) to process")

    # ── Zone 2: Intelligent Mapping ─────────────────────────────────────────────
    cost_logger = CostLogger(economy=economy, pillar=pillar, pdf_path=pdf_path or "")
    indicator_ids = [
        e.indicator_id for e in _load_taxonomy()
        if e.indicator_id.startswith(f"P{pillar}-")
    ]
    if not indicator_ids:
        p.fail(f"No indicators found in taxonomy.json for pillar {pillar}")
        sys.exit(1)

    all_records: list[OutputRecord] = []
    n = len(zone1_results)

    # Lightweight per-stage timing → printed as a summary at the end so we can
    # see where wall-clock goes (fetch vs RAG vs LLM vs validate) against budget.
    stage_times: dict[str, float] = defaultdict(float)

    for i, z1 in enumerate(zone1_results):
        title = z1.act_title or z1.url
        prefix = f"[{i+1}/{n}]"

        p.step(f"{prefix} Fetching — {title}")
        _t = time.monotonic()
        try:
            fetched = route(z1, economy_config)
        except Exception as exc:
            p.fail(f"{prefix} Fetch failed — {exc}")
            continue
        finally:
            stage_times["fetch"] += time.monotonic() - _t
        docs = fetched if isinstance(fetched, list) else [fetched]
        p.done(f"{prefix} Fetched — {title}")

        for doc in docs:
            p.step(f"{prefix} Translating")
            _t = time.monotonic()
            try:
                translated = translate_document(doc, economy_config)
                p.done(f"{prefix} Translation done")
            except Exception as exc:
                p.warn(f"{prefix} Translation failed — using raw text")
                doc.flag_for_review = True
                translated = doc
            finally:
                stage_times["translate"] += time.monotonic() - _t

            p.step(f"{prefix} RAG retrieval — {len(indicator_ids)} indicators")
            _t = time.monotonic()
            try:
                rag_results = retrieve_batch(indicator_ids, translated)
                p.done(f"{prefix} RAG retrieval done")
            except Exception as exc:
                p.fail(f"{prefix} RAG failed — {exc}")
                continue
            finally:
                stage_times["rag"] += time.monotonic() - _t

            p.step(f"{prefix} LLM extraction — {pinned.provider_name}/{pinned.model}")
            _t = time.monotonic()
            try:
                results, llm_cost = extract_provisions(rag_results, translated, known_provisions)
                found = sum(1 for r in results if getattr(r, "found", False))
                p.done(f"{prefix} LLM extraction — {found}/{len(indicator_ids)} indicators matched")
            except Exception as exc:
                p.fail(f"{prefix} LLM extraction failed — {exc}")
                continue
            finally:
                stage_times["llm"] += time.monotonic() - _t

            p.step(f"{prefix} Validating output")
            _t = time.monotonic()
            try:
                validated = validate_and_flag(results)
                p.done(f"{prefix} Validation — {len(validated)} records")
            except Exception as exc:
                p.warn(f"{prefix} Validation failed — {exc}")
                validated = []
            finally:
                stage_times["validate"] += time.monotonic() - _t

            cer = getattr(doc, "cer_score", None)
            t_doc = time.monotonic()
            for vr in validated:
                record = build_output_record(
                    vr,
                    ocr_quality_cer=cer,
                    processing_time=int(time.monotonic() - t_doc),
                )
                all_records.append(record)

            for _, call_data in llm_cost.per_indicator.items():
                cost_logger.record_llm_call(
                    provider=call_data.get("provider", "unknown"),
                    model=call_data.get("model", "unknown"),
                    input_tokens=call_data.get("input_tokens", 0),
                    output_tokens=call_data.get("output_tokens", 0),
                    latency_ms=call_data.get("latency_ms", 0),
                )

    # ── Null assessments ────────────────────────────────────────────────────────
    # RDTII scores EVERY indicator (0/0.5/1). For an indicator the Round 1 DB
    # assessed but where we found no qualifying provision, emit an explicit
    # "no barrier (score 0)" record — Round 1 itself documents these (e.g. SG 6.3:
    # "Singapore does not implement infrastructure requirement"). Without this the
    # indicator is silently absent and reads as a miss, not a documented null.
    null_records = _emit_null_assessments(
        all_records, _seed, economy_config.economy_name, economy_config,
    )
    if null_records:
        all_records.extend(null_records)
        p.info(f"Null assessments — {len(null_records)} indicator(s) assessed as no barrier")

    # ── PDPA gate (Singapore only) ──────────────────────────────────────────────
    if economy_iso == "SG" and pillar == 7:
        p.step("PDPA compliance gate check")
        try:
            check_pdpa_gate(economy_iso, all_records)
            p.done("PDPA gate passed")
        except PDPAGateError as exc:
            p.fail(f"PDPA gate failed: {exc}")
            sys.exit(1)

    # ── Write outputs ───────────────────────────────────────────────────────────
    p.step("Writing outputs")
    cost_logger.save(log_dir=Path("logs"))
    summary = write_outputs(
        records=all_records,
        output_dir=output_dir,
        # Canonical economy name for the filename, not whatever short form the
        # user passed (e.g. "sg" → "Singapore"), so outputs are named consistently.
        economy=economy_config.economy_name,
        pillar=pillar,
        skip_invalid=True,
    )
    p.done(f"Outputs written — {summary.get('written', 0)} records → {output_dir}/")

    from src.output.cost_logger import CostLogger as _CL
    report = cost_logger.to_report()
    p.summary(
        records=summary.get("written", 0),
        cost_usd=report.get("total_cost_usd", 0.0),
    )

    # Per-stage wall-clock summary (helps decide where remaining runtime goes).
    if stage_times:
        total = sum(stage_times.values())
        parts = "  ".join(
            f"{name}={secs:.1f}s ({secs / total * 100:.0f}%)"
            for name, secs in sorted(stage_times.items(), key=lambda kv: -kv[1])
        )
        p.info(f"Stage timing — {parts}  | tracked total {total:.1f}s")

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


def _run_zone1(economy: str, pillar: int, economy_config, p: "Progress | None" = None) -> list:
    """
    Run Zone 1 via the pillar-agnostic per-portal strategy.

    Replaces the old probe → BFS crawl → currency → rank pipeline with a
    single discover() step driven by strategy fields in economies/*.yaml.
    Falls back to KNOWN seed URLs if all discovery fails.
    """
    try:
        from src.crawler.discover import discover
        from src.crawler.seed_loader import load_seed_data
    except ImportError as exc:
        print(f"[WARN] Zone 1 module not available ({exc}), using empty document list.")
        return []

    def _p_step(msg: str) -> None:
        if p: p.step(msg)
    def _p_done(msg: str) -> None:
        if p: p.done(msg)
    def _p_warn(msg: str) -> None:
        if p: p.warn(msg)

    economy_iso = economy_config.iso_code
    taxonomy = load_taxonomy("taxonomy.json")
    _ROUND1_DB = _resolve_round1_db()

    # Load Round 1 known URLs for this pillar (seed for merge + KNOWN tag)
    _p_step(f"Zone 1 — Loading seed data for {economy} P{pillar}")
    try:
        seed_data = load_seed_data(
            economy_iso=economy_iso,
            pillar=f"P{pillar}",
            round1_db_path=_ROUND1_DB,
            economy_name=economy_config.economy_name,
        )
        known_urls = seed_data.known_urls
        _match_mode = "by title" if not known_urls else "by url+title"
        _p_done(
            f"Zone 1 — Seed loaded — {len(seed_data.known_titles)} known act(s), "
            f"{len(known_urls)} URL(s) (matching {_match_mode})"
        )
    except Exception as exc:
        _p_warn(f"Zone 1 — Seed load failed ({exc}) — continuing with empty seed")
        known_urls = set()

    # Discover acts via portal strategy
    _p_step(f"Zone 1 — Discovering acts for {economy} P{pillar} via portal strategy")
    try:
        zone1_results = asyncio.run(
            discover(
                economy_config, pillar, taxonomy, known_urls,
                known_titles=getattr(seed_data, "known_titles", None),
                known_titles_by_indicator=getattr(seed_data, "known_titles_by_indicator", None),
            )
        )
        known_count = sum(1 for z in zone1_results if z.discovery_tag == "KNOWN")
        new_count   = sum(1 for z in zone1_results if z.discovery_tag == "NEW")
        _p_done(
            f"Zone 1 — Discovery complete — {len(zone1_results)} act(s) "
            f"({known_count} KNOWN, {new_count} NEW)"
        )
    except Exception as exc:
        _p_warn(f"Zone 1 — Discovery failed ({exc}) — falling back to seed URLs")
        from src.fetcher.models import Zone1Result
        zone1_results = [
            Zone1Result(url=u, economy=economy_iso, act_title="", discovery_tag="KNOWN", archive_url="")
            for u in known_urls
        ]

    return zone1_results


def _emit_null_assessments(all_records, seed, economy_name, economy_config) -> list:
    """
    Emit explicit "no barrier (score 0)" records for indicators the Round 1 DB
    assessed for this economy+pillar but where the engine found no qualifying
    provision. Pillar-agnostic; driven by the seed's indicator→act mapping, so it
    only documents indicators that were actually in scope (never invents records
    for indicators Round 1 didn't assess). Flagged for human review, since an
    absence can also indicate a retrieval gap rather than a true score of 0.
    """
    from src.output.models import OutputRecord
    from src.mapping.prompts import load_taxonomy_dict

    assessed = getattr(seed, "known_titles_by_indicator", None) or {}
    if not assessed:
        return []

    found = {r.indicator_id for r in all_records}
    taxonomy = load_taxonomy_dict()
    ref_to_id = {e.get("rdtii_ref"): iid for iid, e in taxonomy.items()}
    portal_url = str(economy_config.portals[0].url) if economy_config.portals else ""

    nulls: list = []
    for rdtii_ref, titles in sorted(assessed.items()):
        indicator_id = ref_to_id.get(rdtii_ref)
        if not indicator_id or indicator_id in found:
            continue
        act = sorted(titles)[0].title() if titles else f"{economy_name} legislation reviewed"
        nulls.append(OutputRecord(
            economy=economy_name,
            law_name=act,
            law_number_ref=None,
            last_amended=None,
            indicator_id=indicator_id,
            article="N/A",
            discovery_tag="KNOWN",
            location_reference=None,
            verbatim_snippet="[No qualifying provision identified — assessed as no barrier]",
            mapping_rationale=(
                f"No provision establishing this requirement was identified in the "
                f"reviewed legislation; assessed as no barrier (RDTII score 0)."
            ),
            source_url=portal_url,
            confidence=None,
            notes="Recommend human review — null/no-barrier assessment; absence may indicate a retrieval gap.",
            ocr_quality_cer=None,
            processing_time=None,
            model_version="",
            source_pdf_path=None,
            raw_context_before="",
            raw_context_after="",
            verbatim_original=None,
            archive_url="",
            doc_discovery_tag="KNOWN",
        ))
    return nulls


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
