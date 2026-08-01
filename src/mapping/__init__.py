"""
Zone 2 Intelligent Mapping package.

Ties the 7-tier LLM cascade, prompt builder, and response parser into a
single extraction pipeline that maps retrieved passages to RDTII indicators.

Public API:
    extract_provisions(rag_results, doc) -> (list[ExtractionResult], LLMCostEntry)
        Main entry point. Accepts RAG results (dict or list) and a FetchedDocument /
        TranslatedDocument. Returns all extracted provisions + cost breakdown.

    check_quality_gate(economy, pillar, results)
        Raises QualityGateError when the configured economy/pillar quality
        threshold is not met.

LLM cascade (imported from llm_client):
    pin_active_provider()          — call once at startup
    call_llm_with_cascade(...)     — call per indicator
    get_active_model_version()     — returns "{provider}/{model}" string

Models:
    ExtractionResult   — one mapped provision (13 CSV columns + internal metadata)
    LLMCostEntry       — per-document token + cost accumulator
    LLMResponse        — raw provider response wrapper

Exceptions:
    AllProvidersExhaustedError, QualityGateError, ConfigError, ParseError
"""

from src.mapping.mapper import check_pdpa_gate, check_quality_gate, extract_provisions
from src.mapping.llm_client import (
    call_llm_with_cascade,
    get_active_model_version,
    pin_active_provider,
    PROVIDER_CASCADE,
)
from src.mapping.models import ExtractionResult, LLMCostEntry, LLMResponse
from src.mapping.exceptions import (
    AllProvidersExhaustedError,
    ConfigError,
    MappingError,
    PDPAGateError,
    QualityGateError,
    ParseError,
    ProviderAPIError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)

__all__ = [
    # Pipeline entry points
    "extract_provisions",
    "check_quality_gate",
    "check_pdpa_gate",
    # LLM cascade
    "pin_active_provider",
    "call_llm_with_cascade",
    "get_active_model_version",
    "PROVIDER_CASCADE",
    # Models
    "ExtractionResult",
    "LLMCostEntry",
    "LLMResponse",
    # Exceptions
    "MappingError",
    "AllProvidersExhaustedError",
    "PDPAGateError",
    "QualityGateError",
    "ParseError",
    "ConfigError",
    "ProviderAPIError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
]
