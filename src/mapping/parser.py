"""
LLM response parser + verbatim assertion + two-row handler. [Z2-4 ST4]

parse_llm_response   — parses JSON, runs verbatim assertion, returns ExtractionResult list.
expand_non_consecutive — splits non-adjacent provisions into two rows.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Optional

from src.mapping.exceptions import ParseError
from src.mapping.models import ExtractionResult, LLMResponse
from src.retrieval.models import RetrievedChunk

logger = logging.getLogger("mapping.parser")


def parse_llm_response(
    response: LLMResponse,
    indicator_id: str,
    top_chunks: list[RetrievedChunk],
    doc_metadata: dict,
) -> list[ExtractionResult]:
    """
    Parses LLM JSON response → list[ExtractionResult].
    Returns empty list if found=false or provisions=[].
    """
    raw_text = response.text.strip()

    try:
        parsed = _extract_json(raw_text)
    except ParseError as e:
        logger.warning({
            "event": "parse_error",
            "indicator_id": indicator_id,
            "error": str(e),
        })
        return []

    if not parsed.get("found", False) or not parsed.get("provisions"):
        logger.info({
            "event": "no_provision_found",
            "indicator_id": indicator_id,
            "economy": doc_metadata.get("economy", ""),
        })
        return []

    results = []
    for prov in parsed["provisions"]:
        result = _build_extraction_result(prov, indicator_id, top_chunks, doc_metadata, response)
        if result is not None:
            results.append(result)

    # Deduplicate within this response
    results = _dedup_within_response(results)
    return results


def _extract_json(raw_text: str) -> dict:
    """Handles plain JSON, markdown fences, and noisy prose wrapping."""
    try:
        return json.loads(raw_text)
    except json.JSONDecodeError:
        pass

    cleaned = re.sub(r"```(?:json)?", "", raw_text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise ParseError(f"Cannot parse LLM JSON response: {raw_text[:200]}")


def _assert_verbatim_in_context(
    snippet: str,
    top_chunks: list[RetrievedChunk],
) -> tuple[bool, Optional[str]]:
    """
    Verifies verbatim_snippet appears in at least one source chunk.
    Returns (True, None) on pass, (False, reason) on fail.
    Failed assertion flags for review but does NOT discard the row.
    """
    def normalise(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())

    norm_snippet = normalise(snippet)

    for rc in top_chunks:
        norm_chunk = normalise(rc.chunk.text)
        if norm_snippet in norm_chunk:
            return True, None
        if len(norm_snippet) > 100 and norm_snippet[:80] in norm_chunk:
            return True, None

    return False, f"verbatim_snippet not found in any of {len(top_chunks)} source chunks"


def _build_extraction_result(
    prov: dict,
    indicator_id: str,
    top_chunks: list[RetrievedChunk],
    doc_metadata: dict,
    response: LLMResponse,
) -> Optional[ExtractionResult]:
    snippet = prov.get("verbatim_snippet", "").strip()
    article = prov.get("article", "").strip()
    rationale = prov.get("mapping_rationale", "").strip()
    confidence = prov.get("confidence")
    location_ref = prov.get("location_reference", "")
    non_consecutive = prov.get("non_consecutive", False)

    if not snippet and not article:
        logger.warning({"event": "empty_provision_discarded", "indicator_id": indicator_id})
        return None

    if len(rationale) > 300:
        rationale = rationale[:297] + "..."
        logger.warning({"event": "rationale_truncated", "indicator_id": indicator_id})

    assertion_ok, assertion_reason = _assert_verbatim_in_context(snippet, top_chunks)

    flag_for_review = False
    flag_reasons = []
    if not assertion_ok:
        flag_for_review = True
        flag_reasons.append(f"verbatim_assertion_failed: {assertion_reason}")
        logger.warning({
            "event": "verbatim_assertion_failed",
            "indicator_id": indicator_id,
            "reason": assertion_reason,
        })
    if confidence is not None and confidence < 0.80:
        flag_for_review = True
        flag_reasons.append(f"low_confidence: {confidence}")

    notes_parts = []
    if flag_for_review:
        notes_parts.append("Recommend human review — " + "; ".join(flag_reasons))
    if doc_metadata.get("verbatim_original"):
        notes_parts.append("Translation source: DeepL/Google Translate")

    source_chunk = top_chunks[0] if top_chunks else None
    # context_window is a single string — use as before context
    context_window = source_chunk.context_window if source_chunk else ""

    # Build location_reference string from chunk if not provided by LLM
    if not location_ref and source_chunk:
        loc = source_chunk.chunk.location_reference
        parts = []
        if loc.page is not None:
            parts.append(f"Page {loc.page + 1}")
        if loc.article_number:
            parts.append(f"Art. {loc.article_number}")
        location_ref = " | ".join(parts) if parts else None

    result = ExtractionResult(
        economy=doc_metadata["economy"],
        law_name=doc_metadata["law_name"],
        law_number_ref=doc_metadata.get("law_number_ref"),
        last_amended=doc_metadata.get("last_amended"),
        indicator_id=indicator_id,
        article=article,
        discovery_tag=doc_metadata["discovery_tag"],
        location_reference=location_ref or None,
        verbatim_snippet=snippet,
        mapping_rationale=rationale or None,
        source_url=doc_metadata["source_url"],
        confidence=float(confidence) if confidence is not None else None,
        notes="; ".join(notes_parts) if notes_parts else None,
        provider_used=response.provider,
        model_used=response.model,
        source_chunk_id=source_chunk.chunk.chunk_id if source_chunk else "unknown",
        raw_context_before=context_window,
        raw_context_after="",
        verbatim_original=doc_metadata.get("verbatim_original"),
        flag_for_review=flag_for_review,
        flag_reason="; ".join(flag_reasons) if flag_reasons else None,
        non_consecutive=non_consecutive,
    )

    result.validate()
    return result


def expand_non_consecutive(results: list[ExtractionResult]) -> list[ExtractionResult]:
    """
    Splits provisions spanning non-consecutive sections into two rows.
    Article field must contain 'and' separator (e.g. "Section 26 and Section 31").
    """
    expanded = []
    for r in results:
        if r.non_consecutive and " and " in r.article:
            parts = [p.strip() for p in re.split(r"\s+and\s+|;\s*", r.article, maxsplit=1)]
            if len(parts) == 2:
                snippet_parts = r.verbatim_snippet.split(". ", maxsplit=1)
                for i, art in enumerate(parts):
                    row = copy.deepcopy(r)
                    row.article = art
                    row.verbatim_snippet = snippet_parts[i] if i < len(snippet_parts) else r.verbatim_snippet
                    row.non_consecutive = False
                    row.notes = (row.notes or "") + f" [Split from non-consecutive provision: {r.article}]"
                    expanded.append(row)
                continue
        expanded.append(r)
    return expanded


def _dedup_within_response(results: list[ExtractionResult]) -> list[ExtractionResult]:
    """Remove duplicates by (article, snippet[:50]) key within a single LLM response."""
    seen: dict[tuple, ExtractionResult] = {}
    for r in results:
        key = (r.article.lower().strip(), r.verbatim_snippet[:50].lower().strip())
        if key not in seen:
            seen[key] = r
        elif (r.confidence or 0.0) > (seen[key].confidence or 0.0):
            seen[key] = r
    return list(seen.values())
