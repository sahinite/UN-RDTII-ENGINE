"""
Zone 2 retrieval package — RAG pipeline.

Public surface:
    retrieve(indicator_id, doc)          -> list[RetrievedChunk]
    retrieve_batch(indicator_ids, doc)   -> dict[str, list[RetrievedChunk]]
"""

from src.retrieval.rag import retrieve, retrieve_batch
from src.retrieval.models import Chunk, LocationReference, RetrievedChunk, TaxonomyEntry

__all__ = [
    "retrieve",
    "retrieve_batch",
    "Chunk",
    "LocationReference",
    "RetrievedChunk",
    "TaxonomyEntry",
]
