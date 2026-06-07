"""
RAG pipeline: chunk -> embed -> BM25 + dense hybrid retrieval -> cross-encoder
rerank -> top-5 chunks per indicator query. [Z2-3]

TODO: def retrieve(indicator_id, embedding_index, query) -> list[Chunk]  # top-5, each with location_reference
"""
