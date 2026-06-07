"""Indicator mapping against known examples + LLM cascade behaviour. [Z2-4]"""
import pytest

def test_singapore_pdpa_p7_mapping_matches_sample_kit():
    pytest.skip("Implement after [Z2-4] — PDPA-first build gate")

def test_cascade_falls_through_on_primary_provider_failure():
    pytest.skip("Implement after [Z2-4] — simulate primary failure, assert silent fallthrough")

def test_llama_3_3_not_used_anywhere():
    pytest.skip("License guard — Llama 3.3 excluded (non-Apache 2.0)")
