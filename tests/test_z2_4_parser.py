"""
Unit tests for Z2-4 ST4: LLM Response Parser + Verbatim Assertion. [Z2-4 ST7]
"""

from __future__ import annotations

import pytest

from tests.fixtures.z2_4.fixtures import (
    DOC_METADATA,
    NOT_FOUND_LLM_JSON,
    PDPA_CHUNK_TEXT,
    VALID_LLM_JSON,
    make_llm_response,
    make_retrieved_chunk,
)


def test_parse_valid_json_returns_extraction_result():
    from src.mapping.parser import parse_llm_response

    response = make_llm_response(VALID_LLM_JSON)
    results = parse_llm_response(response, "P6-I1", [make_retrieved_chunk()], DOC_METADATA)
    assert len(results) == 1
    r = results[0]
    assert r.article == "Section 26"
    assert r.confidence == 0.95
    assert r.indicator_id == "P6-I1"
    assert r.economy == "Singapore"
    assert r.provider_used == "anthropic"


def test_parse_not_found_returns_empty_list():
    from src.mapping.parser import parse_llm_response

    response = make_llm_response(NOT_FOUND_LLM_JSON)
    results = parse_llm_response(response, "P6-I1", [make_retrieved_chunk()], DOC_METADATA)
    assert results == []


def test_verbatim_assertion_passes_for_exact_match():
    from src.mapping.parser import _assert_verbatim_in_context

    chunks = [make_retrieved_chunk()]
    ok, reason = _assert_verbatim_in_context(
        "An organisation shall not transfer personal data of an individual to a country",
        chunks,
    )
    assert ok is True
    assert reason is None


def test_verbatim_assertion_tolerates_pdfplumber_missing_spaces():
    """pdfplumber sometimes drops inter-word spaces; a correctly-spaced LLM
    snippet must still verify (whitespace-insensitive match)."""
    from src.mapping.parser import _assert_verbatim_in_context

    # Source chunk as pdfplumber mangled it — words run together.
    chunks = [make_retrieved_chunk(
        text="(a) tooverseeandpromotethecybersecurityofcomputersandcomputersystemsinSingapore;"
    )]
    ok, reason = _assert_verbatim_in_context(
        "(a) to oversee and promote the cybersecurity of computers and computer systems in Singapore;",
        chunks,
    )
    assert ok is True, reason


def test_verbatim_assertion_matches_across_chunk_boundary():
    """A snippet that straddles two retrieved chunks still verifies."""
    from src.mapping.parser import _assert_verbatim_in_context

    chunks = [
        make_retrieved_chunk(text="The Commissioner has the following duties and"),
        make_retrieved_chunk(text="functions to protect critical information infrastructure."),
    ]
    ok, reason = _assert_verbatim_in_context(
        "duties and functions to protect critical information infrastructure",
        chunks,
    )
    assert ok is True, reason


def test_verbatim_assertion_fails_hard_discard():
    """Decision 8: failed verbatim assertion → hard discard (empty results)."""
    from src.mapping.parser import parse_llm_response

    hallucinated_json = """{
      "found": true,
      "provisions": [{
        "article": "Section 26",
        "verbatim_snippet": "This text does not appear in any chunk at all — hallucinated!",
        "mapping_rationale": "Maps to P6-I1.",
        "confidence": 0.90,
        "location_reference": "Page 34",
        "non_consecutive": false
      }]
    }"""
    response = make_llm_response(hallucinated_json)
    results = parse_llm_response(response, "P6-I1", [make_retrieved_chunk()], DOC_METADATA)
    assert results == []


def test_verbatim_assertion_fails_allow_unverified_flag(monkeypatch):
    """Decision 8: ALLOW_UNVERIFIED_SNIPPETS=true → retain row with flag_for_review=True."""
    import os
    from src.mapping.parser import parse_llm_response

    monkeypatch.setenv("ALLOW_UNVERIFIED_SNIPPETS", "true")
    hallucinated_json = """{
      "found": true,
      "provisions": [{
        "article": "Section 26",
        "verbatim_snippet": "This text does not appear in any chunk at all — hallucinated!",
        "mapping_rationale": "Maps to P6-I1.",
        "confidence": 0.90,
        "location_reference": "Page 34",
        "non_consecutive": false
      }]
    }"""
    response = make_llm_response(hallucinated_json)
    results = parse_llm_response(response, "P6-I1", [make_retrieved_chunk()], DOC_METADATA)
    assert len(results) == 1
    assert results[0].flag_for_review is True
    assert "verbatim_assertion_failed" in results[0].flag_reason


def test_low_confidence_sets_flag_for_review():
    from src.mapping.parser import parse_llm_response

    low_conf_json = VALID_LLM_JSON.replace('"confidence": 0.95', '"confidence": 0.65')
    response = make_llm_response(low_conf_json)
    results = parse_llm_response(response, "P6-I1", [make_retrieved_chunk()], DOC_METADATA)
    assert results[0].flag_for_review is True
    assert "low_confidence" in results[0].flag_reason
    assert "Recommend human review" in results[0].notes


def test_rationale_over_300_chars_truncated():
    from src.mapping.parser import parse_llm_response

    long_rat = "X" * 400
    json_str = VALID_LLM_JSON.replace(
        '"mapping_rationale": "Section 26 imposes a default prohibition on cross-border transfer of personal data. Maps to P6-I1 because it establishes a blanket restriction on overseas transfer."',
        f'"mapping_rationale": "{long_rat}"',
    )
    response = make_llm_response(json_str)
    results = parse_llm_response(response, "P6-I1", [make_retrieved_chunk()], DOC_METADATA)
    assert len(results[0].mapping_rationale) <= 300


def test_markdown_fenced_json_parsed():
    """LLM wraps JSON in ```json ... ``` — must still parse."""
    from src.mapping.parser import _extract_json

    fenced = '```json\n{"found": false, "provisions": []}\n```'
    result = _extract_json(fenced)
    assert result["found"] is False


def test_prose_wrapped_json_parsed():
    """LLM adds prose before/after JSON — regex fallback must handle."""
    from src.mapping.parser import _extract_json

    noisy = 'Here is the result:\n{"found": false, "provisions": []}\nHope that helps!'
    result = _extract_json(noisy)
    assert result["found"] is False


def test_invalid_json_raises_parse_error():
    from src.mapping.exceptions import ParseError
    from src.mapping.parser import _extract_json

    with pytest.raises(ParseError):
        _extract_json("this is not json at all!!!")


def test_reasoning_model_think_block_stripped():
    """deepseek-r1 / qwq wrap chain-of-thought in <think>…</think> — braces inside
    the reasoning must not corrupt JSON extraction."""
    from src.mapping.parser import _extract_json

    r1 = (
        '<think>\nAnalyse indicator. Output should be {"found": true} shaped. '
        'The section says {retention}. Let me decide...\n</think>\n'
        '```json\n{"found": true, "provisions": [{"indicator_id": "P7-I3"}]}\n```'
    )
    result = _extract_json(r1)
    assert result["found"] is True
    assert result["provisions"][0]["indicator_id"] == "P7-I3"


def test_think_block_without_fence():
    from src.mapping.parser import _extract_json

    r1 = '<think>reasoning with a { stray brace</think>{"found": false, "provisions": []}'
    assert _extract_json(r1)["found"] is False


def test_strip_reasoning_keeps_answer_after_last_close_tag():
    from src.mapping.parser import _strip_reasoning

    assert _strip_reasoning('<think>a{b}c</think>  {"x": 1}') == '{"x": 1}'
    assert _strip_reasoning('no think tags here') == 'no think tags here'


def test_non_consecutive_provisions_expanded_to_two_rows():
    from src.mapping.parser import expand_non_consecutive, parse_llm_response

    non_consec_chunk = make_retrieved_chunk(
        text=(
            "26. Restriction on transfer of personal data outside Singapore\n"
            "An organisation shall not transfer. "
            "Section 31 applies to recipients of transferred data."
        )
    )
    non_consec_json = """{
      "found": true,
      "provisions": [{
        "article": "Section 26(1) and Section 31(2)",
        "verbatim_snippet": "An organisation shall not transfer. Section 31 applies to recipients of transferred data.",
        "mapping_rationale": "Both sections together establish P6-I1.",
        "confidence": 0.88,
        "location_reference": "Page 34",
        "non_consecutive": true
      }]
    }"""
    response = make_llm_response(non_consec_json)
    results = parse_llm_response(response, "P6-I1", [non_consec_chunk], DOC_METADATA)
    expanded = expand_non_consecutive(results)
    assert len(expanded) == 2
    articles = [r.article for r in expanded]
    assert "Section 26(1)" in articles
    assert "Section 31(2)" in articles


def test_empty_article_and_snippet_discarded():
    from src.mapping.parser import parse_llm_response

    empty_prov_json = """{
      "found": true,
      "provisions": [{"article": "", "verbatim_snippet": "", "confidence": 0.9, "non_consecutive": false}]
    }"""
    response = make_llm_response(empty_prov_json)
    results = parse_llm_response(response, "P6-I1", [make_retrieved_chunk()], DOC_METADATA)
    assert results == []


def test_confidence_none_when_missing():
    from src.mapping.parser import parse_llm_response

    no_conf_json = """{
      "found": true,
      "provisions": [{
        "article": "Section 26(1)",
        "verbatim_snippet": "An organisation shall not transfer personal data of an individual to a country or territory outside Singapore",
        "mapping_rationale": "Maps to P6-I1.",
        "non_consecutive": false
      }]
    }"""
    response = make_llm_response(no_conf_json)
    results = parse_llm_response(response, "P6-I1", [make_retrieved_chunk()], DOC_METADATA)
    assert len(results) == 1
    assert results[0].confidence is None
    # No confidence-based flag — article has sub-paragraph so no article_missing_paragraph either
    assert "low_confidence" not in (results[0].flag_reason or "")


def test_provision_tag_from_known_provisions():
    """Seam 3a: provision URL+anchor in known_provisions → tag 'KNOWN' even with KNOWN doc."""
    from src.mapping.parser import parse_llm_response
    from src.crawler.crawler import _normalise_url as normalise_url

    # Build a known_provisions set with the PDPA section 26 anchor
    known = {normalise_url("https://sso.agc.gov.sg/Act/PDPA2012#pr26-")}
    doc_meta = {**DOC_METADATA, "discovery_tag": "KNOWN"}

    response = make_llm_response(VALID_LLM_JSON)
    results = parse_llm_response(response, "P6-I1", [make_retrieved_chunk()], doc_meta, known)
    assert len(results) == 1
    assert results[0].discovery_tag == "KNOWN"


def test_provision_tag_new_when_anchor_absent():
    """Seam 3b: KNOWN doc but anchor not in known_provisions → tag 'NEW'."""
    from src.mapping.parser import parse_llm_response

    known: set = set()  # empty — no known anchor provisions
    doc_meta = {**DOC_METADATA, "discovery_tag": "KNOWN"}

    response = make_llm_response(VALID_LLM_JSON)
    results = parse_llm_response(response, "P6-I1", [make_retrieved_chunk()], doc_meta, known)
    assert len(results) == 1
    assert results[0].discovery_tag == "NEW"


def test_provision_tag_new_doc_always_new():
    """Seam 3c: NEW doc → provision always tagged NEW regardless of known_provisions."""
    from src.mapping.parser import parse_llm_response
    from src.crawler.crawler import _normalise_url as normalise_url

    known = {normalise_url("https://sso.agc.gov.sg/Act/PDPA2012#pr26-")}
    doc_meta = {**DOC_METADATA, "discovery_tag": "NEW"}

    response = make_llm_response(VALID_LLM_JSON)
    results = parse_llm_response(response, "P6-I1", [make_retrieved_chunk()], doc_meta, known)
    assert len(results) == 1
    assert results[0].discovery_tag == "NEW"


def test_extraction_result_validate_catches_bad_indicator():
    from src.mapping.models import ExtractionResult

    r = ExtractionResult(
        economy="Singapore",
        law_name="PDPA",
        law_number_ref=None,
        last_amended=None,
        indicator_id="P9-I9",  # invalid
        article="Section 26",
        discovery_tag="KNOWN",
        location_reference=None,
        verbatim_snippet="Some text",
        mapping_rationale=None,
        source_url="https://example.com",
        confidence=0.95,
        notes=None,
        provider_used="anthropic",
        model_used="claude-sonnet-4-20250514",
        source_chunk_id="test__26__0",
        raw_context_before="",
        raw_context_after="",
        verbatim_original=None,
    )
    with pytest.raises(ValueError, match="Invalid indicator_id"):
        r.validate()


# ── C-safe: location_reference year/page-as-article guard ───────────────────────

def _loc_ref_for(article_number, page):
    """Run one provision through the parser and return its location_reference."""
    from src.mapping.parser import parse_llm_response
    resp = make_llm_response(VALID_LLM_JSON)  # article "Section 26", snippet from PDPA_CHUNK_TEXT
    chunk = make_retrieved_chunk(article=article_number, page=page)
    results = parse_llm_response(resp, "P6-I1", [chunk], DOC_METADATA)
    assert results, "expected one provision"
    return results[0].location_reference or ""


def test_location_ref_drops_year_as_article():
    loc = _loc_ref_for("2020", 538)          # chunker leaked a year into article_number
    assert "Art. 2020" not in loc
    assert "Page 539" in loc


def test_location_ref_drops_page_as_article():
    loc = _loc_ref_for("539", 538)           # article_number == page+1
    assert "Art. 539" not in loc
    assert "Page 539" in loc


def test_location_ref_keeps_real_section():
    loc = _loc_ref_for("26", 538)            # plausible section survives
    assert "Art. 26" in loc
