"""
Unit tests for Z2-4 ST5: Mapper Orchestrator. [Z2-4 ST7]
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.fixtures.z2_4.fixtures import (
    P7_LLM_JSON,
    PDPA_P7_CHUNK_TEXT,
    TAXONOMY_FIXTURE,
    VALID_LLM_JSON,
    make_llm_response,
    make_retrieved_chunk,
)


def _make_rag_result(indicator_id: str, chunk_text: str = None):
    m = MagicMock()
    m.indicator_id = indicator_id
    m.top_chunks = [make_retrieved_chunk(text=chunk_text) if chunk_text else make_retrieved_chunk()]
    return m


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


def test_extract_provisions_returns_results_for_pdpa():
    """
    Acceptance Criteria 1: Singapore PDPA produces at least one P7 provision.
    """
    from src.mapping.mapper import extract_provisions

    rag_results = [_make_rag_result("P7-I1", PDPA_P7_CHUNK_TEXT)]
    doc = _make_doc("SG")

    with patch("src.mapping.mapper.call_llm_with_cascade", return_value=make_llm_response(P7_LLM_JSON)):
        with patch("src.mapping.mapper.load_taxonomy_dict", return_value=TAXONOMY_FIXTURE):
            results, cost = extract_provisions(rag_results, doc)

    assert len(results) >= 1
    p7_results = [r for r in results if r.indicator_id == "P7-I1"]
    assert len(p7_results) >= 1
    assert p7_results[0].confidence >= 0.80


def test_extract_provisions_accumulates_cost():
    from src.mapping.mapper import extract_provisions

    rag_results = [
        _make_rag_result("P6-I1"),
        _make_rag_result("P7-I1", PDPA_P7_CHUNK_TEXT),
    ]
    doc = _make_doc("SG")

    with patch("src.mapping.mapper.call_llm_with_cascade", return_value=make_llm_response()):
        with patch("src.mapping.mapper.load_taxonomy_dict", return_value=TAXONOMY_FIXTURE):
            results, cost = extract_provisions(rag_results, doc)

    assert cost.calls_made == 2
    assert cost.total_cost_usd > 0


def test_extract_provisions_skips_empty_chunks():
    from src.mapping.mapper import extract_provisions

    empty_rag = MagicMock()
    empty_rag.indicator_id = "P6-I1"
    empty_rag.top_chunks = []

    doc = _make_doc("SG")

    with patch("src.mapping.mapper.call_llm_with_cascade") as mock_llm:
        with patch("src.mapping.mapper.load_taxonomy_dict", return_value=TAXONOMY_FIXTURE):
            results, cost = extract_provisions([empty_rag], doc)

    mock_llm.assert_not_called()
    assert results == []
    assert cost.calls_made == 0


def test_all_providers_exhausted_skips_indicator_not_crash():
    """Acceptance Criteria 2: cascade failure completes run without exception."""
    from src.mapping.exceptions import AllProvidersExhaustedError
    from src.mapping.mapper import extract_provisions

    rag_results = [
        _make_rag_result("P6-I1"),
        _make_rag_result("P7-I1", PDPA_P7_CHUNK_TEXT),
    ]
    doc = _make_doc("SG")

    with patch("src.mapping.mapper.call_llm_with_cascade", side_effect=AllProvidersExhaustedError("all failed")):
        with patch("src.mapping.mapper.load_taxonomy_dict", return_value=TAXONOMY_FIXTURE):
            results, cost = extract_provisions(rag_results, doc)

    assert results == []
    assert cost.calls_made == 0


def test_pdpa_gate_passes_on_valid_p7_result():
    from src.mapping.mapper import check_pdpa_gate

    results = [MagicMock(indicator_id="P7-I1", confidence=0.92)]
    check_pdpa_gate("SG", results)  # must not raise


def test_pdpa_gate_raises_on_no_p7_result():
    from src.mapping.exceptions import PDPAGateError
    from src.mapping.mapper import check_pdpa_gate

    check_pdpa_gate("AU", [])  # non-SG economies skip gate

    with pytest.raises(PDPAGateError):
        check_pdpa_gate("SG", [])  # SG with no P7 → must raise


def test_pdpa_gate_raises_when_all_p7_low_confidence():
    from src.mapping.exceptions import PDPAGateError
    from src.mapping.mapper import check_pdpa_gate

    low_conf = [MagicMock(indicator_id="P7-I1", confidence=0.50)]
    with pytest.raises(PDPAGateError):
        check_pdpa_gate("SG", low_conf)


def test_deduplication_removes_same_snippet():
    from src.mapping.mapper import _deduplicate
    from src.mapping.models import ExtractionResult

    def _make_result(conf):
        return ExtractionResult(
            economy="Singapore",
            law_name="PDPA",
            law_number_ref=None,
            last_amended=None,
            indicator_id="P6-I1",
            article="Section 26",
            discovery_tag="KNOWN",
            location_reference=None,
            verbatim_snippet="An organisation shall not transfer personal data",
            mapping_rationale=None,
            source_url="https://sso.agc.gov.sg/Act/PDPA2012",
            confidence=conf,
            notes=None,
            provider_used="anthropic",
            model_used="claude-sonnet-4-20250514",
            source_chunk_id="test__26__0",
            raw_context_before="",
            raw_context_after="",
            verbatim_original=None,
        )

    r1 = _make_result(0.90)
    r2 = _make_result(0.75)
    deduped = _deduplicate([r1, r2])
    assert len(deduped) == 1
    assert deduped[0].confidence == 0.90  # higher confidence kept


def test_extract_provisions_accepts_dict_rag_results():
    """retrieve_batch returns dict — mapper must handle it."""
    from src.mapping.mapper import extract_provisions

    rag_dict = {"P7-I1": [make_retrieved_chunk(text=PDPA_P7_CHUNK_TEXT)]}
    doc = _make_doc("SG")

    with patch("src.mapping.mapper.call_llm_with_cascade", return_value=make_llm_response(P7_LLM_JSON)):
        with patch("src.mapping.mapper.load_taxonomy_dict", return_value=TAXONOMY_FIXTURE):
            results, cost = extract_provisions(rag_dict, doc)

    assert cost.calls_made == 1


def test_economy_name_mapping():
    """_official_un_name converts ISO to UN official name."""
    from src.mapping.mapper import _official_un_name

    assert _official_un_name("SG") == "Singapore"
    assert _official_un_name("AU") == "Australia"
    assert _official_un_name("TH") == "Thailand"


def test_unknown_economy_returns_iso_code():
    from src.mapping.mapper import _official_un_name

    result = _official_un_name("XX")
    assert result == "XX"  # falls through gracefully
