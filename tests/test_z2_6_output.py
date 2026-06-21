"""
Unit tests for Z2-6: Output Writer, Schema Validator, Cost Logger. [Z2-6 ST7]
Covers all 3 acceptance criteria:
  AC1 — CSV column order matches OUTPUT_TEMPLATE_31MAY.xlsx exactly
  AC2 — JSON extended fields are present and populated
  AC3 — Cost report has non-zero token counts and measured $ costs
"""

from __future__ import annotations

import csv
import io
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from src.output.models import CSV_COLUMNS, OutputRecord, OutputSchemaError
from src.output.writer import (
    build_output_record,
    validate_record,
    write_csv,
    write_json,
    write_outputs,
)
from src.output.cost_logger import CostLogger, compute_llm_cost


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_output_record(**overrides) -> OutputRecord:
    defaults = dict(
        economy="Singapore",
        law_name="Personal Data Protection Act 2012",
        law_number_ref=None,
        last_amended="2021",
        indicator_id="P7-I1",
        article="Section 24",
        discovery_tag="KNOWN",
        location_reference="Page 32",
        verbatim_snippet="An organisation shall protect personal data.",
        mapping_rationale="Section 24 requires security. Maps to P7-I1.",
        source_url="https://sso.agc.gov.sg/Act/PDPA2012",
        confidence=0.92,
        notes=None,
        ocr_quality_cer=0.02,
        processing_time_seconds=12.5,
        model_version="claude-sonnet-4-20250514",
        raw_context_before="23. Accuracy Obligation.",
        raw_context_after="25. Retention Limitation Obligation.",
        verbatim_original=None,
        archive_url="https://web.archive.org/web/20240101/https://sso.agc.gov.sg/Act/PDPA2012",
    )
    defaults.update(overrides)
    return OutputRecord(**defaults)


def _make_validated_result(record=None, archive_url=""):
    from src.output.validator import ValidatedResult
    from src.mapping.models import ExtractionResult

    if record is None:
        record = ExtractionResult(
            economy="Singapore",
            law_name="Personal Data Protection Act 2012",
            law_number_ref=None,
            last_amended="2021",
            indicator_id="P7-I1",
            article="Section 24",
            discovery_tag="KNOWN",
            location_reference="Page 32",
            verbatim_snippet="An organisation shall protect personal data.",
            mapping_rationale="Section 24 requires security. Maps to P7-I1.",
            source_url="https://sso.agc.gov.sg/Act/PDPA2012",
            confidence=0.92,
            notes=None,
            provider_used="anthropic",
            model_used="claude-sonnet-4-20250514",
            source_chunk_id="chunk_001",
            raw_context_before="23. Accuracy Obligation.",
            raw_context_after="25. Retention Limitation Obligation.",
            verbatim_original=None,
        )
    return ValidatedResult(
        record=record,
        url_status="ok",
        url_http_status=200,
        archive_url=archive_url,
        validated_at="2026-06-21T00:00:00+00:00",
    )


# ── ST3: Schema Validator ──────────────────────────────────────────────────────

class TestValidateRecord:
    def test_valid_record_no_violations(self):
        rec = _make_output_record()
        assert validate_record(rec) == []

    def test_empty_verbatim_snippet_flagged(self):
        rec = _make_output_record(verbatim_snippet="")
        viols = validate_record(rec)
        assert any("verbatim_snippet" in v for v in viols)

    def test_invalid_indicator_id_flagged(self):
        rec = _make_output_record(indicator_id="P9-I9")
        viols = validate_record(rec)
        assert any("indicator_id" in v for v in viols)

    def test_confidence_out_of_range_flagged(self):
        rec = _make_output_record(confidence=1.5)
        viols = validate_record(rec)
        assert any("confidence" in v for v in viols)

    def test_invalid_discovery_tag_flagged(self):
        rec = _make_output_record(discovery_tag="MAYBE")
        viols = validate_record(rec)
        assert any("discovery_tag" in v for v in viols)

    def test_long_mapping_rationale_flagged(self):
        rec = _make_output_record(mapping_rationale="x" * 301)
        viols = validate_record(rec)
        assert any("mapping_rationale" in v for v in viols)

    def test_new_discovery_tag_valid(self):
        rec = _make_output_record(discovery_tag="NEW")
        assert validate_record(rec) == []

    def test_all_pillar6_indicator_ids_valid(self):
        for i in range(1, 6):
            rec = _make_output_record(indicator_id=f"P6-I{i}")
            assert validate_record(rec) == []

    def test_confidence_none_is_valid(self):
        rec = _make_output_record(confidence=None)
        assert validate_record(rec) == []


# ── AC1: CSV Column Order ──────────────────────────────────────────────────────

class TestWriteCSV:
    def test_csv_columns_match_template_exactly(self, tmp_path):
        """AC1: CSV column order and names match OUTPUT_TEMPLATE_31MAY.xlsx."""
        records = [_make_output_record()]
        csv_path = write_csv(records, tmp_path / "out.csv")

        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            assert list(reader.fieldnames) == CSV_COLUMNS

    def test_csv_column_names_exact(self):
        """Verify expected column names at module level."""
        assert CSV_COLUMNS == [
            "economy", "law_name", "law_number_ref", "last_amended",
            "indicator_id", "article", "discovery_tag", "location_reference",
            "verbatim_snippet", "mapping_rationale", "source_url", "confidence", "notes",
        ]

    def test_csv_utf8_bom_encoding(self, tmp_path):
        records = [_make_output_record()]
        path = write_csv(records, tmp_path / "out.csv")
        raw = path.read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf"  # UTF-8 BOM

    def test_csv_confidence_formatted_two_decimal(self, tmp_path):
        records = [_make_output_record(confidence=0.9)]
        path = write_csv(records, tmp_path / "out.csv")
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["confidence"] == "0.90"

    def test_csv_none_fields_become_empty_string(self, tmp_path):
        records = [_make_output_record(law_number_ref=None, notes=None)]
        path = write_csv(records, tmp_path / "out.csv")
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["law_number_ref"] == ""
        assert rows[0]["notes"] == ""

    def test_csv_multiple_records(self, tmp_path):
        records = [
            _make_output_record(indicator_id="P6-I1"),
            _make_output_record(indicator_id="P7-I2"),
        ]
        path = write_csv(records, tmp_path / "out.csv")
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2

    def test_csv_empty_records_writes_header_only(self, tmp_path):
        path = write_csv([], tmp_path / "out.csv")
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            assert list(reader.fieldnames) == CSV_COLUMNS
            assert list(reader) == []

    def test_csv_creates_parent_directory(self, tmp_path):
        path = write_csv([_make_output_record()], tmp_path / "subdir" / "out.csv")
        assert path.exists()


# ── AC2: JSON Extended Fields ──────────────────────────────────────────────────

class TestWriteJSON:
    _EXTENDED_FIELDS = {
        "ocr_quality_cer",
        "processing_time_seconds",
        "model_version",
        "raw_context_before",
        "raw_context_after",
        "verbatim_original",
        "archive_url",
    }

    def test_json_extended_fields_present(self, tmp_path):
        """AC2: JSON output includes all 6 extended metadata fields."""
        records = [_make_output_record()]
        path = write_json(records, tmp_path / "out.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        first_rec = data["documents"][0]["records"][0]
        for field in self._EXTENDED_FIELDS:
            assert field in first_rec, f"Missing extended field: {field}"

    def test_json_extended_fields_populated(self, tmp_path):
        """AC2: Extended fields are real values, not None/empty where expected."""
        records = [_make_output_record(
            ocr_quality_cer=0.02,
            processing_time_seconds=12.5,
            model_version="claude-sonnet-4-20250514",
            raw_context_before="23. Accuracy.",
            raw_context_after="25. Retention.",
            archive_url="https://web.archive.org/web/20240101/https://example.com",
        )]
        path = write_json(records, tmp_path / "out.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        rec = data["documents"][0]["records"][0]
        assert rec["ocr_quality_cer"] == pytest.approx(0.02)
        assert rec["processing_time_seconds"] == pytest.approx(12.5)
        assert rec["model_version"] == "claude-sonnet-4-20250514"
        assert rec["raw_context_before"] == "23. Accuracy."
        assert "web.archive.org" in rec["archive_url"]

    def test_json_per_document_grouping(self, tmp_path):
        records = [
            _make_output_record(indicator_id="P6-I1", source_url="https://url1.com"),
            _make_output_record(indicator_id="P7-I1", source_url="https://url1.com"),
            _make_output_record(indicator_id="P6-I2", source_url="https://url2.com"),
        ]
        path = write_json(records, tmp_path / "out.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["total_records"] == 3
        assert len(data["documents"]) == 2
        url1_doc = next(d for d in data["documents"] if d["source_url"] == "https://url1.com")
        assert url1_doc["record_count"] == 2

    def test_json_envelope_has_metadata(self, tmp_path):
        records = [_make_output_record()]
        path = write_json(records, tmp_path / "out.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "generated_at" in data
        assert "total_records" in data
        assert data["total_records"] == 1

    def test_json_confidence_is_numeric(self, tmp_path):
        records = [_make_output_record(confidence=0.92)]
        path = write_json(records, tmp_path / "out.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        rec = data["documents"][0]["records"][0]
        assert isinstance(rec["confidence"], float)


# ── AC3: Cost Logger ───────────────────────────────────────────────────────────

class TestCostLogger:
    def test_compute_llm_cost_anthropic(self):
        """AC3: Measured $ costs per component are non-zero for real usage."""
        cost = compute_llm_cost("anthropic", input_tokens=1000, output_tokens=200)
        assert cost > 0

    def test_compute_llm_cost_ollama_is_zero(self):
        cost = compute_llm_cost("ollama", input_tokens=1000, output_tokens=200)
        assert cost == 0.0

    def test_record_llm_call_accumulates(self):
        cl = CostLogger(economy="Singapore", pillar=7, pdf_path="test.pdf")
        cl.record_llm_call(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            input_tokens=500,
            output_tokens=100,
            latency_ms=1200.0,
        )
        report = cl.to_report()
        assert report["components"]["llm"]["input_tokens"] == 500
        assert report["components"]["llm"]["output_tokens"] == 100
        assert report["components"]["llm"]["cost_usd"] > 0
        assert report["components"]["llm"]["calls"] == 1

    def test_record_ocr_tesseract_is_free(self):
        cl = CostLogger(economy="Singapore", pillar=7, pdf_path="test.pdf")
        cl.record_ocr_page(engine="tesseract", pages=50)
        report = cl.to_report()
        assert report["components"]["ocr"]["cost_usd"] == 0.0
        assert report["components"]["ocr"]["pages_processed"] == 50

    def test_record_ocr_azure_has_cost(self):
        cl = CostLogger(economy="Singapore", pillar=7, pdf_path="test.pdf")
        cl.record_ocr_page(engine="azure", pages=10)
        report = cl.to_report()
        assert report["components"]["ocr"]["cost_usd"] > 0

    def test_record_embedding_tokens(self):
        cl = CostLogger(economy="Singapore", pillar=7, pdf_path="test.pdf")
        cl.record_embedding(tokens=5000, latency_ms=300.0)
        report = cl.to_report()
        assert report["components"]["embedding"]["input_tokens"] == 5000
        assert report["components"]["embedding"]["cost_usd"] == 0.0  # local model

    def test_record_crawl(self):
        cl = CostLogger(economy="Singapore", pillar=7, pdf_path="test.pdf")
        cl.record_crawl(pages_fetched=3, latency_ms=2000.0)
        report = cl.to_report()
        assert report["components"]["crawling"]["pages_fetched"] == 3
        assert report["components"]["crawling"]["cost_usd"] == 0.0

    def test_total_cost_sums_components(self):
        cl = CostLogger(economy="Singapore", pillar=7, pdf_path="test.pdf")
        cl.record_llm_call("anthropic", "model", 1000, 200, 1000.0)
        cl.record_ocr_page("azure", pages=5)
        report = cl.to_report()
        expected = (
            report["components"]["llm"]["cost_usd"]
            + report["components"]["ocr"]["cost_usd"]
        )
        assert report["total_cost_usd"] == pytest.approx(expected, rel=1e-6)

    def test_multiple_llm_calls_accumulate(self):
        cl = CostLogger(economy="Singapore", pillar=7, pdf_path="test.pdf")
        cl.record_llm_call("anthropic", "model", 500, 100, 800.0)
        cl.record_llm_call("anthropic", "model", 500, 100, 800.0)
        report = cl.to_report()
        assert report["components"]["llm"]["calls"] == 2
        assert report["components"]["llm"]["input_tokens"] == 1000

    def test_save_writes_cost_report_json(self, tmp_path):
        """AC3: Running cost_logger produces populated cost_report.json."""
        cl = CostLogger(economy="Singapore", pillar=6, pdf_path="benchmark_50pages.pdf")
        cl.record_llm_call("anthropic", "claude-sonnet-4-20250514", 2000, 400, 3000.0)
        cl.record_ocr_page("tesseract", pages=50)
        cl.record_embedding(tokens=8000)
        cl.record_crawl(pages_fetched=1)

        path = cl.save(log_dir=tmp_path)
        assert path.exists()

        report = json.loads(path.read_text())
        assert report["economy"] == "Singapore"
        assert report["pillar"] == 6
        assert report["components"]["llm"]["input_tokens"] == 2000
        assert report["components"]["llm"]["cost_usd"] > 0
        assert report["total_cost_usd"] > 0
        assert report["processing_time_seconds"] >= 0

    def test_report_has_all_required_keys(self, tmp_path):
        cl = CostLogger(economy="Singapore", pillar=7, pdf_path="test.pdf")
        report = cl.to_report()
        for key in ("generated_at", "document", "economy", "pillar", "model_version",
                    "processing_time_seconds", "total_cost_usd", "components"):
            assert key in report
        for component in ("llm", "ocr", "embedding", "crawling"):
            assert component in report["components"]

    def test_elapsed_seconds_increases(self):
        cl = CostLogger(economy="Singapore", pillar=7, pdf_path="test.pdf")
        t1 = cl.elapsed_seconds()
        time.sleep(0.01)
        t2 = cl.elapsed_seconds()
        assert t2 > t1


# ── ST4: Write Orchestrator ────────────────────────────────────────────────────

class TestWriteOutputs:
    def test_write_outputs_creates_both_files(self, tmp_path):
        records = [_make_output_record()]
        result = write_outputs(records, tmp_path, "Singapore", 7)
        assert Path(result["csv_path"]).exists()
        assert Path(result["json_path"]).exists()
        assert result["written"] == 1
        assert result["skipped"] == 0

    def test_write_outputs_skip_invalid(self, tmp_path):
        valid = _make_output_record()
        invalid = _make_output_record(indicator_id="INVALID")
        result = write_outputs([valid, invalid], tmp_path, "Singapore", 7, skip_invalid=True)
        assert result["written"] == 1
        assert result["skipped"] == 1

    def test_write_outputs_raises_on_invalid(self, tmp_path):
        invalid = _make_output_record(indicator_id="INVALID")
        with pytest.raises(OutputSchemaError):
            write_outputs([invalid], tmp_path, "Singapore", 7, skip_invalid=False)

    def test_write_outputs_filename_pattern(self, tmp_path):
        records = [_make_output_record()]
        result = write_outputs(records, tmp_path, "Singapore", 6)
        assert "singapore_pillar6" in result["csv_path"]
        assert "singapore_pillar6" in result["json_path"]


# ── build_output_record helper ─────────────────────────────────────────────────

class TestBuildOutputRecord:
    def test_build_from_validated_result(self):
        vr = _make_validated_result(archive_url="https://web.archive.org/test")
        rec = build_output_record(vr, ocr_quality_cer=0.03, processing_time_seconds=8.0)
        assert rec.economy == "Singapore"
        assert rec.indicator_id == "P7-I1"
        assert rec.archive_url == "https://web.archive.org/test"
        assert rec.ocr_quality_cer == pytest.approx(0.03)
        assert rec.processing_time_seconds == pytest.approx(8.0)
        assert rec.model_version == "claude-sonnet-4-20250514"

    def test_build_preserves_context_fields(self):
        vr = _make_validated_result()
        rec = build_output_record(vr)
        assert rec.raw_context_before == "23. Accuracy Obligation."
        assert rec.raw_context_after == "25. Retention Limitation Obligation."


# ── as_csv_row / as_json_dict ──────────────────────────────────────────────────

class TestOutputRecordMethods:
    def test_as_csv_row_column_order(self):
        rec = _make_output_record()
        row = rec.as_csv_row()
        assert list(row.keys()) == CSV_COLUMNS

    def test_as_json_dict_includes_extended_fields(self):
        rec = _make_output_record()
        d = rec.as_json_dict()
        for field in ("ocr_quality_cer", "processing_time_seconds", "model_version",
                      "raw_context_before", "raw_context_after", "verbatim_original",
                      "archive_url"):
            assert field in d


# ── Error path coverage ────────────────────────────────────────────────────────

class TestErrorPaths:
    def test_write_outputs_no_valid_records_returns_zeros(self, tmp_path):
        """write_outputs with all invalid records (skip_invalid=True) → written=0."""
        bad = _make_output_record(indicator_id="BAD")
        result = write_outputs([bad], tmp_path, "Singapore", 7, skip_invalid=True)
        assert result["written"] == 0
        assert result["skipped"] == 1
        assert result["csv_path"] is None

    def test_write_outputs_many_violations_in_summary(self, tmp_path, capsys):
        """Console summary shows first 5 violations + 'and N more' line."""
        records = [_make_output_record(indicator_id="BAD")] * 7
        write_outputs(records, tmp_path, "Singapore", 7, skip_invalid=True)
        captured = capsys.readouterr()
        assert "more" in captured.out

    def test_build_output_record_no_optional_args(self):
        from src.output.validator import ValidatedResult
        from src.mapping.models import ExtractionResult
        er = ExtractionResult(
            economy="Singapore", law_name="PDPA", law_number_ref=None,
            last_amended="2021", indicator_id="P6-I1", article="S.26",
            discovery_tag="KNOWN", location_reference=None,
            verbatim_snippet="text", mapping_rationale=None,
            source_url="https://example.com", confidence=0.9, notes=None,
            provider_used="anthropic", model_used="claude-sonnet-4-20250514",
            source_chunk_id="c1", raw_context_before="", raw_context_after="",
            verbatim_original=None,
        )
        vr = ValidatedResult(
            record=er, url_status="ok", url_http_status=200,
            archive_url="", validated_at="2026-06-21T00:00:00+00:00",
        )
        rec = build_output_record(vr)
        assert rec.ocr_quality_cer is None
        assert rec.processing_time_seconds is None
        assert rec.archive_url == ""
