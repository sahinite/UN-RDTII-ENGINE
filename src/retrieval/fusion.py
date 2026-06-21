"""
Reciprocal Rank Fusion: BM25 + dense → top-20. [Z2-3 ST4]

rrf_fusion() merges two ranked lists (each a list of (chunk_index, score))
using the standard RRF formula: score = Σ 1/(k + rank).

k=60 is the standard RRF constant that balances early vs. late ranks.
"""

from __future__ import annotations


_RRF_K = 60


def rrf_fusion(
    bm25_results: list[tuple[int, float]],
    dense_results: list[tuple[int, float]],
    top_k: int = 20,
) -> list[tuple[int, float]]:
    """
    Returns [(chunk_index, rrf_score)] sorted descending.

    Both input lists are [(chunk_index, score)] — only the rank position matters,
    not the original score.
    """
    scores: dict[int, float] = {}

    for rank, (idx, _) in enumerate(bm25_results, start=1):
        scores[idx] = scores.get(idx, 0.0) + 1.0 / (_RRF_K + rank)

    for rank, (idx, _) in enumerate(dense_results, start=1):
        scores[idx] = scores.get(idx, 0.0) + 1.0 / (_RRF_K + rank)

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return merged[:top_k]
