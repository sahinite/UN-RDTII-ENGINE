"""
Integration-level output tests — schema, confidence flagging, URL validation. [Z2-5, Z2-6]

These tests operate at the write_outputs / validate_and_flag boundary,
one level above the unit tests in test_z2_6_output.py and test_z2_5_validator.py.
"""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import patch

from src.mapping.models import ExtractionResult
from src.output.models import CSV_COLUMNS
from src.output.validator import REVIEW_NOTE


# ── Shared fixture ─────────────────────────────────────────────────────────────

def _make_extraction_result(**overrides) -> ExtractionResult:
    defaults = dict(
        economy="Singapore",
        law_name="Personal Data Protection Act 2012",
        law_number_ref=None,
        last_amended="2021",
        indicator_id="P7-I1",
        article="Section 24",
        discovery_tag="KNOWN",
        location_reference="Page 32",
        verbatim_snippet=(
            "An organisation shall protect personal data in its possession or under its "
            "control by making reasonable security arrangements to prevent unauthorised access."
        ),
        mapping_rationale=(
            "Section 24 requires security arrangements. Maps to P7-I1 because it "
            "establishes a mandatory protection obligation for personal data."
        ),
        source_url="https://sso.agc.gov.sg/Act/PDPA2012",
        confidence=0.92,
        notes=None,
        provider_used="anthropic",
        model_used="claude-sonnet-4-20250514",
        source_chunk_id="pdpa__24__0",
        raw_context_before="23. Accuracy Obligation.",
        raw_context_after="25. Retention Limitation Obligation.",
        verbatim_original=None,
    )
    defaults.update(overrides)
    return ExtractionResult(**defaults)


# ── Test 1: CSV column order (integration via write_outputs) ──────────────────

def test_csv_matches_output_template_column_order(tmp_path):
    """
    write_outputs (full orchestrator) must produce a CSV whose column names and
    order exactly match the OUTPUT_TEMPLATE_31MAY.xlsx schema.

    Seam: write_outputs → write_csv → on-disk CSV.
    Distinct from test_z2_6_output.py which calls write_csv directly.
    """
    from src.output.validator import ValidatedResult
    from src.output.writer import build_output_record, write_outputs

    result = _make_extraction_result()
    vr = ValidatedResult(
        record=result,
        url_status="ok",
        url_http_status=200,
        archive_url="",
        validated_at="2026-06-22T00:00:00+00:00",
    )
    record = build_output_record(vr, ocr_quality_cer=0.01, processing_time=5)

    summary = write_outputs([record], tmp_path, "Singapore", 7)

    csv_path = Path(summary["csv_path"])
    assert csv_path.exists()

    with open(csv_path, encoding="utf-8-sig") as f:
        headers = list(csv.DictReader(f).fieldnames or [])

    assert headers == CSV_COLUMNS, (
        f"Column mismatch.\n  Got:      {headers}\n  Expected: {CSV_COLUMNS}"
    )


# ── Test 2: Low-confidence note (integration via validate_and_flag) ───────────

def test_low_confidence_rows_carry_review_note(tmp_path):
    """
    A record with confidence < 0.80 must carry the standard review note after
    passing through validate_and_flag, and that note must appear in the output CSV.

    Seam: validate_and_flag → build_output_record → write_outputs → CSV row.
    Distinct from test_z2_6_output.py which tests the parser-level flag only.
    """
    from src.output.validator import validate_and_flag
    from src.output.writer import build_output_record, write_outputs

    low_conf = _make_extraction_result(confidence=0.65, notes=None)

    # archive=False avoids live HTTP calls in tests
    validated = validate_and_flag([low_conf], archive=False)
    assert validated, "validate_and_flag must return a ValidatedResult"

    vr = validated[0]
    assert REVIEW_NOTE in (vr.record.notes or ""), (
        f"REVIEW_NOTE missing from record.notes after validate_and_flag. "
        f"Got: '{vr.record.notes}'"
    )

    record = build_output_record(vr)
    summary = write_outputs([record], tmp_path, "Singapore", 7, skip_invalid=True)

    csv_path = Path(summary["csv_path"])
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    assert rows, "CSV must contain at least one row"
    assert REVIEW_NOTE in rows[0]["notes"], (
        f"REVIEW_NOTE missing from CSV 'notes' column. Got: '{rows[0]['notes']}'"
    )


# ── Test 3: Broken URL flagged (integration via validate_and_flag) ────────────

def test_broken_source_urls_are_flagged(tmp_path):
    """
    When validate_and_flag encounters a broken source URL (HTTP 404), the
    ValidatedResult must record url_status='broken' and the record's notes
    must carry a BROKEN URL marker — visible in the output CSV.

    Seam: validate_and_flag with mocked HTTP → build_output_record → write_outputs.
    Distinct from test_z2_5_validator.py which tests validate_url() in isolation.
    """
    from src.output.validator import validate_and_flag
    from src.output.writer import build_output_record, write_outputs

    broken_result = _make_extraction_result(
        source_url="https://sso.agc.gov.sg/Act/REPEALED2005",
        confidence=0.91,
        notes=None,
    )

    with patch("src.output.validator.validate_url", return_value=("broken", 404)):
        validated = validate_and_flag([broken_result], archive=False)

    assert validated
    vr = validated[0]
    assert vr.url_status == "broken"
    assert "BROKEN URL" in (vr.record.notes or ""), (
        f"Expected 'BROKEN URL' in notes after broken URL detection. "
        f"Got: '{vr.record.notes}'"
    )

    record = build_output_record(vr)
    summary = write_outputs([record], tmp_path, "Singapore", 7, skip_invalid=True)

    csv_path = Path(summary["csv_path"])
    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    assert rows, "CSV must contain at least one row even for broken-URL records"
    assert "BROKEN URL" in rows[0]["notes"], (
        f"BROKEN URL marker missing from CSV 'notes'. Got: '{rows[0]['notes']}'"
    )
