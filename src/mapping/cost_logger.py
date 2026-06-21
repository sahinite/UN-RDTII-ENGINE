"""
LLM cost logger — writes logs/cost_report.json. [Z2-4 ST6]

Required by hackathon rubric: judges verify cost claims against this file.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from src.mapping.models import LLMCostEntry

logger = logging.getLogger("mapping.cost_logger")

LOG_DIR = Path(os.environ.get("LOG_DIR", "logs"))
COST_REPORT_PATH = LOG_DIR / "cost_report.json"


class CostLogger:
    """
    Accumulates per-document LLM cost entries and writes to cost_report.json.
    Append mode within a run: multiple documents accumulate in the same report.
    """

    def __init__(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._entries: list[dict] = []
        self._run_start = time.time()
        # Import here to avoid circular; model version may not be pinned at module load
        from src.mapping.llm_client import get_active_model_version
        self._model_version = get_active_model_version()

    def record(
        self,
        economy: str,
        law_name: str,
        source_url: str,
        llm_cost: LLMCostEntry,
        doc_cost_entry=None,       # RAGCostEntry or similar from Z2-3 (may be None)
        translation_cost_entry=None,  # TranslationCostEntry from Z2-2 (may be None)
        ocr_cost_entry=None,       # CostLogEntry from Z2-1 (may be None)
        processing_time_s: float = 0.0,
    ) -> None:
        """Records one document's complete cost breakdown."""
        llm_total = round(llm_cost.total_cost_usd, 6)
        ocr_total = round(getattr(ocr_cost_entry, "cost_usd", 0.0) or 0.0, 6)
        trans_total = round(getattr(translation_cost_entry, "total_cost_usd", 0.0) or 0.0, 6)
        embed_total = 0.0  # sentence-transformers local — always $0

        entry = {
            "document": Path(source_url).name or source_url,
            "economy": economy,
            "law_name": law_name,
            "source_url": source_url,
            "measured_on": time.strftime("%Y-%m-%d"),
            "model_version": self._model_version,
            "processing_time_seconds": round(processing_time_s, 2),
            "costs": {
                "llm": {
                    "provider": llm_cost.providers_used[0] if llm_cost.providers_used else "none",
                    "model": self._model_version,
                    "input_tokens": llm_cost.total_input_tokens,
                    "output_tokens": llm_cost.total_output_tokens,
                    "calls_made": llm_cost.calls_made,
                    "fallback_triggered": llm_cost.fallback_triggered,
                    "cost_usd": llm_total,
                },
                "ocr": {
                    "engine": getattr(ocr_cost_entry, "engine", "none") or "none",
                    "pages": getattr(ocr_cost_entry, "pages", 0) or 0,
                    "cost_usd": ocr_total,
                },
                "translation": {
                    "engine": getattr(translation_cost_entry, "engine_primary", "none") or "none",
                    "chars_translated": getattr(translation_cost_entry, "total_chars", 0) or 0,
                    "cost_usd": trans_total,
                },
                "embedding": {
                    "model": "sentence-transformers/all-MiniLM-L6-v2",
                    "chunks": getattr(doc_cost_entry, "chunks_embedded", 0) or 0,
                    "cost_usd": embed_total,
                },
                "total_cost_usd": round(llm_total + ocr_total + trans_total + embed_total, 6),
            },
            "per_indicator": llm_cost.per_indicator,
        }

        self._entries.append(entry)
        self._write()

    def _write(self) -> None:
        report = {
            "run_date": time.strftime("%Y-%m-%d"),
            "run_duration_seconds": round(time.time() - self._run_start, 1),
            "model_version": self._model_version,
            "documents": self._entries,
            "summary": {
                "total_documents": len(self._entries),
                "total_cost_usd": round(
                    sum(e["costs"]["total_cost_usd"] for e in self._entries), 6
                ),
                "average_cost_per_document_usd": round(
                    sum(e["costs"]["total_cost_usd"] for e in self._entries)
                    / max(len(self._entries), 1),
                    6,
                ),
            },
        }
        COST_REPORT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        logger.debug({
            "event": "cost_report_written",
            "path": str(COST_REPORT_PATH),
            "documents": len(self._entries),
        })

    def get_summary(self) -> dict:
        return {
            "total_documents": len(self._entries),
            "total_cost_usd": round(
                sum(e["costs"]["total_cost_usd"] for e in self._entries), 4
            ),
            "cost_report_path": str(COST_REPORT_PATH),
        }
