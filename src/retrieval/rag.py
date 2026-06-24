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
) -> dict[str, list[RetrievedChunk]]:
    """
    Amortised batch retrieval: chunk and build indexes once, then query per indicator.
    Returns {indicator_id: [RetrievedChunk, ...]} for each requested indicator.
    """
    chunks: list[Chunk] = chunk_document(doc)
    if not chunks:
        return {iid: [] for iid in indicator_ids}

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
        results[iid] = retrieved

        logger.info({
            "event": "batch_indicator_done",
            "indicator_id": iid,
            "returned": len(retrieved),
        })

    return results
