"""
RAG pipeline configuration + taxonomy loader. [Z2-3 ST6]
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from src.retrieval.models import TaxonomyEntry

logger = logging.getLogger("retrieval.config")

_TAXONOMY_PATH = Path(__file__).parent.parent.parent / "taxonomy.json"

_taxonomy_cache: Optional[list[TaxonomyEntry]] = None


def load_taxonomy(path: Path = _TAXONOMY_PATH) -> list[TaxonomyEntry]:
    global _taxonomy_cache
    if _taxonomy_cache is not None:
        return _taxonomy_cache
    with open(path, encoding="utf-8") as fh:
        raw: list[dict] = json.load(fh)
    entries = [
        TaxonomyEntry(
            indicator_id=r["indicator_id"],
            name=r.get("name", ""),
            legal_question=r.get("legal_question", ""),
            probe_keywords=r.get("probe_keywords", []),
            exclude_keywords=r.get("exclude_keywords", []),
            exclude_act_titles=r.get("exclude_act_titles", []),
        )
        for r in raw
    ]
    _taxonomy_cache = entries
    logger.info({"event": "taxonomy_loaded", "indicators": len(entries)})
    return entries


def get_indicator(indicator_id: str) -> TaxonomyEntry:
    """Lookup a single indicator; raises KeyError if not found."""
    for entry in load_taxonomy():
        if entry.indicator_id == indicator_id:
            return entry
    raise KeyError(f"Unknown indicator_id: {indicator_id!r}")


def get_valid_indicator_ids() -> frozenset[str]:
    """Return frozenset of all indicator_id values from taxonomy.json."""
    return frozenset(e.indicator_id for e in load_taxonomy())


# Pipeline hyper-parameters (env-overridable for recall/cost tuning).
# Larger candidate pools + a deeper rerank cut surface provisions buried in big
# acts (e.g. PDPA's DPO clause ranks ~18th; CPC's access powers sit in a 1M-char
# code). Raise RERANK_TOP_N toward 20 for max recall at higher LLM token cost.
BM25_TOP_K = int(os.getenv("BM25_TOP_K", "30"))
DENSE_TOP_K = int(os.getenv("DENSE_TOP_K", "30"))
FUSION_TOP_K = int(os.getenv("FUSION_TOP_K", "30"))
RERANK_TOP_N = int(os.getenv("RERANK_TOP_N", "12"))
