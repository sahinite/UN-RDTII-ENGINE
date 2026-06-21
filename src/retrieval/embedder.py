"""
Sentence-transformer embeddings + FAISS dense search. [Z2-3 ST2]

EmbeddingIndex wraps a FAISS flat-IP index and a chunk list so callers
can call dense_search() without managing numpy arrays.

Model: all-MiniLM-L6-v2 (Apache 2.0, 22 M params, 384-dim vectors).
Lazy-loaded on first use; model is module-level singleton so the weights
are loaded once per process.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from src.retrieval.models import Chunk

logger = logging.getLogger("retrieval.embedder")

# ── Lazy model singleton ───────────────────────────────────────────────────────

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info({"event": "embedding_model_loaded", "model": "all-MiniLM-L6-v2"})
    return _model


# ── Index dataclass ────────────────────────────────────────────────────────────

@dataclass
class EmbeddingIndex:
    chunks: list[Chunk]
    _matrix: Optional[np.ndarray] = field(default=None, repr=False)
    _faiss_index: object = field(default=None, repr=False)

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(self) -> None:
        """Compute embeddings and populate the FAISS index."""
        import faiss  # type: ignore

        model = _get_model()
        texts = [c.text for c in self.chunks]
        matrix: np.ndarray = model.encode(
            texts,
            batch_size=64,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        ).astype("float32")

        self._matrix = matrix
        dim = matrix.shape[1]
        index = faiss.IndexFlatIP(dim)
        index.add(matrix)
        self._faiss_index = index

        logger.info({
            "event": "embedding_index_built",
            "chunks": len(self.chunks),
            "dim": dim,
        })

    # ── Query ─────────────────────────────────────────────────────────────────

    def query_vector(self, text: str) -> np.ndarray:
        model = _get_model()
        vec: np.ndarray = model.encode(
            [text],
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        ).astype("float32")
        return vec

    def dense_search(self, query_text: str, top_k: int = 20) -> list[tuple[int, float]]:
        """
        Returns [(chunk_index, cosine_score)] sorted descending.
        chunk_index is the position in self.chunks.
        """
        if self._faiss_index is None:
            raise RuntimeError("Call build() before dense_search()")
        vec = self.query_vector(query_text)
        scores, indices = self._faiss_index.search(vec, min(top_k, len(self.chunks)))
        return [(int(idx), float(score)) for idx, score in zip(indices[0], scores[0]) if idx >= 0]


# ── Public factory ─────────────────────────────────────────────────────────────

def build_index(chunks: list[Chunk]) -> EmbeddingIndex:
    idx = EmbeddingIndex(chunks=chunks)
    idx.build()
    return idx


def embed_query(text: str) -> np.ndarray:
    """Convenience: embed a single query string, returns normalised float32 vector."""
    return _get_model().encode(
        [text],
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=True,
    ).astype("float32")[0]
