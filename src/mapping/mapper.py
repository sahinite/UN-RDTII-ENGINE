"""
Mapper orchestrator — ties LLM cascade + prompt builder + parser together.

Public API:
  extract_provisions(rag_results, doc) -> (list[ExtractionResult], LLMCostEntry)
  check_quality_gate(economy, pillar, results)
"""

from __future__ import annotations

import logging
import time

from src.cli.progress import substep
from src.mapping.exceptions import (
    AllProvidersExhaustedError,
    ConfigError,
    PDPAGateError,
    QualityGateError,
)
from src.mapping.llm_client import call_llm_with_cascade, get_active_model_version
from src.mapping.models import ExtractionResult, LLMCostEntry
from src.mapping.parser import expand_non_consecutive, parse_llm_response
from src.mapping.prompts import SYSTEM_PROMPT, build_user_prompt, load_taxonomy_dict, trim_chunks_to_budget

logger = logging.getLogger("mapping.mapper")

_ECONOMY_NAMES_CACHE: dict[str, str] | None = None


def _get_economy_names() -> dict[str, str]:
    """Build ISO→UN name map by scanning economies/*.yaml at first call."""
    global _ECONOMY_NAMES_CACHE
    if _ECONOMY_NAMES_CACHE is not None:
        return _ECONOMY_NAMES_CACHE
    from pathlib import Path
    import yaml as _yaml
    economies_dir = Path(__file__).parent.parent.parent / "economies"
    names: dict[str, str] = {}
    for yaml_path in economies_dir.glob("*.yaml"):
        if yaml_path.stem.lower() == "readme":
            continue
        try:
            raw = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            iso = str(raw.get("iso_code", "")).upper()
            un_name = str(raw.get("un_name", ""))
            if iso and un_name:
                names[iso] = un_name
        except Exception:
            pass
    _ECONOMY_NAMES_CACHE = names
    return names


def extract_provisions(
    rag_results,
    doc,
    known_provisions: "set[str] | None" = None,
    known_sections: "dict[str, set[str]] | None" = None,
    portal_type: str = "primary",
) -> tuple[list[ExtractionResult], LLMCostEntry]:
    """
    Main entry point called by the pipeline orchestrator.

    Args:
        rag_results: list of objects with .indicator_id and .top_chunks
                     OR dict[str, list[RetrievedChunk]] from retrieve_batch
        doc: FetchedDocument or TranslatedDocument
        known_provisions: anchor-level URL set from SeedData.known_provisions

    Returns:
        (list[ExtractionResult], LLMCostEntry)
    """
    taxonomy = load_taxonomy_dict()
    all_results: list[ExtractionResult] = []
    cost_entry = LLMCostEntry()
    doc_metadata = None
    known_provisions = known_provisions or set()
    known_sections = known_sections or {}

    # Support both list-of-RAGResult and dict from retrieve_batch
    if isinstance(rag_results, dict):
        items = [_DictRAGResult(iid, chunks) for iid, chunks in rag_results.items()]
    else:
        items = rag_results

    total_indicators = len(items)
    for indicator_number, rag_result in enumerate(items, 1):
        indicator_id = rag_result.indicator_id
        top_chunks = rag_result.top_chunks
        substep(f"LLM call {indicator_id} ({indicator_number}/{total_indicators})")

        if not top_chunks:
            logger.warning({
                "event": "skipping_indicator_no_chunks",
                "indicator_id": indicator_id,
                "source_url": getattr(doc, "source_url", ""),
            })
            continue

        if indicator_id not in taxonomy:
            logger.warning({"event": "unknown_indicator", "indicator_id": indicator_id})
            continue

        if doc_metadata is None:
            doc_metadata = _build_doc_metadata(doc)
            doc_metadata["portal_type"] = portal_type

        chunks_for_prompt = trim_chunks_to_budget(top_chunks, SYSTEM_PROMPT)

        user_prompt = build_user_prompt(
            indicator_id=indicator_id,
            taxonomy=taxonomy,
            top_chunks=chunks_for_prompt,
            act_title=getattr(doc, "act_title", ""),
            economy=getattr(doc, "economy", ""),
            source_url=getattr(doc, "source_url", ""),
        )

        t0 = time.time()
        try:
            response = call_llm_with_cascade(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                max_tokens=1000,
                temperature=0.0,
            )
        except AllProvidersExhaustedError as e:
            logger.error({
                "event": "all_providers_exhausted",
                "indicator_id": indicator_id,
                "source_url": getattr(doc, "source_url", ""),
                "error": str(e),
            })
            continue

        elapsed = (time.time() - t0) * 1000
        cost_entry.add_call(response, indicator_id, elapsed)

        provisions = parse_llm_response(
            response, indicator_id, top_chunks, doc_metadata,
            known_provisions=known_provisions,
            known_sections=known_sections,
        )
        provisions = expand_non_consecutive(provisions)
        all_results.extend(provisions)

        logger.info({
            "event": "indicator_extracted",
            "indicator_id": indicator_id,
            "provisions_found": len(provisions),
            "provider": response.provider,
            "model": response.model,
            "cost_usd": response.cost_usd,
            "latency_ms": round(elapsed, 1),
        })

    all_results = _deduplicate(all_results)

    logger.info({
        "event": "extraction_completed",
        "source_url": getattr(doc, "source_url", ""),
        "total_provisions": len(all_results),
        "total_cost_usd": round(cost_entry.total_cost_usd, 6),
        "model": get_active_model_version(),
    })

    return all_results, cost_entry


def _build_doc_metadata(doc) -> dict:
    economy_iso = getattr(doc, "economy", "")
    economy_name = _official_un_name(economy_iso)
    # doc_type: FetchedDocument has it directly; TranslatedDocument wraps it in .fetched
    fetched = getattr(doc, "fetched", doc)
    doc_type = getattr(fetched, "doc_type", None)

    return {
        "economy": economy_name,
        "law_name": getattr(doc, "act_title", ""),
        "law_number_ref": getattr(doc, "law_number_ref", None),
        "last_amended": getattr(doc, "last_amended_year", None),
        "source_url": getattr(doc, "source_url", ""),
        "discovery_tag": getattr(doc, "discovery_tag", "KNOWN"),
        "verbatim_original": getattr(doc, "verbatim_original", None),
        "translation_provider": getattr(doc, "translation_provider", "none"),
        "doc_type": doc_type,
    }


def _official_un_name(iso_code: str) -> str:
    name = _get_economy_names().get(iso_code.upper() if iso_code else "")
    if name is None:
        raise ConfigError(
            f"Unknown economy ISO code '{iso_code}'. "
            "Add a YAML file under economies/ with iso_code and un_name fields. "
            "See economies/README.md for the format."
        )
    return name


def _deduplicate(results: list[ExtractionResult]) -> list[ExtractionResult]:
    """
    Removes duplicates by (indicator_id, article, snippet[:80]).
    On collision, keeps higher confidence.
    """
    seen: dict[tuple, ExtractionResult] = {}
    for result in results:
        key = (
            result.indicator_id,
            result.article.lower().strip(),
            result.verbatim_snippet[:80].lower().strip(),
        )
        if key not in seen:
            seen[key] = result
        else:
            existing = seen[key]
            if (result.confidence or 0.0) > (existing.confidence or 0.0):
                seen[key] = result

    deduped = list(seen.values())
    removed = len(results) - len(deduped)
    if removed > 0:
        logger.info({"event": "deduplication_removed", "count": removed})
    return deduped


def check_quality_gate(
    economy: str,
    pillar: int,
    results: list[ExtractionResult],
    min_confidence: float = 0.80,
) -> None:
    """
    Generic quality guardrail for CI/build validation.

    A run passes when it produces at least one provision for the requested
    pillar at or above ``min_confidence``. Production may disable this check or
    run it in warning-only mode; the caller controls that policy.
    """
    pillar_results = [
        r for r in results
        if r.indicator_id.startswith(f"P{pillar}-")
        and r.confidence is not None
        and r.confidence >= min_confidence
    ]

    if not pillar_results:
        logger.error({
            "event": "quality_gate_failed",
            "economy": economy,
            "pillar": pillar,
            "min_confidence": min_confidence,
            "qualifying_results": 0,
        })
        raise QualityGateError(
            f"Quality gate failed for {economy} P{pillar}: no provision extracted "
            f"with confidence >= {min_confidence:.2f}. Verify discovery, text "
            "extraction, retrieval, and LLM mapping."
        )

    logger.info({
        "event": "quality_gate_passed",
        "economy": economy,
        "pillar": pillar,
        "qualifying_results": len(pillar_results),
        "highest_confidence": max(r.confidence for r in pillar_results if r.confidence),
    })


def check_pdpa_gate(economy: str, results: list[ExtractionResult]) -> None:
    """Deprecated wrapper retained for existing Phase 1 regression tests."""
    if economy != "SG":
        return
    try:
        check_quality_gate(economy, 7, results)
    except QualityGateError as exc:
        raise PDPAGateError(str(exc)) from exc


class _DictRAGResult:
    """Adapter: wraps dict entry from retrieve_batch into an object with .indicator_id/.top_chunks."""
    def __init__(self, indicator_id: str, top_chunks: list):
        self.indicator_id = indicator_id
        self.top_chunks = top_chunks
