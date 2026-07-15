"""
Integration-level mapper tests — Phase 1 gate assertions.

These tests operate at the extract_provisions / PROVIDER_CASCADE boundary,
one level above the unit tests in test_z2_4_mapper.py and test_z2_4_cascade.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.fixtures.z2_4.fixtures import (
    P7_LLM_JSON,
    PDPA_P7_CHUNK_TEXT,
    TAXONOMY_FIXTURE,
    make_llm_response,
    make_retrieved_chunk,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_doc(economy: str = "SG"):
    m = MagicMock()
    m.economy = economy
    m.act_title = "Personal Data Protection Act 2012"
    m.source_url = "https://sso.agc.gov.sg/Act/PDPA2012"
    m.discovery_tag = "KNOWN"
    m.law_number_ref = None
    m.last_amended_year = "2021"
    m.verbatim_original = None
    return m


def _make_rag_result(indicator_id: str, chunk_text: str = PDPA_P7_CHUNK_TEXT):
    m = MagicMock()
    m.indicator_id = indicator_id
    m.top_chunks = [make_retrieved_chunk(text=chunk_text)]
    return m


# ── Test 1: PDPA sample-kit gate ───────────────────────────────────────────────

def test_singapore_pdpa_p7_mapping_matches_sample_kit(tmp_path):
    """
    Phase 1 gate: extract_provisions on PDPA P7 text produces results whose
    indicator IDs match what evaluate() expects from the sample kit.

    Seam: extract_provisions output → write_outputs CSV → evaluate() report.
    Distinct from test_z2_4_mapper.py which only checks ExtractionResult fields.
    """
    from src.mapping.mapper import extract_provisions
    from src.output.writer import build_output_record, write_outputs
    from src.output.validator import ValidatedResult
    from evaluate import evaluate

    # TAXONOMY_FIXTURE has P7-I1; use it for the single indicator under test
    rag_results = [_make_rag_result("P7-I1")]
    doc = _make_doc("SG")

    with patch("src.mapping.mapper.call_llm_with_cascade", return_value=make_llm_response(P7_LLM_JSON)):
        with patch("src.mapping.mapper.load_taxonomy_dict", return_value=TAXONOMY_FIXTURE):
            results, _ = extract_provisions(rag_results, doc)

    assert results, "extract_provisions must return at least one P7 provision"

    # Wrap each ExtractionResult in a minimal ValidatedResult (offline — no HTTP)
    now = "2026-06-22T00:00:00+00:00"
    validated = [
        ValidatedResult(record=r, url_status="ok", url_http_status=200,
                        archive_url="", validated_at=now)
        for r in results
    ]

    # Build OutputRecords and write to temp CSV; use the returned path directly
    # to avoid glob case-sensitivity issues in evaluate()'s auto-detection.
    records = [build_output_record(vr) for vr in validated]
    summary = write_outputs(records, tmp_path, "Singapore", 7, skip_invalid=True)
    csv_path = Path(summary["csv_path"])

    # evaluate() must find at least one KNOWN indicator matched (P7-I1 is in sample kit)
    report = evaluate(
        sample_kit_dir=Path("data/sample_kit"),
        economy="Singapore",
        pillar=7,
        csv_path=csv_path,
    )
    assert report["scores"]["known_matched"] >= 1, (
        f"evaluate() matched 0 KNOWN indicators. "
        f"Engine indicators: {report['engine_indicators']} | "
        f"Missed: {report['missed_known']}"
    )


# ── Test 2: Cascade fallthrough at extract_provisions level ───────────────────

def test_cascade_falls_through_on_primary_provider_failure():
    """
    When all providers are exhausted for one indicator, extract_provisions
    silently skips it and continues extracting the remaining indicators.

    Seam: extract_provisions → call_llm_with_cascade (mocked).
    Distinct from test_z2_4_cascade.py which tests call_llm_with_cascade directly.

    Contract: call_llm_with_cascade raises AllProvidersExhaustedError when all
    cascade tiers fail for a given call — extract_provisions must catch it and
    continue, not re-raise.
    """
    from src.mapping.exceptions import AllProvidersExhaustedError
    from src.mapping.mapper import extract_provisions

    call_count = {"n": 0}
    fallback_model = make_llm_response(P7_LLM_JSON, provider="openai")

    def _side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First indicator: all providers exhausted — mapper must catch and skip
            raise AllProvidersExhaustedError("all 5 providers failed for P7-I1")
        return fallback_model

    rag_results = [_make_rag_result("P7-I1"), _make_rag_result("P7-I2")]
    doc = _make_doc("SG")

    # Extend taxonomy to include P7-I2 so the second rag_result gets an LLM call
    extended_taxonomy = {**TAXONOMY_FIXTURE, "P7-I2": TAXONOMY_FIXTURE["P7-I1"]}

    with patch("src.mapping.mapper.call_llm_with_cascade", side_effect=_side_effect):
        with patch("src.mapping.mapper.load_taxonomy_dict", return_value=extended_taxonomy):
            results, cost = extract_provisions(rag_results, doc)

    # P7-I1 was skipped (AllProvidersExhaustedError); P7-I2 succeeded via fallback
    assert cost.calls_made >= 1, "At least one LLM call must have completed"
    assert isinstance(results, list)
    # Run must NOT raise — that is the core contract under test


# ── Test 3: Llama 3.3 license guard ──────────────────────────────────────────

def test_llama_3_3_not_used_anywhere():
    """
    License guard: no provider in the cascade may resolve to Llama 3.3
    (non-Apache 2.0 — explicitly excluded per CLAUDE.md and PRD).
    """
    from src.mapping.llm_client import PROVIDER_CASCADE

    forbidden = ("llama-3.3", "llama3.3", "llama_3.3", "llama3-3")

    for provider in PROVIDER_CASCADE:
        model_lower = provider.model.lower()
        for pattern in forbidden:
            assert pattern not in model_lower, (
                f"Provider '{provider.provider_name}' uses model '{provider.model}' "
                f"which matches forbidden pattern '{pattern}' (non-Apache 2.0 license)."
            )
