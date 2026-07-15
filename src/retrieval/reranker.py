"""
Cross-encoder reranking: top-20 → top-5 with context windows.

Model: cross-encoder/ms-marco-MiniLM-L-6-v2 (Apache 2.0, ~22 M params).
Lazy-loaded once per process.

Context window: ±_CONTEXT_CHARS characters around the chunk boundaries are
appended from the original document text so the LLM sees surrounding
legislative context when it reads the returned passages.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from src.retrieval.models import Chunk, RetrievedChunk

logger = logging.getLogger("retrieval.reranker")

_CONTEXT_CHARS = 300    # characters of surrounding text to include
_TOP_N = 5

_cross_encoder = None


# English reranker (build gate) vs multilingual mMARCO reranker (non-English
# economies), selected per economy to pair with the embedder choice.
_ENGLISH_RERANK_MODEL = os.environ.get("RERANK_MODEL_EN", "").strip() or "cross-encoder/ms-marco-MiniLM-L-6-v2"
_MULTILINGUAL_RERANK_MODEL = os.environ.get("RERANK_MODEL_ML", "").strip() or "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
_use_multilingual = False


def set_multilingual(flag: bool) -> None:
    """Select the multilingual reranker vs the English one. Call at run start."""
    global _use_multilingual, _cross_encoder
    if bool(flag) != _use_multilingual:
        _use_multilingual = bool(flag)
        _cross_encoder = None


def _get_cross_encoder():
    global _cross_encoder
    if _cross_encoder is None:
        from src.retrieval.embedder import quiet_hf_hub
        quiet_hf_hub()
        from sentence_transformers import CrossEncoder  # type: ignore
        name = _MULTILINGUAL_RERANK_MODEL if _use_multilingual else _ENGLISH_RERANK_MODEL
        _cross_encoder = CrossEncoder(name)
        logger.info({"event": "cross_encoder_loaded", "model": name})
    return _cross_encoder


def _build_context_window(
    chunk: Chunk,
    all_chunks: list[Chunk],
    chunk_idx: int,
) -> str:
    """Append text from adjacent chunks as a lightweight context window."""
    parts: list[str] = []

    # Preceding context
    if chunk_idx > 0:
        prev_text = all_chunks[chunk_idx - 1].text
        parts.append(prev_text[-_CONTEXT_CHARS:].strip())

    parts.append(chunk.text)

    # Following context
    if chunk_idx < len(all_chunks) - 1:
        next_text = all_chunks[chunk_idx + 1].text
        parts.append(next_text[:_CONTEXT_CHARS].strip())

    return "\n\n".join(p for p in parts if p)


def rerank(
    query: str,
    candidates: list[tuple[int, float]],  # [(chunk_idx, rrf_score)]
    all_chunks: list[Chunk],
    top_n: int = _TOP_N,
) -> list[RetrievedChunk]:
    """
    Score (query, chunk_text) pairs with the cross-encoder and return top_n
    RetrievedChunk objects, each with a context_window.
    """
    if not candidates:
        return []

    model = _get_cross_encoder()

    # Build (query, passage) pairs for the cross-encoder
    chunk_indices = [idx for idx, _ in candidates]
    pairs = [(query, all_chunks[idx].text) for idx in chunk_indices]

    scores: list[float] = model.predict(pairs, show_progress_bar=False).tolist()

    ranked = sorted(zip(chunk_indices, scores), key=lambda x: x[1], reverse=True)

    results: list[RetrievedChunk] = []
    for idx, score in ranked[:top_n]:
        chunk = all_chunks[idx]
        context = _build_context_window(chunk, all_chunks, idx)
        results.append(RetrievedChunk(
            chunk=chunk,
            rerank_score=score,
            context_window=context,
            retrieval_method="hybrid",
        ))

        logger.info({
            "event": "chunk_reranked",
            "chunk_id": chunk.chunk_id,
            "rerank_score": round(score, 4),
            "act_title": chunk.location_reference.act_title,
            "article_number": chunk.location_reference.article_number,
        })

    return results
