"""
LLM mapping — maps retrieved chunks to RDTII indicator IDs. [Z2-4]

Per-indicator prompt -> {indicator_id, article, verbatim_snippet,
mapping_rationale, confidence}. Runs against the 5-tier cascade in
src/llm/client.py.

TODO: def map_provision(indicator_id, chunks, llm_client) -> MappedProvision
"""
