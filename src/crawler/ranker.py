"""
Two-pass discovery tagging (KNOWN/NEW) + ranking. [Z1-5]

Pass 1: seed from Round 1 Database -> tag KNOWN
Pass 2: broader independent search -> tag NEW (worth 20/40 accuracy points)
Ranks candidates via semantic similarity + BM25 + exclusion rules, gated by
a low-cost LLM (DeepSeek V3 — Tier-3 of the cascade).

Hands the top 3-5 ranked candidate acts to Zone 2.

TODO: def tag_and_rank(candidates, round1_db) -> list[RankedAct]  # top 3-5, with discovery_tag
"""
