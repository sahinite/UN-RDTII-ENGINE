"""
CostLogger — records measured (not estimated) per-component costs. [Z2-6 ST5]

Tracks token counts and $ costs for: OCR / embedding / LLM / crawling.
Writes logs/cost_report.json at the end of each run.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.fetcher.logger import get_logger

logger = get_logger("output.cost_logger")

# Pricing constants (USD per 1K tokens) — from provider pricing pages
# Anthropic claude-sonnet-4-20250514
_ANTHROPIC_INPUT_PRICE_PER_1K = 0.003
_ANTHROPIC_OUTPUT_PRICE_PER_1K = 0.015

# OpenAI gpt-4o
_OPENAI_INPUT_PRICE_PER_1K = 0.0025
_OPENAI_OUTPUT_PRICE_PER_1K = 0.01

# Groq qwen3-32b (free tier — treat as $0)
_GROQ_INPUT_PRICE_PER_1K = 0.0
_GROQ_OUTPUT_PRICE_PER_1K = 0.0

# Ollama — local, $0
_OLLAMA_PRICE_PER_1K = 0.0

# OCR — Tesseract and PaddleOCR are free; Azure OCR / Mistral OCR have costs
_AZURE_OCR_PRICE_PER_PAGE = 0.001   # $0.001/page as of 2025
_MISTRAL_OCR_PRICE_PER_PAGE = 0.001

# Embedding — using local sentence-transformers, free
_EMBEDDING_PRICE_PER_1K_TOKENS = 0.0

_PROVIDER_PRICING = {
    "anthropic": (_ANTHROPIC_INPUT_PRICE_PER_1K, _ANTHROPIC_OUTPUT_PRICE_PER_1K),
    "openai": (_OPENAI_INPUT_PRICE_PER_1K, _OPENAI_OUTPUT_PRICE_PER_1K),
    "groq": (_GROQ_INPUT_PRICE_PER_1K, _GROQ_OUTPUT_PRICE_PER_1K),
    "ollama": (_OLLAMA_PRICE_PER_1K, _OLLAMA_PRICE_PER_1K),
}


def compute_llm_cost(
    provider: str, input_tokens: int, output_tokens: int
) -> float:
    """Return USD cost for LLM call given provider and token counts."""
    in_price, out_price = _PROVIDER_PRICING.get(provider, (0.0, 0.0))
    return (input_tokens / 1000) * in_price + (output_tokens / 1000) * out_price


@dataclass
class ComponentCost:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    pages_processed: int = 0
    calls: int = 0
    latency_ms: float = 0.0


@dataclass
class CostLogger:
    """
    Accumulates measured costs across all pipeline components for one run.
    Call record_* methods during processing, then call save() to persist.
    """
    economy: str
    pillar: int
    pdf_path: str

    _start_time: float = field(default_factory=time.monotonic, init=False)
    _llm: ComponentCost = field(default_factory=ComponentCost, init=False)
    _ocr: ComponentCost = field(default_factory=ComponentCost, init=False)
    _embedding: ComponentCost = field(default_factory=ComponentCost, init=False)
    _crawling: ComponentCost = field(default_factory=ComponentCost, init=False)
    _model_version: str = field(default="unknown", init=False)

    def record_llm_call(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float,
    ) -> None:
        """Record one LLM API call with measured token counts."""
        cost = compute_llm_cost(provider, input_tokens, output_tokens)
        self._llm.input_tokens += input_tokens
        self._llm.output_tokens += output_tokens
        self._llm.cost_usd += cost
        self._llm.calls += 1
        self._llm.latency_ms += latency_ms
        self._model_version = model
        logger.debug({
            "event": "llm_cost_recorded",
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
        })

    def record_ocr_page(
        self,
        engine: str,
        pages: int = 1,
        latency_ms: float = 0.0,
    ) -> None:
        """Record OCR processing cost for N pages."""
        if engine in ("azure", "azure_ocr"):
            cost = pages * _AZURE_OCR_PRICE_PER_PAGE
        elif engine in ("mistral_ocr", "mistral"):
            cost = pages * _MISTRAL_OCR_PRICE_PER_PAGE
        else:
            cost = 0.0  # tesseract / paddleocr / pdfplumber are free
        self._ocr.pages_processed += pages
        self._ocr.cost_usd += cost
        self._ocr.calls += 1
        self._ocr.latency_ms += latency_ms

    def record_llm_ocr_page(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        pages: int = 1,
        latency_ms: float = 0.0,
    ) -> None:
        """Record LLM vision OCR cost — billed as LLM tokens, tracked under OCR component."""
        cost = compute_llm_cost(provider, input_tokens, output_tokens)
        self._ocr.pages_processed += pages
        self._ocr.input_tokens += input_tokens
        self._ocr.output_tokens += output_tokens
        self._ocr.cost_usd += cost
        self._ocr.calls += 1
        self._ocr.latency_ms += latency_ms
        logger.debug({
            "event": "llm_ocr_cost_recorded",
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
            "pages": pages,
        })

    def record_embedding(
        self,
        tokens: int,
        latency_ms: float = 0.0,
    ) -> None:
        """Record embedding cost (local model = $0)."""
        cost = (tokens / 1000) * _EMBEDDING_PRICE_PER_1K_TOKENS
        self._embedding.input_tokens += tokens
        self._embedding.cost_usd += cost
        self._embedding.calls += 1
        self._embedding.latency_ms += latency_ms

    def record_crawl(
        self,
        pages_fetched: int = 1,
        latency_ms: float = 0.0,
    ) -> None:
        """Record crawling cost (Crawl4AI is free; track time only)."""
        self._crawling.pages_processed += pages_fetched
        self._crawling.calls += 1
        self._crawling.latency_ms += latency_ms

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._start_time

    def to_report(self) -> dict:
        elapsed = self.elapsed_seconds()
        total_cost = (
            self._llm.cost_usd
            + self._ocr.cost_usd
            + self._embedding.cost_usd
            + self._crawling.cost_usd
        )
        return {
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "document": self.pdf_path,
            "economy": self.economy,
            "pillar": self.pillar,
            "model_version": self._model_version,
            "processing_time_seconds": round(elapsed, 2),
            "total_cost_usd": round(total_cost, 6),
            "components": {
                "llm": {
                    "calls": self._llm.calls,
                    "input_tokens": self._llm.input_tokens,
                    "output_tokens": self._llm.output_tokens,
                    "cost_usd": round(self._llm.cost_usd, 6),
                    "latency_ms": round(self._llm.latency_ms, 1),
                },
                "ocr": {
                    "pages_processed": self._ocr.pages_processed,
                    "calls": self._ocr.calls,
                    "cost_usd": round(self._ocr.cost_usd, 6),
                    "latency_ms": round(self._ocr.latency_ms, 1),
                    "input_tokens": self._ocr.input_tokens,
                    "output_tokens": self._ocr.output_tokens,
                },
                "embedding": {
                    "calls": self._embedding.calls,
                    "input_tokens": self._embedding.input_tokens,
                    "cost_usd": round(self._embedding.cost_usd, 6),
                    "latency_ms": round(self._embedding.latency_ms, 1),
                },
                "crawling": {
                    "pages_fetched": self._crawling.pages_processed,
                    "calls": self._crawling.calls,
                    "cost_usd": round(self._crawling.cost_usd, 6),
                    "latency_ms": round(self._crawling.latency_ms, 1),
                },
            },
        }

    def save(self, log_dir: Path = Path("logs")) -> Path:
        """Write cost_report.json and return the path."""
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        report_path = log_dir / "cost_report.json"
        report = self.to_report()
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        logger.info({
            "event": "cost_report_saved",
            "path": str(report_path),
            "total_cost_usd": report["total_cost_usd"],
            "processing_time_seconds": report["processing_time_seconds"],
        })
        return report_path
