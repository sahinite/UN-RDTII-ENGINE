"""
Unit tests for Z2-5: Validate + Archive + OCR Stage 2 + Confidence Flagging.

Acceptance criteria verified:
  AC1 — Low-quality scanned input triggers Stage-2 OCR fallback automatically
        and the fallback is logged (engine used + resulting CER).
  AC2 — Any output row with confidence < 0.80 carries the exact standard
        review note in its notes field.
  AC3 — Every source_url in the final output resolves with HTTP 200,
        or is explicitly flagged as broken.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest
import respx
import httpx

# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_record(
    source_url: str = "https://sso.agc.gov.sg/Act/PDPA",
    confidence: Optional[float] = 0.90,
    notes: Optional[str] = None,
    indicator_id: str = "P7-I1",
    economy: str = "SG",
) -> "ExtractionResult":
    from src.mapping.models import ExtractionResult
    return ExtractionResult(
        economy=economy,
        law_name="Personal Data Protection Act",
        law_number_ref="Act 26 of 2012",
        last_amended="2020",
        indicator_id=indicator_id,
        article="Section 24",
        discovery_tag="KNOWN",
        location_reference="Part V, Section 24",
        verbatim_snippet="An organisation shall protect personal data...",
        mapping_rationale="Direct match to data protection obligation.",
        source_url=source_url,
        confidence=confidence,
        notes=notes,
        provider_used="anthropic",
        model_used="claude-sonnet-4-20250514",
        source_chunk_id="chunk_001",
        raw_context_before="",
        raw_context_after="",
        verbatim_original=None,
    )


def _make_zone1(url: str = "https://sso.agc.gov.sg/Act/PDPA") -> "Zone1Result":
    from src.fetcher.models import Zone1Result
    return Zone1Result(
        url=url,
        economy="SG",
        act_title="Personal Data Protection Act",
        discovery_tag="KNOWN",
        archive_url="",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# ST1 — URL Validator
# ═══════════════════════════════════════════════════════════════════════════════

class TestUrlValidator:

    @respx.mock
    def test_ok_url_returns_ok_status(self):
        respx.get("https://sso.agc.gov.sg/Act/PDPA").mock(
            return_value=httpx.Response(200, text="<html>Personal Data Protection Act</html>")
        )
        from src.output.validator import validate_url
        status, code = validate_url("https://sso.agc.gov.sg/Act/PDPA")
        assert status == "ok"
        assert code == 200

    @respx.mock
    def test_404_returns_broken(self):
        respx.get("https://sso.agc.gov.sg/Act/MISSING").mock(
            return_value=httpx.Response(404, text="Not Found")
        )
        from src.output.validator import validate_url
        status, code = validate_url("https://sso.agc.gov.sg/Act/MISSING")
        assert status == "broken"
        assert code == 404

    @respx.mock
    def test_410_returns_broken(self):
        respx.get("https://example.com/repealed").mock(
            return_value=httpx.Response(410, text="Gone")
        )
        from src.output.validator import validate_url
        status, code = validate_url("https://example.com/repealed")
        assert status == "broken"
        assert code == 410

    @respx.mock
    def test_soft_404_detected_on_http_200(self):
        """HTTP 200 but body contains 'page not found' → soft_404."""
        respx.get("https://sso.agc.gov.sg/Act/GHOST").mock(
            return_value=httpx.Response(
                200, text="<html><h1>Page Not Found</h1><p>The page you requested does not exist.</p></html>"
            )
        )
        from src.output.validator import validate_url
        status, code = validate_url("https://sso.agc.gov.sg/Act/GHOST")
        assert status == "soft_404"
        assert code == 200

    @respx.mock
    def test_error_page_pattern_file_not_found(self):
        respx.get("https://example.com/missing").mock(
            return_value=httpx.Response(200, text="File not found on this server")
        )
        from src.output.validator import validate_url
        status, _ = validate_url("https://example.com/missing")
        assert status == "soft_404"

    @respx.mock
    def test_429_retries_then_succeeds(self):
        """429 on first attempt → retry → 200."""
        call_count = 0

        def rate_limit_then_ok(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, text="<html>OK</html>")

        respx.get("https://sso.agc.gov.sg/Act/PDPA").mock(side_effect=rate_limit_then_ok)

        with patch("src.output.validator._sleep"):
            from src.output.validator import validate_url
            status, code = validate_url("https://sso.agc.gov.sg/Act/PDPA")
        assert status == "ok"
        assert code == 200

    @respx.mock
    def test_5xx_retries_then_error(self):
        respx.get("https://example.com/act").mock(
            return_value=httpx.Response(503, text="Service Unavailable")
        )
        with patch("src.output.validator._sleep"):
            from src.output.validator import validate_url
            status, code = validate_url("https://example.com/act")
        assert status == "error"
        assert code == 503

    def test_network_error_returns_broken(self):
        with patch("httpx.Client.get", side_effect=httpx.ConnectError("Connection refused")):
            from src.output.validator import validate_url
            status, code = validate_url("https://unreachable.invalid/act")
        assert status == "broken"
        assert code is None

    def test_timeout_returns_error(self):
        with patch("httpx.Client.get", side_effect=httpx.TimeoutException("timeout")):
            with patch("src.output.validator._sleep"):
                from src.output.validator import validate_url
                status, code = validate_url("https://slow.example.com/act")
        assert status == "error"


# ═══════════════════════════════════════════════════════════════════════════════
# ST2 — Wayback Machine Archiver
# ═══════════════════════════════════════════════════════════════════════════════

class TestWaybackArchiver:

    @respx.mock
    def test_successful_archive_returns_url(self):
        respx.get("https://web.archive.org/save/https://sso.agc.gov.sg/Act/PDPA").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Location": "/web/20240621120000/https://sso.agc.gov.sg/Act/PDPA"},
                text="",
            )
        )
        from src.output.validator import archive_wayback
        result = archive_wayback("https://sso.agc.gov.sg/Act/PDPA")
        assert "web.archive.org" in result
        assert "sso.agc.gov.sg" in result

    @respx.mock
    def test_wayback_429_retries_with_wait(self):
        call_count = 0

        def rate_limit_then_ok(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, text="Rate limited")
            return httpx.Response(
                200,
                headers={"Content-Location": "/web/20240101/https://example.com/"},
                text="",
            )

        respx.get("https://web.archive.org/save/https://example.com/act").mock(
            side_effect=rate_limit_then_ok
        )
        with patch("src.output.validator._sleep") as mock_sleep:
            from src.output.validator import archive_wayback
            result = archive_wayback("https://example.com/act")
        mock_sleep.assert_called_once()
        assert result != ""

    @respx.mock
    def test_wayback_failure_returns_empty_string(self):
        respx.get("https://web.archive.org/save/https://example.com/act").mock(
            return_value=httpx.Response(523, text="Service Unavailable")
        )
        from src.output.validator import archive_wayback
        result = archive_wayback("https://example.com/act")
        assert result == ""

    @respx.mock
    def test_wayback_network_error_returns_empty_string(self):
        respx.get("https://web.archive.org/save/https://example.com/act").mock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        from src.output.validator import archive_wayback
        result = archive_wayback("https://example.com/act")
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════════
# ST3 — Azure Document Intelligence
# ═══════════════════════════════════════════════════════════════════════════════

class TestAzureDI:

    def test_raises_without_credentials(self):
        from src.ocr.processor import run_azure_di
        with patch.dict(os.environ, {"AZURE_DI_KEY": "", "AZURE_DI_ENDPOINT": ""}):
            with pytest.raises(RuntimeError, match="AZURE_DI_KEY"):
                run_azure_di(b"\x89PNG\r\n")

    @respx.mock
    def test_successful_azure_di_extraction(self):
        endpoint = "https://my-resource.cognitiveservices.azure.com"
        analyze_url = (
            f"{endpoint}/documentintelligence/documentModels/prebuilt-read:analyze"
            "?api-version=2024-11-30"
        )
        operation_url = f"{endpoint}/operations/12345"

        respx.post(analyze_url).mock(
            return_value=httpx.Response(
                202, headers={"Operation-Location": operation_url}, text=""
            )
        )
        respx.get(operation_url).mock(
            return_value=httpx.Response(200, json={
                "status": "succeeded",
                "analyzeResult": {
                    "pages": [{
                        "words": [
                            {"content": "Personal", "confidence": 0.99},
                            {"content": "Data", "confidence": 0.98},
                            {"content": "Protection", "confidence": 0.97},
                        ]
                    }]
                }
            })
        )

        env = {
            "AZURE_DI_KEY": "test-key-123",
            "AZURE_DI_ENDPOINT": endpoint,
        }
        with patch.dict(os.environ, env):
            with patch("src.ocr.processor._sleep"):
                from src.ocr.processor import run_azure_di
                text, cer = run_azure_di(b"fake_image_bytes")

        assert "Personal" in text
        assert "Data" in text
        assert 0.0 <= cer <= 1.0
        # High-confidence words → low CER
        assert cer < 0.05

    @respx.mock
    def test_azure_di_api_error_raises(self):
        endpoint = "https://my-resource.cognitiveservices.azure.com"
        analyze_url = (
            f"{endpoint}/documentintelligence/documentModels/prebuilt-read:analyze"
            "?api-version=2024-11-30"
        )
        respx.post(analyze_url).mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )
        env = {"AZURE_DI_KEY": "bad-key", "AZURE_DI_ENDPOINT": endpoint}
        with patch.dict(os.environ, env):
            from src.ocr.processor import run_azure_di
            with pytest.raises(RuntimeError, match="HTTP 401"):
                run_azure_di(b"fake_image_bytes")


# ═══════════════════════════════════════════════════════════════════════════════
# ST4 — Mistral OCR
# ═══════════════════════════════════════════════════════════════════════════════

class TestMistralOCR:

    def test_raises_without_api_key(self):
        from src.ocr.processor import run_mistral_ocr
        with patch.dict(os.environ, {"MISTRAL_API_KEY": ""}):
            with pytest.raises(RuntimeError, match="MISTRAL_API_KEY"):
                run_mistral_ocr(b"\x89PNG\r\n")

    @respx.mock
    def test_successful_mistral_ocr_extraction(self):
        respx.post("https://api.mistral.ai/v1/ocr").mock(
            return_value=httpx.Response(200, json={
                "pages": [
                    {"markdown": "# Personal Data Protection Act\n\nSection 24: data protection obligation."}
                ]
            })
        )
        with patch.dict(os.environ, {"MISTRAL_API_KEY": "test-key"}):
            from src.ocr.processor import run_mistral_ocr
            text, cer = run_mistral_ocr(b"fake_image_bytes")

        assert "Personal Data Protection Act" in text
        assert 0.0 <= cer <= 1.0

    @respx.mock
    def test_mistral_ocr_api_error_raises(self):
        respx.post("https://api.mistral.ai/v1/ocr").mock(
            return_value=httpx.Response(401, text="Unauthorized")
        )
        with patch.dict(os.environ, {"MISTRAL_API_KEY": "bad-key"}):
            from src.ocr.processor import run_mistral_ocr
            with pytest.raises(RuntimeError, match="HTTP 401"):
                run_mistral_ocr(b"fake_image_bytes")

    @respx.mock
    def test_mistral_ocr_returns_text_field_fallback(self):
        """Response with 'text' key instead of 'pages' is handled."""
        respx.post("https://api.mistral.ai/v1/ocr").mock(
            return_value=httpx.Response(200, json={"text": "Extracted text here"})
        )
        with patch.dict(os.environ, {"MISTRAL_API_KEY": "test-key"}):
            from src.ocr.processor import run_mistral_ocr
            text, cer = run_mistral_ocr(b"fake_image_bytes")
        assert "Extracted text here" in text


# ═══════════════════════════════════════════════════════════════════════════════
# ST5 — OCR Stage 2 Controller  (AC1)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOCRStage2Controller:

    def test_below_threshold_no_stage2(self):
        """CER < 5% → Stage 2 not triggered."""
        from src.ocr.processor import maybe_stage2_fallback
        result = maybe_stage2_fallback(
            cer=0.03, image_bytes=b"img", stage1_text="Good text", stage1_engine="tesseract"
        )
        assert result.stage2_triggered is False
        assert result.engine_used == "tesseract"
        assert result.text == "Good text"

    def test_above_threshold_triggers_stage2_azure(self):
        """CER >= 5% + AZURE_DI_KEY set → Azure DI used. AC1."""
        from src.ocr.processor import maybe_stage2_fallback

        with patch.dict(os.environ, {"AZURE_DI_KEY": "test", "AZURE_DI_ENDPOINT": "https://ep"}):
            with patch("src.ocr.processor.run_azure_di", return_value=("Azure extracted text", 0.02)) as mock_azure:
                result = maybe_stage2_fallback(
                    cer=0.10,
                    image_bytes=b"img",
                    stage1_text="bad ocr",
                    stage1_engine="tesseract",
                )

        assert result.stage2_triggered is True
        assert result.engine_used == "azure_di"
        assert result.text == "Azure extracted text"
        assert result.cer == 0.02
        mock_azure.assert_called_once()

    def test_above_threshold_azure_fails_fallback_mistral(self):
        """Azure DI fails → Mistral OCR used as fallback."""
        from src.ocr.processor import maybe_stage2_fallback

        env = {"AZURE_DI_KEY": "test", "AZURE_DI_ENDPOINT": "https://ep", "MISTRAL_API_KEY": "mkey"}
        with patch.dict(os.environ, env):
            with patch("src.ocr.processor.run_azure_di", side_effect=RuntimeError("Azure failed")):
                with patch("src.ocr.processor.run_mistral_ocr", return_value=("Mistral text", 0.01)):
                    result = maybe_stage2_fallback(
                        cer=0.08,
                        image_bytes=b"img",
                        stage1_text="bad",
                        stage1_engine="paddleocr",
                    )

        assert result.stage2_triggered is True
        assert result.engine_used == "mistral_ocr"
        assert result.text == "Mistral text"

    def test_all_providers_fail_returns_stage1_text(self):
        """All Stage 2 providers fail → Stage 1 text returned with flag."""
        from src.ocr.processor import maybe_stage2_fallback

        with patch.dict(os.environ, {"AZURE_DI_KEY": "", "MISTRAL_API_KEY": ""}):
            result = maybe_stage2_fallback(
                cer=0.12,
                image_bytes=b"img",
                stage1_text="fallback text",
                stage1_engine="tesseract",
            )

        assert result.stage2_triggered is True
        assert result.text == "fallback text"
        assert result.engine_used == "tesseract"

    def test_stage2_logged_on_trigger(self, caplog):
        """Stage 2 trigger is logged with engine + resulting CER. AC1."""
        import logging
        from src.ocr.processor import maybe_stage2_fallback

        with patch.dict(os.environ, {"AZURE_DI_KEY": "", "MISTRAL_API_KEY": "mkey"}):
            with patch("src.ocr.processor.run_mistral_ocr", return_value=("text", 0.02)):
                with caplog.at_level(logging.INFO, logger="ocr_stage2"):
                    result = maybe_stage2_fallback(
                        cer=0.08,
                        image_bytes=b"img",
                        stage1_text="bad",
                        stage1_engine="tesseract",
                    )

        assert result.stage2_triggered is True
        # Structured log entries are dicts — check raw log messages
        log_events = [r.getMessage() for r in caplog.records]
        assert any("ocr_stage2_triggered" in m for m in log_events)

    def test_cer_estimate_empty_text(self):
        from src.ocr.processor import _estimate_cer_from_text
        assert _estimate_cer_from_text("") == 1.0

    def test_cer_estimate_clean_text(self):
        from src.ocr.processor import _estimate_cer_from_text
        cer = _estimate_cer_from_text("Personal Data Protection Act 2012")
        assert cer == 0.0  # no garbage chars

    def test_cer_estimate_garbage_text(self):
        from src.ocr.processor import _estimate_cer_from_text
        # Mix of printable + 5 null bytes
        cer = _estimate_cer_from_text("abc\x00\x00\x00\x00\x00")
        assert cer > 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# ST6 — Confidence Flagging (AC2)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfidenceFlagging:

    def test_low_confidence_appends_review_note(self):
        """confidence < 0.80 → exact REVIEW_NOTE in notes. AC2."""
        from src.output.validator import REVIEW_NOTE, _flag_confidence
        record = _make_record(confidence=0.75)
        _flag_confidence(record)
        assert REVIEW_NOTE in record.notes

    def test_exact_note_text(self):
        """Exact note text matches acceptance criteria."""
        from src.output.validator import REVIEW_NOTE, _flag_confidence
        record = _make_record(confidence=0.50)
        _flag_confidence(record)
        assert record.notes == REVIEW_NOTE

    def test_confidence_at_threshold_not_flagged(self):
        """confidence == 0.80 → no note."""
        from src.output.validator import REVIEW_NOTE, _flag_confidence
        record = _make_record(confidence=0.80)
        _flag_confidence(record)
        assert record.notes is None or REVIEW_NOTE not in (record.notes or "")

    def test_confidence_above_threshold_not_flagged(self):
        from src.output.validator import REVIEW_NOTE, _flag_confidence
        record = _make_record(confidence=0.95)
        _flag_confidence(record)
        assert record.notes is None or REVIEW_NOTE not in (record.notes or "")

    def test_none_confidence_not_flagged(self):
        from src.output.validator import REVIEW_NOTE, _flag_confidence
        record = _make_record(confidence=None)
        _flag_confidence(record)
        assert REVIEW_NOTE not in (record.notes or "")

    def test_existing_notes_preserved(self):
        """Existing notes are preserved; REVIEW_NOTE appended."""
        from src.output.validator import REVIEW_NOTE, _flag_confidence
        record = _make_record(confidence=0.60, notes="See article 5.")
        _flag_confidence(record)
        assert "See article 5." in record.notes
        assert REVIEW_NOTE in record.notes

    def test_duplicate_note_not_added_twice(self):
        """If REVIEW_NOTE already in notes, it is not duplicated."""
        from src.output.validator import REVIEW_NOTE, _flag_confidence
        record = _make_record(confidence=0.60, notes=REVIEW_NOTE)
        _flag_confidence(record)
        assert record.notes.count(REVIEW_NOTE) == 1

    def test_zero_confidence_flagged(self):
        from src.output.validator import REVIEW_NOTE, _flag_confidence
        record = _make_record(confidence=0.0)
        _flag_confidence(record)
        assert REVIEW_NOTE in record.notes


# ═══════════════════════════════════════════════════════════════════════════════
# Full Validation Orchestrator (AC2 + AC3)
# ═══════════════════════════════════════════════════════════════════════════════

class TestValidateAndFlag:

    @respx.mock
    def test_ok_url_validated_and_archived(self):
        """AC3: source_url resolves → ok status; AC2: high confidence → no note."""
        respx.get("https://sso.agc.gov.sg/Act/PDPA").mock(
            return_value=httpx.Response(200, text="<html>PDPA</html>")
        )
        respx.get("https://web.archive.org/save/https://sso.agc.gov.sg/Act/PDPA").mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Location": "/web/20240101/https://sso.agc.gov.sg/Act/PDPA"},
                text="",
            )
        )
        from src.output.validator import REVIEW_NOTE, validate_and_flag

        record = _make_record(confidence=0.92)
        with patch("src.output.validator._sleep"):
            results = validate_and_flag([record])

        assert len(results) == 1
        r = results[0]
        assert r.url_status == "ok"
        assert r.url_http_status == 200
        assert "web.archive.org" in r.archive_url
        assert REVIEW_NOTE not in (record.notes or "")

    @respx.mock
    def test_broken_url_flagged_in_notes(self):
        """AC3: HTTP 404 → broken status + broken-URL note appended."""
        respx.get("https://sso.agc.gov.sg/Act/MISSING").mock(
            return_value=httpx.Response(404, text="Not Found")
        )
        from src.output.validator import validate_and_flag

        record = _make_record(source_url="https://sso.agc.gov.sg/Act/MISSING", confidence=0.90)
        with patch("src.output.validator._sleep"):
            results = validate_and_flag([record], archive=False)

        assert results[0].url_status == "broken"
        assert "BROKEN URL" in record.notes

    @respx.mock
    def test_low_confidence_gets_review_note(self):
        """AC2: confidence < 0.80 → REVIEW_NOTE in notes."""
        respx.get("https://sso.agc.gov.sg/Act/PDPA").mock(
            return_value=httpx.Response(200, text="<html>OK</html>")
        )
        from src.output.validator import REVIEW_NOTE, validate_and_flag

        record = _make_record(confidence=0.72)
        with patch("src.output.validator._sleep"):
            results = validate_and_flag([record], archive=False)

        assert REVIEW_NOTE in results[0].record.notes

    @respx.mock
    def test_multiple_records_processed(self):
        respx.get("https://sso.agc.gov.sg/Act/PDPA").mock(
            return_value=httpx.Response(200, text="<html>OK</html>")
        )
        respx.get("https://sso.agc.gov.sg/Act/ETA").mock(
            return_value=httpx.Response(200, text="<html>OK</html>")
        )
        from src.output.validator import validate_and_flag

        records = [
            _make_record(source_url="https://sso.agc.gov.sg/Act/PDPA", confidence=0.95),
            _make_record(source_url="https://sso.agc.gov.sg/Act/ETA", confidence=0.60),
        ]
        with patch("src.output.validator._sleep"):
            results = validate_and_flag(records, archive=False)

        assert len(results) == 2
        assert results[0].url_status == "ok"
        assert results[1].url_status == "ok"

    @respx.mock
    def test_soft_404_treated_as_broken_for_archive_skip(self):
        """Soft-404 URL is not archived (only 'ok'/'redirected' are archived)."""
        respx.get("https://example.com/gone").mock(
            return_value=httpx.Response(200, text="<html>Page Not Found</html>")
        )
        from src.output.validator import validate_and_flag

        record = _make_record(source_url="https://example.com/gone", confidence=0.90)
        with patch("src.output.validator._sleep"):
            with patch("src.output.validator.archive_wayback") as mock_archive:
                results = validate_and_flag([record], archive=True)

        mock_archive.assert_not_called()
        assert results[0].archive_url == ""

    def test_no_archive_skipped_when_archive_false(self):
        with patch("src.output.validator.validate_url", return_value=("ok", 200)):
            with patch("src.output.validator.archive_wayback") as mock_archive:
                from src.output.validator import validate_and_flag
                record = _make_record(confidence=0.95)
                validate_and_flag([record], archive=False)
        mock_archive.assert_not_called()

    def test_validated_at_is_iso_format(self):
        with patch("src.output.validator.validate_url", return_value=("ok", 200)):
            from src.output.validator import validate_and_flag
            record = _make_record(confidence=0.95)
            results = validate_and_flag([record], archive=False)
        assert "T" in results[0].validated_at  # ISO 8601


# ═══════════════════════════════════════════════════════════════════════════════
# Router integration: Stage 2 wired into _try_ocr (AC1)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRouterStage2Integration:

    def test_ocr_quality_error_triggers_stage2(self, sg_economy):
        """OCRQualityError from Stage 1 → run_ocr_stage2 called automatically. AC1."""
        from src.fetcher.extractors.ocr_stage1 import OCRQualityError
        from src.fetcher.models import Zone1Result
        from src.fetcher.router import _try_ocr

        zone1 = _make_zone1()
        fake_doc = MagicMock()
        fake_doc.raw_text = "Stage 2 extracted text"

        with patch(
            "src.fetcher.router.extract_ocr_stage1",
            side_effect=OCRQualityError(cer=0.12, engine_used="tesseract"),
        ):
            with patch("src.ocr.processor.run_ocr_stage2", return_value=fake_doc) as mock_s2:
                result = _try_ocr(b"fake_pdf_bytes", zone1, sg_economy)

        mock_s2.assert_called_once()
        call_kwargs = mock_s2.call_args
        assert call_kwargs.kwargs["stage1_cer"] == 0.12
        assert call_kwargs.kwargs["stage1_engine"] == "tesseract"
        assert result is fake_doc

    def test_good_ocr_does_not_trigger_stage2(self, sg_economy):
        """CER below threshold → Stage 1 result returned, Stage 2 never called."""
        from src.fetcher.models import Zone1Result
        from src.fetcher.router import _try_ocr

        zone1 = _make_zone1()
        fake_doc = MagicMock()

        with patch("src.fetcher.router.extract_ocr_stage1", return_value=fake_doc):
            with patch("src.ocr.processor.run_ocr_stage2") as mock_s2:
                result = _try_ocr(b"fake_pdf_bytes", zone1, sg_economy)

        mock_s2.assert_not_called()
        assert result is fake_doc


# ═══════════════════════════════════════════════════════════════════════════════
# run_ocr_stage2 full document pipeline (AC1 - Stage 2 fallback logged)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunOCRStage2:

    def test_run_ocr_stage2_with_mistral_success(self, sg_economy):
        """Full Stage 2 document pipeline uses Mistral when Azure not configured."""
        from src.ocr.processor import run_ocr_stage2

        zone1 = _make_zone1()

        env = {"AZURE_DI_KEY": "", "MISTRAL_API_KEY": "mkey"}
        fake_images = [b"img1", b"img2"]

        with patch.dict(os.environ, env):
            with patch("src.ocr.processor.pdf_to_images", return_value=fake_images):
                with patch("src.ocr.processor.run_mistral_ocr", return_value=("page text", 0.02)):
                    doc = run_ocr_stage2(
                        raw_bytes=b"%PDFfake",
                        zone1_result=zone1,
                        economy_config=sg_economy,
                        stage1_cer=0.12,
                        stage1_engine="tesseract",
                    )

        assert doc.raw_text.strip() != ""
        assert doc.cer_score < 0.05
        assert doc.extraction_method == "mistral_ocr"
        assert doc.economy == "SG"

    def test_run_ocr_stage2_all_providers_fail_uses_stage1_text(self, sg_economy):
        """All Stage 2 providers fail → stage1_text used, flagged for review."""
        from src.ocr.processor import run_ocr_stage2

        zone1 = _make_zone1()
        env = {"AZURE_DI_KEY": "", "MISTRAL_API_KEY": ""}

        with patch.dict(os.environ, env):
            with patch("src.ocr.processor.pdf_to_images", return_value=[b"img1"]):
                doc = run_ocr_stage2(
                    raw_bytes=b"%PDFfake",
                    zone1_result=zone1,
                    economy_config=sg_economy,
                    stage1_cer=0.15,
                    stage1_engine="tesseract",
                    stage1_text="stage1 fallback text",
                )

        assert "stage1 fallback text" in doc.raw_text
        assert doc.flag_for_review is True

    def test_run_ocr_stage2_with_azure_success(self, sg_economy):
        """Azure DI used when AZURE_DI_KEY is set."""
        from src.ocr.processor import run_ocr_stage2

        zone1 = _make_zone1()
        env = {"AZURE_DI_KEY": "akey", "AZURE_DI_ENDPOINT": "https://ep"}

        with patch.dict(os.environ, env):
            with patch("src.ocr.processor.pdf_to_images", return_value=[b"img1"]):
                with patch("src.ocr.processor.run_azure_di", return_value=("azure text", 0.01)):
                    doc = run_ocr_stage2(
                        raw_bytes=b"%PDFfake",
                        zone1_result=zone1,
                        economy_config=sg_economy,
                        stage1_cer=0.10,
                        stage1_engine="tesseract",
                    )

        assert "azure text" in doc.raw_text
        assert doc.extraction_method == "azure_di"
        assert doc.flag_for_review is False

    def test_run_ocr_stage2_image_input(self, sg_economy):
        """Non-PDF raw bytes (image) → single image, no pdf_to_images call."""
        from src.ocr.processor import run_ocr_stage2

        zone1 = _make_zone1()
        env = {"AZURE_DI_KEY": "", "MISTRAL_API_KEY": "mkey"}

        with patch.dict(os.environ, env):
            with patch("src.ocr.processor.run_mistral_ocr", return_value=("image ocr text", 0.01)):
                # Non-PDF bytes (no %PDF header)
                doc = run_ocr_stage2(
                    raw_bytes=b"\x89PNG\r\n\x1a\n",  # PNG header
                    zone1_result=zone1,
                    economy_config=sg_economy,
                    stage1_cer=0.08,
                    stage1_engine="tesseract",
                )

        assert "image ocr text" in doc.raw_text
        assert doc.doc_type == "IMAGE"
        assert doc.page_count == 1

    def test_run_ocr_stage2_empty_result_uses_placeholder(self, sg_economy):
        """If all pages return empty text, stage1_text placeholder used."""
        from src.ocr.processor import run_ocr_stage2

        zone1 = _make_zone1()
        env = {"AZURE_DI_KEY": "", "MISTRAL_API_KEY": "mkey"}

        with patch.dict(os.environ, env):
            with patch("src.ocr.processor.pdf_to_images", return_value=[b"img1"]):
                with patch("src.ocr.processor.run_mistral_ocr", return_value=("", 1.0)):
                    doc = run_ocr_stage2(
                        raw_bytes=b"%PDFfake",
                        zone1_result=zone1,
                        economy_config=sg_economy,
                        stage1_cer=0.20,
                        stage1_engine="tesseract",
                        stage1_text="original stage1",
                    )

        # Either stage1_text or placeholder used — text not empty
        assert doc.raw_text.strip() != ""


# ═══════════════════════════════════════════════════════════════════════════════
# Archive dedupe + local-snapshot fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestArchiveDedupeAndFallback:
    def test_archive_source_falls_back_to_local_when_wayback_fails(self):
        """Wayback returns "" (520/network) → archive_source uses local snapshot."""
        from src.output import validator

        with (
            patch.object(validator, "archive_wayback", return_value=""),
            patch.object(validator, "archive_local", return_value="outputs/archive/Act_PDPA2012.pdf") as mock_local,
        ):
            result = validator.archive_source("https://sso.agc.gov.sg/Act/PDPA2012?ViewType=Pdf")

        assert result == "outputs/archive/Act_PDPA2012.pdf"
        mock_local.assert_called_once()

    def test_archive_source_prefers_wayback_when_available(self):
        """When Wayback succeeds, local snapshot is not taken."""
        from src.output import validator

        with (
            patch.object(validator, "archive_wayback", return_value="https://web.archive.org/web/x"),
            patch.object(validator, "archive_local", side_effect=AssertionError("local must not run")),
        ):
            result = validator.archive_source("https://sso.agc.gov.sg/Act/PDPA2012")

        assert result == "https://web.archive.org/web/x"

    def test_validate_and_flag_archives_each_url_once(self):
        """10 records sharing one source URL → archive_source called once, not 10x."""
        from src.output import validator
        from src.output.validator import validate_and_flag

        records = [_make_record(indicator_id=f"P7-I{i}") for i in range(10)]
        assert len({r.source_url for r in records}) == 1  # all same URL

        with (
            patch.object(validator, "validate_url", return_value=("ok", 200)),
            patch.object(validator, "archive_source", return_value="local/snap.pdf") as mock_arch,
            patch.object(validator, "_sleep"),
        ):
            results = validate_and_flag(records, archive=True, wayback_rate_limit_s=0.0)

        assert len(results) == 10
        mock_arch.assert_called_once()  # deduped by URL
        assert all(r.archive_url == "local/snap.pdf" for r in results)

    def test_archive_local_saves_snapshot(self, tmp_path):
        """archive_local downloads bytes and writes them to the archive dir."""
        from src.output import validator

        def handler(request):
            return httpx.Response(200, content=b"%PDF-1.7 fake", headers={"content-type": "application/pdf"})

        with respx.mock:
            respx.get("https://sso.agc.gov.sg/Act/PDPA2012?ViewType=Pdf").mock(side_effect=handler)
            path = validator.archive_local(
                "https://sso.agc.gov.sg/Act/PDPA2012?ViewType=Pdf", dest_dir=str(tmp_path)
            )

        assert path
        assert path.endswith(".pdf")
        from pathlib import Path as _P
        assert _P(path).read_bytes() == b"%PDF-1.7 fake"
