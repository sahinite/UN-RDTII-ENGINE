"""
Unit tests for Per-Indicator Prompt Builder.
"""

from __future__ import annotations

import pytest

from tests.fixtures.z2_4.fixtures import TAXONOMY_FIXTURE, make_retrieved_chunk


def test_build_user_prompt_contains_indicator_id():
    from src.mapping.prompts import build_user_prompt

    chunks = [make_retrieved_chunk()]
    prompt = build_user_prompt("P6-I1", TAXONOMY_FIXTURE, chunks, "PDPA 2012", "Singapore")
    assert "P6-I1" in prompt


def test_build_user_prompt_contains_chunk_text():
    from src.mapping.prompts import build_user_prompt

    chunks = [make_retrieved_chunk()]
    prompt = build_user_prompt("P6-I1", TAXONOMY_FIXTURE, chunks, "PDPA 2012", "Singapore")
    assert "shall not transfer personal data" in prompt


def test_build_user_prompt_contains_negative_examples():
    from src.mapping.prompts import build_user_prompt

    chunks = [make_retrieved_chunk()]
    prompt = build_user_prompt("P6-I1", TAXONOMY_FIXTURE, chunks, "PDPA 2012", "Singapore")
    assert "Banking Act S.47" in prompt


def test_build_user_prompt_contains_legal_question():
    from src.mapping.prompts import build_user_prompt

    chunks = [make_retrieved_chunk()]
    prompt = build_user_prompt("P6-I1", TAXONOMY_FIXTURE, chunks, "PDPA 2012", "Singapore")
    assert "cross-border" in prompt.lower() or "restrict" in prompt.lower()


def test_build_user_prompt_contains_act_title():
    from src.mapping.prompts import build_user_prompt

    chunks = [make_retrieved_chunk()]
    prompt = build_user_prompt("P6-I1", TAXONOMY_FIXTURE, chunks, "PDPA 2012", "Singapore")
    assert "PDPA 2012" in prompt


def test_build_user_prompt_contains_in_scope():
    from src.mapping.prompts import build_user_prompt

    chunks = [make_retrieved_chunk()]
    prompt = build_user_prompt("P6-I1", TAXONOMY_FIXTURE, chunks, "PDPA 2012", "Singapore")
    assert "IN SCOPE" in prompt


def test_build_user_prompt_multiple_chunks():
    from src.mapping.prompts import build_user_prompt

    chunks = [make_retrieved_chunk(), make_retrieved_chunk(text="Other chunk text", article="27")]
    prompt = build_user_prompt("P6-I1", TAXONOMY_FIXTURE, chunks, "PDPA 2012", "Singapore")
    assert "CHUNK 1" in prompt
    assert "CHUNK 2" in prompt


def test_trim_chunks_reduces_to_one_on_huge_chunk():
    from src.mapping.prompts import SYSTEM_PROMPT, trim_chunks_to_budget

    huge_chunk = make_retrieved_chunk(text="A" * 20000)
    chunks = [huge_chunk] * 5
    trimmed = trim_chunks_to_budget(chunks, SYSTEM_PROMPT)
    assert len(trimmed) >= 1  # always keep at least 1


def test_trim_chunks_keeps_all_when_budget_ok():
    from src.mapping.prompts import SYSTEM_PROMPT, trim_chunks_to_budget

    small_chunks = [make_retrieved_chunk(text="Short text " * 10)] * 3
    trimmed = trim_chunks_to_budget(small_chunks, SYSTEM_PROMPT)
    assert len(trimmed) == 3


def test_estimate_tokens():
    from src.mapping.prompts import estimate_tokens

    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("") == 0


def test_system_prompt_contains_verbatim_rules():
    from src.mapping.prompts import SYSTEM_PROMPT

    assert "VERBATIM ONLY" in SYSTEM_PROMPT
    assert "JSON OUTPUT ONLY" in SYSTEM_PROMPT
    assert "found" in SYSTEM_PROMPT
    assert "provisions" in SYSTEM_PROMPT


def test_load_taxonomy_dict_has_all_indicators():
    from src.mapping.prompts import load_taxonomy_dict

    taxonomy = load_taxonomy_dict()
    for pillar in [6, 7]:
        for i in range(1, 6):
            indicator_id = f"P{pillar}-I{i}"
            assert indicator_id in taxonomy, f"Missing {indicator_id} in taxonomy"


def test_load_taxonomy_dict_has_in_scope_fields():
    from src.mapping.prompts import load_taxonomy_dict

    taxonomy = load_taxonomy_dict()
    assert "in_scope" in taxonomy["P6-I1"]
    assert "out_of_scope" in taxonomy["P6-I1"]
    assert len(taxonomy["P6-I1"]["in_scope"]) > 0
