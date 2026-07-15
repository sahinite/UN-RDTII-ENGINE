"""
Shared dataclasses for the RAG pipeline.

LocationReference  — (act_title, part, article_number, page) tuple carried by every chunk.
Chunk              — The unit passed through chunk → embed → retrieve → rerank.
RetrievedChunk     — Chunk extended with a reranker score and context window.
TaxonomyEntry      — One indicator row loaded from taxonomy.json.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LocationReference:
    """Citable provenance for a single chunk."""
    act_title: str
    part: str           # e.g. "PART II" — empty string if not applicable
    article_number: str # e.g. "26", "26A", "26(1)" — empty string for page-only refs
    page: Optional[int] = None  # 0-based page index, None for HTML docs


@dataclass
class Chunk:
    chunk_id: str                       # "<act_title>__<article_number>__<seq>"
    text: str                           # Passage text (article text or paragraph)
    location_reference: LocationReference
    doc_source_url: str = ""


@dataclass
class RetrievedChunk:
    chunk: Chunk
    rerank_score: float
    context_window: str     # chunk.text ± surrounding context
    retrieval_method: str   # "hybrid" | "bm25" | "dense"


@dataclass
class TaxonomyEntry:
    indicator_id: str
    name: str
    legal_question: str
    probe_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    exclude_act_titles: list[str] = field(default_factory=list)
