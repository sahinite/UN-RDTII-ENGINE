"""
Shared dataclasses for the mapping module.

ExtractionResult  — one extracted provision row, maps 1:1 to a CSV output row.
LLMCostEntry      — per-document LLM cost summary across all indicator calls.
LLMResponse       — raw response from any LLM provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    latency_ms: float
    cost_usd: float


@dataclass
class ExtractionResult:
    """
    One extracted provision row — maps 1:1 to a CSV output row.
    All field names match the 13-column CSV schema exactly.
    """
    # CSV columns 1-6
    economy: str
    law_name: str
    law_number_ref: Optional[str]
    last_amended: Optional[str]
    indicator_id: str
    article: str

    # CSV columns 7-13
    discovery_tag: str                  # "KNOWN" | "NEW"
    location_reference: Optional[str]
    verbatim_snippet: str
    mapping_rationale: Optional[str]    # ≤ 300 chars
    source_url: str
    confidence: Optional[float]         # 0.00–1.00
    notes: Optional[str]

    # Internal metadata — NOT written to CSV
    provider_used: str
    model_used: str
    source_chunk_id: str
    raw_context_before: str
    raw_context_after: str
    verbatim_original: Optional[str]
    doc_type: Optional[str] = None       # "TEXT_PDF" | "SCANNED_PDF" | "HTML" | etc.
    # Document-level KNOWN/NEW (the act's Round 1 status), distinct from the
    # per-provision discovery_tag above. Drives the JSON document envelope tag so
    # a KNOWN act isn't shown as NEW just because its provisions lack anchors.
    doc_discovery_tag: str = "KNOWN"
    flag_for_review: bool = False
    flag_reason: Optional[str] = None
    non_consecutive: bool = False
    # Diagnostic-only: retrieval signal of the source chunk (top retrieved chunk for
    # this indicator) — lets us explain KNOWN cross-indicator mis-maps later.
    source_rerank_score: Optional[float] = None
    source_retrieval_method: Optional[str] = None

    def validate(self) -> None:
        if not self.verbatim_snippet or not self.verbatim_snippet.strip():
            raise ValueError(f"verbatim_snippet is empty for {self.indicator_id}")
        if not self.article or not self.article.strip():
            raise ValueError(f"article is empty for {self.indicator_id}")
        if self.mapping_rationale and len(self.mapping_rationale) > 300:
            raise ValueError(f"mapping_rationale exceeds 300 chars: {len(self.mapping_rationale)}")
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence out of range: {self.confidence}")
        from src.retrieval.config import get_valid_indicator_ids
        if self.indicator_id not in get_valid_indicator_ids():
            raise ValueError(f"Invalid indicator_id: {self.indicator_id}")


@dataclass
class LLMCostEntry:
    """Per-document LLM cost summary across all indicator calls."""
    calls_made: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    total_latency_ms: float = 0.0
    providers_used: list = field(default_factory=list)
    fallback_triggered: bool = False
    per_indicator: dict = field(default_factory=dict)

    def add_call(self, response: LLMResponse, indicator_id: str, elapsed_ms: float) -> None:
        self.calls_made += 1
        self.total_input_tokens += response.input_tokens
        self.total_output_tokens += response.output_tokens
        self.total_cost_usd += response.cost_usd
        self.total_latency_ms += elapsed_ms
        self.providers_used.append(response.provider)
        if len(self.providers_used) > 1 and response.provider != self.providers_used[0]:
            self.fallback_triggered = True
        self.per_indicator[indicator_id] = {
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
            "provider": response.provider,
            "model": response.model,
            "latency_ms": round(elapsed_ms, 1),
        }
