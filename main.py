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
import json
import os
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
    from src.fetcher.router import route, _find_portal_for_url
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

    # Surface the active run profile up front so a submission is never accidentally
    # run under the safe build-gate settings (NEW-act discovery off).
    from src.crawler.discover import RUN_PROFILE as _RUN_PROFILE, _MAX_NEW_ACTS as _NEW_CAP
    p.info(f"Run profile — {_RUN_PROFILE} (NEW-act cap {_NEW_CAP}"
           + ("; KNOWN-only — set RUN_PROFILE=submit for NEW discovery)" if _NEW_CAP == 0 else ")"))

    # Retrieval strategy by economy language. English-only economies (SG, AU) use the
    # English embedder/reranker (best English retrieval → build gate preserved) and
    # translate nothing. Non-English economies use the multilingual models so RAG runs
    # on the ORIGINAL text and only retrieved passages reach the LLM — no whole-doc
    # translation (Malaysia P7: 50 min → ~7 min).
    _english_only = all(str(lang).lower().strip() == "en" for lang in economy_config.languages)
    _translate_body = _english_only
    from src.retrieval.embedder import set_multilingual as _set_embed_ml
    from src.retrieval.reranker import set_multilingual as _set_rerank_ml
    _set_embed_ml(not _english_only)
    _set_rerank_ml(not _english_only)

    # Ensure the Tesseract language packs this economy needs are installed (e.g.
    # Malay 'msa'); best-effort, config-driven. Missing packs otherwise crash local
    # OCR — cloud Stage 2 still covers it, but installing avoids the round-trip.
    try:
        from src.fetcher.extractors.ocr_stage1 import ensure_tesseract_langs
        ensure_tesseract_langs(economy_config)
    except Exception as exc:  # never let a bootstrap step abort the run
        p.warn(f"Tesseract language bootstrap skipped: {exc}")

    # Pre-download Argos translation models for this economy's languages (offline
    # primary translator). Config-driven, best-effort; avoids a mid-run stall.
    try:
        from src.fetcher.translator import ensure_argos_langs
        ensure_argos_langs(economy_config)
    except Exception as exc:
        p.warn(f"Argos language bootstrap skipped: {exc}")

    # ── Pin LLM provider once ───────────────────────────────────────────────────
    p.step("Connecting to LLM provider")
    try:
        pinned = pin_active_provider()
        p.done(f"LLM provider — {pinned.provider_name}/{pinned.model}")
    except Exception as exc:
        p.fail(f"No LLM provider available: {exc}")
        sys.exit(1)

    # Fail fast: prove the LLM can actually return parseable output BEFORE the run.
    # An available key that is dead (quota/auth), a thinking-only model, or a prompt
    # that overruns the context window all produce nothing — and the pipeline would
    # then silently write every provision as a false "no barrier" (N/A) null. One
    # tiny call here surfaces that in seconds instead of after a full run.
    from src.mapping.llm_client import smoke_check_llm
    p.step("Verifying LLM can produce output")
    _llm_ok, _llm_detail = smoke_check_llm()
    if not _llm_ok:
        p.fail(f"LLM smoke-check failed — {_llm_detail}")
        p.info(
            "The provider is configured but cannot produce usable output. Fix it before "
            "running — otherwise every provision would be written as a false 'no barrier' (N/A). "
            "Common causes: dead/quota'd API key, wrong LLM_MODEL, or (local Ollama) OLLAMA_NUM_CTX too small."
        )
        sys.exit(1)
    p.done(f"LLM ready — {_llm_detail}")

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
        known_sections = _seed.known_sections
        known_sections_by_indicator = _seed.known_sections_by_indicator
        p.done(
            f"Seed data — {len(_seed.known_titles)} known acts, {len(known_provisions)} anchored "
            f"provisions, {len(known_sections)} act(s) with prose sections"
        )
        # Audit seed-URL domains against portal config so any domain lacking a
        # declared fetch strategy (→ blind auto) is surfaced up front, not silently.
        from src.crawler.discover import audit_seed_domains
        _audit = audit_seed_domains(_seed.known_urls, economy_config)
        _uncovered = [d for d, _c, name, _disc, _f in _audit if name is None]
        for domain, count, name, disc, fetch in _audit:
            if name:
                p.info(f"Seed domain {domain} ({count}) → {name} [{disc}/{fetch}]")
            else:
                p.warn(f"Seed domain {domain} ({count}) → no portal config — fetched via default auto")
        if _uncovered:
            p.warn(f"{len(_uncovered)} seed domain(s) lack a config strategy: {', '.join(_uncovered)}")
    except Exception as exc:
        p.warn(f"Seed data unavailable ({exc}) — continuing without")
        known_provisions = set()
        known_sections = {}
        known_sections_by_indicator = {}

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

        # Record real OCR cost (Mistral/Azure/LLM-vision compute it into cost_log_entry;
        # local Tesseract/Paddle/pdfplumber are $0). Keeps logs/cost_report.json truthful.
        for doc in docs:
            cle = getattr(doc, "cost_log_entry", None)
            if cle is not None:
                cost_logger.record_ocr_page(
                    engine=doc.extraction_method,
                    pages=doc.page_count or 1,
                    latency_ms=cle.processing_time_ms,
                    cost_usd=cle.cost_usd,
                )

        # Source authority: the portal's declared type (primary legislation vs
        # secondary regulator guidance). Drives a review flag on provisions
        # extracted from non-primary sources — a summary/guidance page is not the
        # binding statute, so its "provisions" need verification. Economy-agnostic:
        # read straight from the matched portal's YAML `type` field.
        _src_portal = _find_portal_for_url(getattr(docs[0], "source_url", ""), economy_config)
        portal_type = getattr(_src_portal, "type", "primary") if _src_portal else "primary"

        for doc in docs:
            p.step(f"{prefix} Translating")
            _t = time.monotonic()
            try:
                # Non-English economies: RAG retrieves over the ORIGINAL text via the
                # multilingual embedder, so the body is NOT translated (was ~1.5M chars
                # for MY; now ~0) — the LLM reads retrieved source-language passages.
                # English economies keep the original full flow (translate_body no-op).
                translated = translate_document(doc, economy_config, translate_body=_translate_body)
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
                rag_results = retrieve_batch(
                    indicator_ids, translated,
                    known_sections_by_indicator=known_sections_by_indicator,
                )
                p.done(f"{prefix} RAG retrieval done")
            except Exception as exc:
                p.fail(f"{prefix} RAG failed — {exc}")
                continue
            finally:
                stage_times["rag"] += time.monotonic() - _t

            p.step(f"{prefix} LLM extraction — {pinned.provider_name}/{pinned.model}")
            _t = time.monotonic()
            try:
                results, llm_cost = extract_provisions(
                    rag_results, translated, known_provisions, known_sections,
                    portal_type=portal_type,
                )
                # Count distinct indicators that yielded ≥1 provision (ExtractionResult
                # has no "found" flag — its existence in the list is the match signal).
                found = len({r.indicator_id for r in results})
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

    # ── Cross-document dedup ────────────────────────────────────────────────────
    # The mapper dedups within one document, but the SAME act can be fetched under
    # two URLs (e.g. Cybersecurity Act as consolidated /Act/CA2018 AND as-enacted
    # /acts-supp/9-2018), yielding identical provisions in separate documents. Fold
    # those together, keeping the copy from the consolidated act with the better
    # location reference.
    before = len(all_records)
    all_records = _dedup_cross_document(all_records)
    if before != len(all_records):
        p.info(f"Cross-document dedup — {before - len(all_records)} duplicate provision(s) removed")

    # ── KNOWN cross-indicator prune ─────────────────────────────────────────────
    # KNOWN is ground-truth: drop KNOWN provisions filed under an indicator Round 1
    # never assigns them to (only when a correct-indicator copy survives). NEW is left
    # untouched. Mis-maps are logged for root-cause analysis (see memory).
    all_records, _mismaps = _prune_known_cross_indicator(all_records, known_sections_by_indicator)
    _diag_dir = Path(os.environ.get("RDTII_LOG_DIR", "logs")) / "diagnostics"
    _diag_tag = f"{economy_iso}_P{pillar}"
    if _mismaps:
        _dropped = sum(1 for m in _mismaps if m["dropped"])
        _diag_dir.mkdir(parents=True, exist_ok=True)
        # Per-economy-pillar filename so successive runs accumulate evidence (over-fire).
        (_diag_dir / f"{_diag_tag}_mismaps.json").write_text(
            json.dumps({"economy": economy_config.economy_name, "pillar": pillar,
                        "mismaps": _mismaps}, indent=2),
            encoding="utf-8",
        )
        p.info(
            f"KNOWN indicator prune — {_dropped} wrong-indicator row(s) removed, "
            f"{len(_mismaps)} confirmed mis-map(s) → logs/diagnostics/{_diag_tag}_mismaps.json"
        )

    # ── KNOWN-recall audit (diagnostic only — no output change) ──────────────────
    # For every Round 1 (act, section, indicator), record whether the run emitted it,
    # and classify misses (act_missing = fetch/discovery; provision_missing = LLM
    # rejected / retrieval missed). Evidence for the drift/recall decision (issue A).
    _recall = _audit_known_recall(all_records, known_sections_by_indicator)
    if _recall["found"] or _recall["missing"]:
        _diag_dir.mkdir(parents=True, exist_ok=True)
        (_diag_dir / f"{_diag_tag}_recall.json").write_text(
            json.dumps({"economy": economy_config.economy_name, "pillar": pillar, **_recall}, indent=2),
            encoding="utf-8",
        )
        _pm = sum(1 for m in _recall["missing"] if m["reason"] == "provision_missing")
        p.info(
            f"KNOWN recall audit — {len(_recall['found'])} found, {len(_recall['missing'])} missing "
            f"({_pm} provision_missing) → logs/diagnostics/{_diag_tag}_recall.json"
        )

    # ── Null assessments ────────────────────────────────────────────────────────
    # RDTII scores EVERY indicator (0/0.5/1). For an indicator the Round 1 DB
    # assessed but where we found no qualifying provision, emit an explicit
    # "no barrier (score 0)" record — Round 1 itself documents these (e.g. SG 6.3:
    # "Singapore does not implement infrastructure requirement"). Without this the
    # indicator is silently absent and reads as a miss, not a documented null.
    # Only for full-economy runs: a single provided PDF (--pdf) can't justify a
    # "no barrier" verdict for the economy's other indicators, so skip nulls there.
    null_records = [] if pdf_path else _emit_null_assessments(
        all_records, _seed, economy_config.economy_name, economy_config,
    )
    if null_records:
        all_records.extend(null_records)
        p.info(f"Null assessments — {len(null_records)} indicator(s) assessed as no barrier")

    # ── PDPA gate (Singapore crawl only) ────────────────────────────────────────
    # This is the Phase-1 build gate for the live Singapore P7 crawl. It must NOT
    # apply to --pdf mode, which processes an arbitrary provided document (e.g. a
    # foreign or non-PDPA law) and would otherwise always abort here.
    if economy_iso == "SG" and pillar == 7 and not pdf_path:
        p.step("PDPA compliance gate check")
        try:
            check_pdpa_gate(economy_iso, all_records)
            p.done("PDPA gate passed")
        except PDPAGateError as exc:
            p.fail(f"PDPA gate failed: {exc}")
            sys.exit(1)

    # ── Write outputs ───────────────────────────────────────────────────────────
    p.step("Writing outputs")
    cost_logger.save(log_dir=Path(os.environ.get("RDTII_LOG_DIR", "logs")))
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


def _dedup_cross_document(records: list) -> list:
    """
    Remove provisions duplicated ACROSS documents — the same act fetched under two
    URLs (consolidated /Act/ vs as-enacted /acts-supp/) produces identical rows in
    separate documents that the per-document mapper dedup never sees.

    Key: (law_name, indicator_id, article, snippet[:80]) — whitespace/case-folded,
    so genuinely distinct provisions are never merged. On collision keep the higher
    quality copy: consolidated /Act/ source > higher confidence > a concrete
    (non-"unknown") location reference. Insertion order of first-seen keys is kept.
    """
    def _norm(s: str | None) -> str:
        return " ".join((s or "").split()).lower()

    def _quality(r) -> tuple:
        url = (getattr(r, "source_url", "") or "").lower()
        loc = (getattr(r, "location_reference", "") or "").lower()
        consolidated = 1 if ("/act/" in url and "/acts-supp/" not in url) else 0
        concrete_loc = 0 if (not loc or "unknown" in loc) else 1
        return (consolidated, getattr(r, "confidence", None) or 0.0, concrete_loc)

    best: dict[tuple, object] = {}
    for r in records:
        key = (_norm(r.law_name), r.indicator_id, _norm(r.article), _norm(r.verbatim_snippet)[:80])
        if key not in best or _quality(r) > _quality(best[key]):
            best[key] = r
    return list(best.values())


def _prune_known_cross_indicator(records: list, known_sections_by_indicator: dict) -> tuple[list, list]:
    """
    Drop KNOWN provisions filed under an indicator Round 1 never assigns them to.

    KNOWN is ground-truth: `known_sections_by_indicator` says exactly which indicator(s)
    each (act, section) belongs to. A KNOWN row whose indicator is outside that set is a
    CONFIRMED mis-map. We drop it — but ONLY when the same provision still survives under
    a correct (in-set) indicator, so a known provision is never lost outright.

    NEW provisions are untouched: a NEW may legitimately serve multiple indicators and we
    have no ground truth to prune it safely (dropping one could kill a real 20-pt finding).

    Returns (kept_records, mismaps) where mismaps is a ground-truth-verified list for
    root-cause analysis, each carrying the source chunk's retrieval signal
    (retrieval over-match vs LLM over-fire). See [[known-wrong-indicator-rootcause]].
    """
    from collections import defaultdict

    from src.crawler.seed_loader import match_known_act
    from src.mapping.provision_tag import infer_section_token

    ksbi = known_sections_by_indicator or {}
    if not ksbi:
        return records, []

    all_act_keys = {act for acts in ksbi.values() for act in acts}

    def _round1_indicators(act_norm: str, token: str) -> set:
        return {ind for ind, acts in ksbi.items() if token and token in acts.get(act_norm, set())}

    groups: dict = defaultdict(list)
    passthrough: list = []
    for r in records:
        token = infer_section_token(r.article) if r.discovery_tag == "KNOWN" else None
        # Resolve law_name to its canonical Round 1 key with the SAME fuzzy matcher
        # the tagger uses — otherwise a differently-spelled title ("PDPA") silently
        # bypasses the prune and its wrong-indicator duplicates are never cleaned up.
        act_norm = match_known_act(r.law_name or "", all_act_keys) if token else None
        # Only rows we have ground truth for (KNOWN + resolvable section + in the R1 map)
        if act_norm and _round1_indicators(act_norm, token):
            groups[(act_norm, token)].append(r)
        else:
            passthrough.append(r)

    kept = list(passthrough)
    mismaps: list = []

    def _record_mismap(r, allowed, dropped):
        mismaps.append({
            "economy": r.economy, "law_name": r.law_name, "article": r.article,
            "wrong_indicator": r.indicator_id, "round1_indicators": sorted(allowed),
            "confidence": r.confidence,
            "source_retrieval_method": getattr(r, "source_retrieval_method", None),
            "source_rerank_score": getattr(r, "source_rerank_score", None),
            "dropped": dropped,
        })

    for (act_norm, token), rows in groups.items():
        allowed = _round1_indicators(act_norm, token)
        in_set = [r for r in rows if r.indicator_id in allowed]
        out_set = [r for r in rows if r.indicator_id not in allowed]
        if in_set and out_set:
            kept.extend(in_set)                        # keep the correct copies
            for r in out_set:
                _record_mismap(r, allowed, dropped=True)   # drop the confirmed wrong ones
        else:
            kept.extend(rows)                          # all in-set, OR only wrong copies → keep
            for r in out_set:
                _record_mismap(r, allowed, dropped=False)  # confirmed mis-map but sole evidence

    # Preserve original record order.
    order = {id(r): i for i, r in enumerate(records)}
    kept.sort(key=lambda r: order.get(id(r), len(records)))
    return kept, mismaps


def _audit_known_recall(records: list, known_sections_by_indicator: dict) -> dict:
    """
    Diagnostic (no output change): for every Round 1 KNOWN (act, section, indicator),
    record whether the run emitted it, and classify each miss:
      - "act_missing"       → the act produced no output row at all (fetch/discovery gap)
      - "provision_missing" → the act IS in the output but not this (section, indicator)
                              → the LLM rejected it or retrieval never surfaced it

    This is the under-recall counterpart to the over-fire mis-map log — together they
    tell us which way the drift/recall problem (issue A) actually leans. See
    [[known-wrong-indicator-rootcause]] and [[indicator-drift]].
    """
    from src.crawler.seed_loader import match_known_act, normalise_title
    from src.mapping.provision_tag import infer_section_token

    ksbi = known_sections_by_indicator or {}
    all_act_keys = {act for acts in ksbi.values() for act in acts}

    def _canon(name: str) -> str:
        # Map an emitted law_name to its canonical Round 1 key (fuzzy — same matcher
        # as the tagger), so a KNOWN provision emitted as "PDPA" is still counted as
        # found against the Round 1 "personal data protection act" entry.
        return match_known_act(name or "", all_act_keys) or normalise_title(name or "")

    acts_present = {_canon(r.law_name) for r in records}
    emitted = set()
    for r in records:
        tok = infer_section_token(r.article)
        if tok:
            emitted.add((_canon(r.law_name), tok, r.indicator_id))

    found, missing = [], []
    for indicator, acts in ksbi.items():
        for act, sections in acts.items():
            for section in sections:
                entry = {"act": act, "section": section, "indicator": indicator}
                if (act, section, indicator) in emitted:
                    found.append(entry)
                else:
                    entry["reason"] = "act_missing" if act not in acts_present else "provision_missing"
                    missing.append(entry)
    return {"found": found, "missing": missing}


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

    # known_titles_by_indicator is keyed by engine indicator id ("P6-I2"), the same
    # convention as everything else in the seed (no rdtii_ref → id bridging needed).
    assessed = getattr(seed, "known_titles_by_indicator", None) or {}
    if not assessed:
        return []

    found = {r.indicator_id for r in all_records}
    portal_url = str(economy_config.portals[0].url) if economy_config.portals else ""

    nulls: list = []
    for indicator_id, titles in sorted(assessed.items()):
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
