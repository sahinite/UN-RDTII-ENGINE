"""
BM25 retrieval with legal keyword boosting and negative filtering. [Z2-3 ST3]

BM25Index wraps rank_bm25.BM25Okapi.  When searching for an indicator:
  - probe_keywords from TaxonomyEntry boost the BM25 score by a configurable factor
  - exclude_keywords / exclude_act_titles filter out irrelevant chunks before returning
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from src.retrieval.models import Chunk, TaxonomyEntry

logger = logging.getLogger("retrieval.bm25")

_BOOST_FACTOR = 1.5     # multiplier applied to score when a probe_keyword is present
_TOKENISE_RE = re.compile(r"[^a-z0-9]+")


def _tokenise(text: str) -> list[str]:
    return [t for t in _TOKENISE_RE.split(text.lower()) if t]


def _matches_any(text_lower: str, terms: list[str]) -> bool:
    return any(t.lower() in text_lower for t in terms)


@dataclass
class BM25Index:
    chunks: list[Chunk]
    _bm25: object = field(default=None, repr=False)
    _tokenised_corpus: list[list[str]] = field(default_factory=list, repr=False)

    def build(self) -> None:
        from rank_bm25 import BM25Okapi  # type: ignore
        self._tokenised_corpus = [_tokenise(c.text) for c in self.chunks]
        self._bm25 = BM25Okapi(self._tokenised_corpus)
        logger.info({"event": "bm25_index_built", "chunks": len(self.chunks)})

    def search(
        self,
        query: str,
        indicator: TaxonomyEntry,
        top_k: int = 20,
    ) -> list[tuple[int, float]]:
        """
        Returns [(chunk_index, score)] sorted descending after:
        1. Negative filtering — removes chunks that match exclude_keywords or
           whose act_title matches exclude_act_titles.
        2. Probe-keyword boosting — multiplies score by _BOOST_FACTOR for
           each unique probe_keyword hit found in the chunk text.
        """
        if self._bm25 is None:
            raise RuntimeError("Call build() before search()")

        # Expand query with indicator probe keywords
        expanded_query = query + " " + " ".join(indicator.probe_keywords)
        query_tokens = _tokenise(expanded_query)
        raw_scores: list[float] = self._bm25.get_scores(query_tokens).tolist()

        results: list[tuple[int, float]] = []
        for idx, (chunk, score) in enumerate(zip(self.chunks, raw_scores)):
            text_lower = chunk.text.lower()
            act_lower = chunk.location_reference.act_title.lower()

            # Negative filter
            if _matches_any(act_lower, indicator.exclude_act_titles):
                continue
            if _matches_any(text_lower, indicator.exclude_keywords):
                continue

            # Probe-keyword boost
            boosted = score
            for kw in indicator.probe_keywords:
                if kw.lower() in text_lower:
                    boosted *= _BOOST_FACTOR

            results.append((idx, boosted))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]


def build_bm25(chunks: list[Chunk]) -> BM25Index:
    idx = BM25Index(chunks=chunks)
    idx.build()
    return idx
