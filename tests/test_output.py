"""Output schema validation + cost logger. [Z2-5, Z2-6]"""
import pytest

def test_csv_matches_output_template_column_order():
    pytest.skip("Implement after [Z2-6] — compare against OUTPUT_TEMPLATE_31MAY.xlsx")

def test_low_confidence_rows_carry_review_note():
    pytest.skip("Implement after [Z2-5] — confidence < 0.80 -> standard note")

def test_broken_source_urls_are_flagged():
    pytest.skip("Implement after [Z2-5]")
