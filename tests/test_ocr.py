"""OCR accuracy on sample scanned PDFs — both cascade stages. [Z2-1, Z2-5]

Stage 1 engine is selected purely from the economy's derived `ocr_engine`
(latin→tesseract, asian→paddleocr). Stage 2 (Azure DI / Mistral) is escalated by
the router when Stage 1 fails the CER gate (CER ≥ 5%). These are unit tests at
those two seams — no OCR binaries or scanned fixtures required.
"""
from __future__ import annotations

from unittest.mock import patch

from src.config.economy_config import EconomyConfig
from src.fetcher.models import Zone1Result


def _economy(script_type: str, name: str = "Test") -> EconomyConfig:
    return EconomyConfig.model_validate({
        "economy_name": name,
        "iso_code": "XX",
        "un_name": name,
        "script_type": script_type,
        "languages": ["en"],
        "portals": [{"name": "P", "url": "https://example.gov"}],
    })


def test_stage1_engine_selected_by_economy_script_type():
    """get_ocr_engine reads the derived ocr_engine: latin→tesseract, asian→paddleocr."""
    from src.fetcher.extractors.ocr_stage1 import get_ocr_engine

    assert get_ocr_engine(_economy("latin")) == "tesseract"
    assert get_ocr_engine(_economy("asian")) == "paddleocr"


def test_stage2_fallback_triggers_above_cer_threshold():
    """Router escalates to Stage 2 when Stage 1 raises OCRQualityError (CER ≥ 5%),
    and does NOT escalate when Stage 1 succeeds (CER below threshold)."""
    from src.fetcher import router
    from src.fetcher.extractors.ocr_stage1 import OCRQualityError
    from src.ocr.processor import _CER_THRESHOLD

    assert _CER_THRESHOLD == 0.05

    zone1 = Zone1Result(
        url="https://example.gov/scan.pdf",
        economy="XX",
        act_title="Scanned Act",
        discovery_tag="KNOWN",
        archive_url="",
    )
    economy = _economy("asian")
    raw = b"%PDF-fake-scanned-bytes"

    # Stage 1 fails the CER gate (0.08 ≥ 0.05) → Stage 2 must be called with the
    # Stage 1 CER + engine.
    stage2_sentinel = object()
    with (
        patch.object(
            router, "extract_ocr_stage1",
            side_effect=OCRQualityError(cer=0.08, engine_used="paddleocr"),
        ),
        patch("src.ocr.processor.run_ocr_stage2", return_value=stage2_sentinel) as mock_stage2,
    ):
        result = router._try_ocr(raw, zone1, economy)

    assert result is stage2_sentinel, "Stage 2 result must be returned on CER-gate failure"
    assert mock_stage2.call_count == 1
    _, kwargs = mock_stage2.call_args
    assert kwargs["stage1_cer"] == 0.08
    assert kwargs["stage1_engine"] == "paddleocr"

    # Stage 1 succeeds (CER under threshold → no OCRQualityError) → no escalation.
    stage1_sentinel = object()
    with (
        patch.object(router, "extract_ocr_stage1", return_value=stage1_sentinel),
        patch("src.ocr.processor.run_ocr_stage2") as mock_stage2_no,
    ):
        result = router._try_ocr(raw, zone1, economy)

    assert result is stage1_sentinel
    mock_stage2_no.assert_not_called()
