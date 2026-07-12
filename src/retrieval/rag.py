"""
RAG pipeline orchestrator: chunk → embed → BM25 + dense hybrid retrieval →
Reciprocal Rank Fusion → cross-encoder rerank → top-5 chunks. [Z2-3 ST6]

Public API
----------
retrieve(indicator_id, doc) -> list[RetrievedChunk]
    Runs the full pipeline for one indicator against one document.
    Each RetrievedChunk carries a location_reference so citations are verifiable.

retrieve_batch(indicator_ids, doc) -> dict[str, list[RetrievedChunk]]
    Convenience wrapper: one chunking/embedding pass, one BM25 build,
    then retrieval for each indicator.  Amortises the expensive I/O.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Union

from src.cli.progress import substep
from src.retrieval.bm25_index import build_bm25
from src.retrieval.chunker import chunk_document
from src.retrieval.config import (
    BM25_TOP_K,
    DENSE_TOP_K,
    FUSION_TOP_K,
    RERANK_TOP_N,
    get_indicator,
)
from src.crawler.seed_loader import match_known_act, normalise_title
from src.mapping.provision_tag import infer_section_token
from src.retrieval.embedder import build_index
from src.retrieval.fusion import rrf_fusion
from src.retrieval.models import Chunk, RetrievedChunk
from src.retrieval.reranker import rerank

if TYPE_CHECKING:
    from src.fetcher.models import FetchedDocument, TranslatedDocument

logger = logging.getLogger("retrieval.rag")


def retrieve(
    indicator_id: str,
    doc: Union["FetchedDocument", "TranslatedDocument"],
    top_n: int = RERANK_TOP_N,
) -> list[RetrievedChunk]:
    """
    Full RAG pipeline for a single indicator.

    Steps:
      1. chunk_document  → list[Chunk]
      2. build_index     → EmbeddingIndex (FAISS)
      3. build_bm25      → BM25Index
      4. dense_search    → top-DENSE_TOP_K
      5. bm25 search     → top-BM25_TOP_K
      6. rrf_fusion      → top-FUSION_TOP_K
      7. rerank          → top-top_n RetrievedChunk

    Returns an empty list if the document has no usable text.
    """
    t0 = time.perf_counter()
    indicator = get_indicator(indicator_id)

    # ── 1. Chunk ──────────────────────────────────────────────────────────────
    chunks: list[Chunk] = chunk_document(doc)
    if not chunks:
        logger.warning({
            "event": "rag_empty_chunks",
            "indicator_id": indicator_id,
            "url": getattr(getattr(doc, "fetched", doc), "source_url", ""),
        })
        return []

    logger.info({
        "event": "rag_chunks_ready",
        "indicator_id": indicator_id,
        "chunks": len(chunks),
    })

    # ── 2 & 3. Build indexes (parallel in future; sequential for now) ─────────
    emb_index = build_index(chunks)
    bm25_idx = build_bm25(chunks)

    # ── 4 & 5. Retrieve ───────────────────────────────────────────────────────
    query = indicator.legal_question + " " + " ".join(indicator.probe_keywords[:3])
    dense_results = emb_index.dense_search(query, top_k=DENSE_TOP_K)
    bm25_results = bm25_idx.search(query, indicator, top_k=BM25_TOP_K)

    # ── 6. Fuse ───────────────────────────────────────────────────────────────
    fused = rrf_fusion(bm25_results, dense_results, top_k=FUSION_TOP_K)

    # ── 7. Rerank ─────────────────────────────────────────────────────────────
    retrieved = rerank(query, fused, chunks, top_n=top_n)

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info({
        "event": "rag_pipeline_complete",
        "indicator_id": indicator_id,
        "chunks_input": len(chunks),
        "fused_candidates": len(fused),
        "top_n_returned": len(retrieved),
        "elapsed_ms": round(elapsed_ms, 1),
        "url": getattr(getattr(doc, "fetched", doc), "source_url", ""),
    })

    return retrieved


def retrieve_batch(
    indicator_ids: list[str],
    doc: Union["FetchedDocument", "TranslatedDocument"],
    top_n: int = RERANK_TOP_N,
    known_sections_by_indicator: "dict[str, dict[str, set[str]]] | None" = None,
) -> dict[str, list[RetrievedChunk]]:
    """
    Amortised batch retrieval: chunk and build indexes once, then query per indicator.
    Returns {indicator_id: [RetrievedChunk, ...]} for each requested indicator.

    When ``known_sections_by_indicator`` is supplied, seed-guided retrieval ensures
    the specific section Round 1 filed under an indicator for THIS act is present in
    that indicator's chunks — countering indicator drift where BM25/rerank surfaces a
    different section of the same act (e.g. Employment s.103 access instead of the
    Round 1 s.95 retention provision for I3).
    """
    chunks: list[Chunk] = chunk_document(doc)
    if not chunks:
        return {iid: [] for iid in indicator_ids}

    ksbi = known_sections_by_indicator or {}
    # Only resolve the act identity when there is a seed map to match against.
    raw_act_title = str(getattr(doc, "act_title", "") or "") if ksbi else ""
    act_norm = normalise_title(raw_act_title) if raw_act_title else ""

    substep(f"Building FAISS index — {len(chunks)} chunks")
    emb_index = build_index(chunks)
    substep(f"Building BM25 index — {len(chunks)} chunks")
    bm25_idx = build_bm25(chunks)

    results: dict[str, list[RetrievedChunk]] = {}
    total_iids = len(indicator_ids)
    for i, iid in enumerate(indicator_ids, 1):
        substep(f"RAG retrieve {iid} ({i}/{total_iids})")
        try:
            indicator = get_indicator(iid)
        except KeyError:
            logger.warning({"event": "unknown_indicator_id_skipped", "indicator_id": iid})
            results[iid] = []
            continue
        query = indicator.legal_question + " " + " ".join(indicator.probe_keywords[:3])

        dense_results = emb_index.dense_search(query, top_k=DENSE_TOP_K)
        bm25_results = bm25_idx.search(query, indicator, top_k=BM25_TOP_K)
        fused = rrf_fusion(bm25_results, dense_results, top_k=FUSION_TOP_K)
        retrieved = rerank(query, fused, chunks, top_n=top_n)

        # Fuzzy act-identity match (acronym / punctuation / dropped-word tolerant)
        # so a cover-page title differing in form from Round 1 still guides retrieval.
        per_ind = ksbi.get(iid, {})
        matched_act = match_known_act(raw_act_title, per_ind.keys()) if (per_ind and raw_act_title) else None
        want = per_ind.get(matched_act, set()) if matched_act else set()
        if want:
            retrieved, injected = _inject_seed_sections(retrieved, chunks, want)
            if injected:
                logger.info({
                    "event": "seed_guided_injection",
                    "indicator_id": iid,
                    "act": act_norm,
                    "sections": sorted(want),
                    "injected": injected,
                })

        results[iid] = retrieved

        logger.info({
            "event": "batch_indicator_done",
            "indicator_id": iid,
            "returned": len(retrieved),
        })

    return results


_NEXT_HEADING = re.compile(r"\n\s*\d+[A-Z]?\.\s")


def _find_section_chunk(all_chunks: list[Chunk], token: str) -> "Chunk | None":
    """Locate the operative provision for a section token. Two ways, because the
    chunker often leaves `article_number` empty on large acts (e.g. Employment
    Act s.95 and PDPA s.25 live in chunks labelled ''):
      1. exact `article_number` match (clean, when the chunker labelled it);
      2. the `N.` heading at a line start in the chunk TEXT, scored by how much
         body follows before the next section heading. The operative provision
         ("25. An organisation must cease to retain…") has a long body; a
         Contents/TOC listing ("25. Retention…\n26. …") has only a short title,
         so the longest-body match wins and pure-TOC chunks (body < 80) are
         rejected. Handles both subsectioned (95.—(1)) and single-sentence (25.)
         provisions without matching the TOC.
    """
    for c in all_chunks:
        if infer_section_token(c.location_reference.article_number) == token:
            return c

    # Heading at a line start; allow whatever follows the dot — subsectioned
    # "95.—(1)" (em-dash) and single-sentence "25. An organisation…" alike.
    head = re.compile(rf"(?:^|\n)\s*{re.escape(token)}\.", re.IGNORECASE)
    best: "Chunk | None" = None
    best_body = -1
    for c in all_chunks:
        m = head.search(c.text)
        if not m:
            continue
        rest = c.text[m.end():]
        nh = _NEXT_HEADING.search(rest)
        body_len = nh.start() if nh else len(rest)
        if body_len > best_body:
            best_body, best = body_len, c
    return best if best_body >= 80 else None


def _inject_seed_sections(
    retrieved: list[RetrievedChunk],
    all_chunks: list[Chunk],
    want_sections: set[str],
) -> tuple[list[RetrievedChunk], int]:
    """GENTLE seed guidance: add a Round 1 known section ONLY when it is entirely
    absent from retrieval, prepended so it reaches the LLM. Sections already
    retrieved (by label or chunk identity) are left exactly where they are — we do
    NOT reorder/promote them, because displacing other chunks from the mapper's
    token budget was net-negative (dropped unrelated provisions) without changing
    the LLM's verdict on the promoted section. Returns (chunks, n_added)."""
    have_ids = {rc.chunk.chunk_id for rc in retrieved}
    have_tokens = {infer_section_token(rc.chunk.location_reference.article_number)
                   for rc in retrieved}
    lead_score = max((rc.rerank_score for rc in retrieved), default=0.0) + 1.0

    added: list[RetrievedChunk] = []
    added_ids: set[str] = set()
    for tok in sorted(want_sections):
        if tok in have_tokens:
            continue  # already retrieved by label — leave as-is
        chunk = _find_section_chunk(all_chunks, tok)
        if chunk is None or chunk.chunk_id in have_ids or chunk.chunk_id in added_ids:
            continue  # missing token but the chunk is already present — don't reorder
        added_ids.add(chunk.chunk_id)
        added.append(RetrievedChunk(
            chunk=chunk,
            rerank_score=lead_score,
            context_window=chunk.text,
            retrieval_method="seed_guided",
        ))

    return added + retrieved, len(added)
