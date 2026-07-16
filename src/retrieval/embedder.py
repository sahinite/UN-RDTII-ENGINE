"""
Sentence-transformer embeddings + FAISS dense search.

EmbeddingIndex wraps a FAISS flat-IP index and a chunk list so callers
can call dense_search() without managing numpy arrays.

Model: all-MiniLM-L6-v2 (Apache 2.0, 22 M params, 384-dim vectors).
Lazy-loaded on first use; model is module-level singleton so the weights
are loaded once per process.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from src.retrieval.models import Chunk

logger = logging.getLogger("retrieval.embedder")

# ── Lazy model singleton ───────────────────────────────────────────────────────

_model = None


def quiet_hf_hub() -> None:
    """Silence the benign 'unauthenticated requests to the HF Hub' notice.

    Local model weights are cached, so no token is needed — this just hides the
    rate-limit reminder huggingface_hub prints on load. Shared by the embedder
    and reranker model loaders.
    """
    import logging as _logging
    import os as _os
    _os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    _os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    _logging.getLogger("huggingface_hub").setLevel(_logging.ERROR)


# Two embedders, selected per economy:
#  - English-only economies (SG, AU) use the English-specialised model — it retrieves
#    better on English than the multilingual one (empirically 18 vs 10 SG P7 records),
#    so the build gate is preserved.
#  - Non-English economies (MY) use the multilingual model so RAG runs on the ORIGINAL
#    text (no whole-document translation; only retrieved passages reach the LLM).
_ENGLISH_EMBED_MODEL = os.environ.get("EMBED_MODEL_EN", "").strip() or "all-MiniLM-L6-v2"
_MULTILINGUAL_EMBED_MODEL = os.environ.get("EMBED_MODEL_ML", "").strip() or "paraphrase-multilingual-MiniLM-L12-v2"
_use_multilingual = False


def set_multilingual(flag: bool) -> None:
    """Select the multilingual embedder (non-English economies) vs the English one.
    Call once at run start; resets the cached model if the choice changed."""
    global _use_multilingual, _model
    if bool(flag) != _use_multilingual:
        _use_multilingual = bool(flag)
        _model = None  # force reload with the newly-selected model


def _get_model():
    global _model
    if _model is None:
        quiet_hf_hub()
        from sentence_transformers import SentenceTransformer
        name = _MULTILINGUAL_EMBED_MODEL if _use_multilingual else _ENGLISH_EMBED_MODEL
        _model = SentenceTransformer(name)
        logger.info({"event": "embedding_model_loaded", "model": name})
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


