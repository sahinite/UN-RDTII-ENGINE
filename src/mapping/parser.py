"""
LLM response parser + verbatim assertion + two-row handler. [Z2-4 ST4]

parse_llm_response   — parses JSON, runs verbatim assertion, returns ExtractionResult list.
expand_non_consecutive — splits non-adjacent provisions into two rows.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import unicodedata
from typing import Optional

from src.mapping.exceptions import ParseError
from src.mapping.models import ExtractionResult, LLMResponse
from src.mapping.provision_tag import (
    infer_article_anchor,
    infer_section_token,
    resolve_provision_tag,
)
from src.retrieval.models import RetrievedChunk

logger = logging.getLogger("mapping.parser")

_CROSS_REF_PATTERNS = re.compile(
    r"\b(see also|pursuant to|as defined in|referred to in section|under [A-Z][a-z]+ Act)\b",
    re.IGNORECASE,
)
_DELEGATED_LEG_KEYWORDS = re.compile(
    r"\b(Regulations|Order|Rules|Subsidiary Legislation|Direction)\b"
)


def _norm_text(text: str) -> str:
    """NFKC + punctuation/space normalisation shared by verbatim matching."""
    t = unicodedata.normalize("NFKC", text)
    for a, b in (
        ("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'),
        ("–", "-"), ("—", "-"), ("…", "..."),
        (" ", " "), ("­", ""),
    ):
        t = t.replace(a, b)
    return t.lower()


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", _norm_text(text).strip())


def _strip_ws(text: str) -> str:
    return re.sub(r"\s+", "", _norm_text(text))


def _snippet_in_text(snippet: str, text: str) -> bool:
    """True if snippet appears in text under whitespace/punctuation tolerance."""
    snip_c, hay_c = _collapse_ws(snippet), _collapse_ws(text)
    if snip_c and snip_c in hay_c:
        return True
    snip_s, hay_s = _strip_ws(snippet), _strip_ws(text)
    if snip_s and snip_s in hay_s:
        return True
    if len(snip_s) > 80 and snip_s[:80] in hay_s:
        return True
    return False


def _find_matching_chunk(
    snippet: str,
    top_chunks: list[RetrievedChunk],
) -> Optional[RetrievedChunk]:
    """
    Return the chunk whose text actually contains the verbatim snippet, so the
    citation (page/article) is anchored to where the text really lives — not to
    top_chunks[0], which varies per (indicator × doc) call and produced the
    "same provision, different page" mismatch. Falls back to None if no single
    chunk contains it (snippet may straddle a chunk boundary).
    """
    if not snippet:
        return None
    for rc in top_chunks:
        if _snippet_in_text(snippet, rc.chunk.text):
            return rc
    return None


def parse_llm_response(
    response: LLMResponse,
    indicator_id: str,
    top_chunks: list[RetrievedChunk],
    doc_metadata: dict,
    known_provisions: "set[str] | None" = None,
    known_sections: "dict[str, set[str]] | None" = None,
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
    kp = known_provisions or set()
    ks = known_sections or {}
    for prov in parsed["provisions"]:
        result = _build_extraction_result(prov, indicator_id, top_chunks, doc_metadata, response, kp, ks)
        if result is not None:
            results.append(result)

    # Deduplicate within this response
    results = _dedup_within_response(results)
    return results


# Reasoning models (deepseek-r1, qwen3, …) prepend chain-of-thought wrapped in
# <think>…</think> before the JSON answer. Braces inside that reasoning corrupt the
# greedy {…} match below, so strip it first. Provider-agnostic — any model in the
# cascade that "thinks out loud" is handled here, not per-provider.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_reasoning(text: str) -> str:
    """Remove <think>…</think> reasoning; the answer follows the final close tag."""
    lower = text.lower()
    if "</think>" in lower:
        text = text[lower.rfind("</think>") + len("</think>"):]
    return _THINK_BLOCK_RE.sub("", text).strip()


def _extract_json(raw_text: str) -> dict:
    """Handles plain JSON, markdown fences, reasoning blocks, and noisy prose."""
    raw_text = _strip_reasoning(raw_text)
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
    Verifies verbatim_snippet appears in the retrieved source chunks.
    Returns (True, None) on pass, (False, reason) on fail.
    Failed assertion discards the row unless ALLOW_UNVERIFIED_SNIPPETS=true.

    Matching is tolerant of two extraction artefacts that do NOT mean the LLM
    invented text:
      - pdfplumber sometimes drops the spaces between words ("tooverseeand…"),
        so we also compare with ALL whitespace removed.
      - Unicode punctuation variants (curly quotes, en/em dashes, ligatures) are
        normalised. We also match across the *joined* chunks so a snippet that
        straddles a chunk boundary still verifies.
    """
    joined = " ".join(rc.chunk.text for rc in top_chunks)
    snip_collapse, hay_collapse = _collapse_ws(snippet), _collapse_ws(joined)
    snip_strip, hay_strip = _strip_ws(snippet), _strip_ws(joined)

    if snip_collapse and snip_collapse in hay_collapse:
        return True, None
    # Whitespace-insensitive — handles pdfplumber dropping inter-word spaces.
    if snip_strip and snip_strip in hay_strip:
        return True, None
    # Long-snippet prefix fallback (snippet may run slightly past the chunk window).
    if len(snip_strip) > 80 and snip_strip[:80] in hay_strip:
        return True, None

    return False, f"verbatim_snippet not found in any of {len(top_chunks)} source chunks"


def _build_extraction_result(
    prov: dict,
    indicator_id: str,
    top_chunks: list[RetrievedChunk],
    doc_metadata: dict,
    response: LLMResponse,
    known_provisions: "set[str]" = frozenset(),
    known_sections: "dict[str, set[str]] | None" = None,
) -> Optional[ExtractionResult]:
    snippet = prov.get("verbatim_snippet", "").strip()
    article = prov.get("article", "").strip()
    rationale = prov.get("mapping_rationale", "").strip()
    confidence = prov.get("confidence")
    non_consecutive = prov.get("non_consecutive", False)

    if not snippet and not article:
        logger.warning({"event": "empty_provision_discarded", "indicator_id": indicator_id})
        return None

    if len(rationale) > 300:
        rationale = rationale[:297] + "..."
        logger.warning({"event": "rationale_truncated", "indicator_id": indicator_id})

    # Decision 8: verbatim assertion — hard discard unless ALLOW_UNVERIFIED_SNIPPETS=true
    assertion_ok, assertion_reason = _assert_verbatim_in_context(snippet, top_chunks)
    allow_unverified = os.environ.get("ALLOW_UNVERIFIED_SNIPPETS", "").lower() == "true"

    if not assertion_ok and not allow_unverified:
        logger.warning({
            "event": "verbatim_assertion_failed_discarded",
            "indicator_id": indicator_id,
            "reason": assertion_reason,
        })
        return None

    flag_for_review = False
    flag_reasons = []

    if not assertion_ok and allow_unverified:
        flag_for_review = True
        flag_reasons.append(f"verbatim_assertion_failed: {assertion_reason}")
        logger.warning({
            "event": "verbatim_assertion_failed_flagged",
            "indicator_id": indicator_id,
            "reason": assertion_reason,
        })

    if confidence is not None and confidence < 0.80:
        flag_for_review = True
        flag_reasons.append(f"low_confidence: {confidence}")

    # Decision 6: law name abbreviation check
    law_name = doc_metadata.get("law_name", "")
    if re.match(r'^[A-Z]{2,8}$', law_name) or (law_name and len(law_name) < 20):
        flag_for_review = True
        flag_reasons.append("law_name_possibly_abbreviated")

    # Decision 7: article sub-paragraph check
    if article and re.match(r'^(Art\.|s\.|Section|Reg\.)\s*\d+$', article):
        flag_for_review = True
        flag_reasons.append("article_missing_paragraph")

    # Non-primary source check: a provision extracted from a secondary portal
    # (regulator guidance / advisory / summary page) is not the binding statute.
    # Such pages often paraphrase the law and number obligations as a plain list
    # ("8. Transfer Limitation Obligation"), which the LLM cites as a spurious
    # section — so flag for verification against the primary legislation. Driven
    # by the portal's declared YAML `type`, so it holds for every economy.
    if doc_metadata.get("portal_type") == "secondary":
        flag_for_review = True
        flag_reasons.append("non_primary_source — verify against primary legislation")

    # Decision 2/3: provision-level discovery tag
    anchor = infer_article_anchor(article) if article else None
    tag, tag_unresolvable = resolve_provision_tag(
        source_url=doc_metadata.get("source_url", ""),
        article_anchor=anchor,
        doc_discovery_tag=doc_metadata.get("discovery_tag", "KNOWN"),
        known_provisions=known_provisions,
        law_name=law_name,
        article=article,
        known_sections=known_sections or {},
    )
    if tag_unresolvable:
        flag_for_review = True
        flag_reasons.append("discovery_tag_unresolvable")

    notes_parts = []
    if flag_for_review:
        notes_parts.append("Recommend human review — " + "; ".join(flag_reasons))
    # Only note a translation source when the doc was actually translated —
    # verbatim_original is always set (ADR-017), so it is NOT a translation signal.
    if doc_metadata.get("translation_provider") not in (None, "", "none", "failed"):
        notes_parts.append("Translation source: DeepL/Google Translate")

    # Decision 12: cross-reference and delegated legislation detection
    if snippet and _CROSS_REF_PATTERNS.search(snippet):
        notes_parts.append("Cross-reference detected — provision may depend on another instrument")
    if law_name and _DELEGATED_LEG_KEYWORDS.search(law_name):
        notes_parts.append("Delegated legislation — verify enabling act")

    # Decision 9: location_reference is derived deterministically from trusted
    # provenance — never from an LLM-emitted value. The LLM cannot know page
    # numbers or anchor URLs (they are not in the prompt), so any it invents are
    # fabricated (e.g. the literal "https://url#anchor" placeholder). We build the
    # citation from (a) the LLM's `article` field — the authoritative section it
    # read from the chunk header — and (b) the page of the chunk that actually
    # contains the verbatim snippet. Anchoring to the snippet-bearing chunk (not
    # top_chunks[0], which varies per indicator call) is what keeps the SAME
    # provision's citation identical across indicators.
    matched_chunk = _find_matching_chunk(snippet, top_chunks) or (
        top_chunks[0] if top_chunks else None
    )
    context_window = matched_chunk.context_window if matched_chunk else ""

    loc_parts = []
    section_token = infer_section_token(article) if article else None
    if section_token:
        loc_parts.append(f"Art. {section_token.upper()}")
    if matched_chunk is not None:
        page = matched_chunk.chunk.location_reference.page
        if page is not None:
            loc_parts.append(f"Page {page + 1}")
    final_location_ref = " | ".join(loc_parts) if loc_parts else None

    result = ExtractionResult(
        economy=doc_metadata["economy"],
        law_name=law_name,
        law_number_ref=doc_metadata.get("law_number_ref"),
        last_amended=doc_metadata.get("last_amended"),
        indicator_id=indicator_id,
        article=article,
        discovery_tag=tag,
        doc_discovery_tag=doc_metadata.get("discovery_tag", "KNOWN"),
        location_reference=final_location_ref,
        verbatim_snippet=snippet,
        mapping_rationale=rationale or None,
        source_url=doc_metadata.get("source_url", ""),
        confidence=float(confidence) if confidence is not None else None,
        notes="; ".join(notes_parts) if notes_parts else None,
        provider_used=response.provider,
        model_used=response.model,
        source_chunk_id=matched_chunk.chunk.chunk_id if matched_chunk else "unknown",
        raw_context_before=context_window,
        raw_context_after="",
        verbatim_original=doc_metadata.get("verbatim_original"),
        doc_type=doc_metadata.get("doc_type"),
        flag_for_review=flag_for_review,
        flag_reason="; ".join(flag_reasons) if flag_reasons else None,
        non_consecutive=non_consecutive,
        source_rerank_score=getattr(matched_chunk, "rerank_score", None) if matched_chunk else None,
        source_retrieval_method=getattr(matched_chunk, "retrieval_method", None) if matched_chunk else None,
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
                # Carry over the parent's page token so each split row keeps its
                # provenance, but re-cite the article for the row's own section.
                page_tok = next(
                    (p.strip() for p in (r.location_reference or "").split("|")
                     if p.strip().lower().startswith("page ")),
                    None,
                )
                for i, art in enumerate(parts):
                    row = copy.deepcopy(r)
                    row.article = art
                    row.verbatim_snippet = snippet_parts[i] if i < len(snippet_parts) else r.verbatim_snippet
                    row.non_consecutive = False
                    art_tok = infer_section_token(art)
                    loc_bits = ([f"Art. {art_tok.upper()}"] if art_tok else []) + (
                        [page_tok] if page_tok else []
                    )
                    row.location_reference = " | ".join(loc_bits) or None
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
