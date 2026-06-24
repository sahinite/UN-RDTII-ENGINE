"""
Mapper orchestrator — ties LLM cascade + prompt builder + parser together. [Z2-4 ST5]

Public API:
  extract_provisions(rag_results, doc) -> (list[ExtractionResult], LLMCostEntry)
  check_pdpa_gate(economy, results)
"""

from __future__ import annotations

import logging
import time
from typing import Union

from src.cli.progress import substep
from src.mapping.exceptions import AllProvidersExhaustedError, ConfigError, PDPAGateError
from src.mapping.llm_client import call_llm_with_cascade, get_active_model_version
from src.mapping.models import ExtractionResult, LLMCostEntry
from src.mapping.parser import expand_non_consecutive, parse_llm_response
from src.mapping.prompts import SYSTEM_PROMPT, build_user_prompt, load_taxonomy_dict, trim_chunks_to_budget
from src.retrieval.models import RetrievedChunk

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

    # Support both list-of-RAGResult and dict from retrieve_batch
    if isinstance(rag_results, dict):
        items = [_DictRAGResult(iid, chunks) for iid, chunks in rag_results.items()]
    else:
        items = rag_results

    total_items = len(items)
    for idx, rag_result in enumerate(items, 1):
        indicator_id = rag_result.indicator_id
        top_chunks = rag_result.top_chunks
        substep(f"LLM call {indicator_id} ({idx}/{total_items})")

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

        doc_metadata = _build_doc_metadata(doc)
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
            known_provisions=known_provisions or set(),
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
    for r in results:
        key = (
            r.indicator_id,
            r.article.lower().strip(),
            r.verbatim_snippet[:80].lower().strip(),
        )
        if key not in seen:
            seen[key] = r
        else:
            existing = seen[key]
            if (r.confidence or 0.0) > (existing.confidence or 0.0):
                seen[key] = r

    deduped = list(seen.values())
    removed = len(results) - len(deduped)
    if removed > 0:
        logger.info({"event": "deduplication_removed", "count": removed})
    return deduped


def check_pdpa_gate(economy: str, results: list[ExtractionResult]) -> None:
    """
    Quality guardrail: verifies at least one P7 provision with confidence >= 0.80
    was extracted from Singapore PDPA before expanding to other economies.
    Raises PDPAGateError if gate fails for Singapore.
    """
    if economy != "SG":
        return

    p7_results = [
        r for r in results
        if r.indicator_id.startswith("P7") and (r.confidence or 0.0) >= 0.80
    ]

    if not p7_results:
        logger.error({
            "event": "pdpa_gate_failed",
            "economy": economy,
            "p7_results_count": 0,
        })
        raise PDPAGateError(
            "Singapore PDPA-first gate: no P7 provision extracted with confidence >= 0.80. "
            "Verify PDPA text extraction before proceeding to other economies."
        )

    logger.info({
        "event": "pdpa_gate_passed",
        "p7_provisions_found": len(p7_results),
        "highest_confidence": max(r.confidence for r in p7_results if r.confidence),
    })


class _DictRAGResult:
    """Adapter: wraps dict entry from retrieve_batch into an object with .indicator_id/.top_chunks."""
    def __init__(self, indicator_id: str, top_chunks: list):
        self.indicator_id = indicator_id
        self.top_chunks = top_chunks
