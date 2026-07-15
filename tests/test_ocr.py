"""OCR accuracy on sample scanned PDFs — both cascade stages.

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
    """get_ocr_engine reads the derived ocr_engine: latin→tesseract (SG/AU/MY)."""
    from src.fetcher.extractors.ocr_stage1 import get_ocr_engine

    assert get_ocr_engine(_economy("latin")) == "tesseract"


def test_cloud_first_ocr_then_local_floor():
    """Router runs cloud OCR first; it only falls to the local Tesseract/Paddle floor
    when the cloud cascade returns None (no key / all cloud tiers failed)."""
    from src.fetcher import router

    zone1 = Zone1Result(
        url="https://example.gov/scan.pdf",
        economy="XX",
        act_title="Scanned Act",
        discovery_tag="KNOWN",
        archive_url="",
    )
    economy = _economy("latin")
    raw = b"%PDF-fake-scanned-bytes"

    # Cloud tier produces a document → returned, local floor never touched.
    cloud_sentinel = object()
    with (
        patch("src.ocr.processor.run_ocr_cloud", return_value=cloud_sentinel) as mock_cloud,
        patch.object(router, "extract_ocr_stage1") as mock_floor,
    ):
        result = router._try_ocr(raw, zone1, economy)

    assert result is cloud_sentinel
    mock_cloud.assert_called_once()
    mock_floor.assert_not_called()

    # No cloud tier available (None) → local floor used with gate_cer=False (advisory CER).
    floor_sentinel = object()
    with (
        patch("src.ocr.processor.run_ocr_cloud", return_value=None),
        patch.object(router, "extract_ocr_stage1", return_value=floor_sentinel) as mock_floor,
    ):
        result = router._try_ocr(raw, zone1, economy)

    assert result is floor_sentinel
    assert mock_floor.call_args.kwargs["gate_cer"] is False


# ── Config-driven tesseract language-pack bootstrap ────────────────────────────

class TestTesseractLangBootstrap:
    """Missing language packs are installed from the economy's declared languages —
    no economy-specific codes in Python (Malay 'msa' comes from the YAML)."""

    def _cfg(self, languages, ocr_engine="tesseract"):
        from unittest.mock import MagicMock
        c = MagicMock()
        c.languages = languages
        c.ocr_engine = ocr_engine
        return c

    def test_required_langs_derived_from_config(self):
        from src.fetcher.extractors.ocr_stage1 import required_tesseract_langs
        assert required_tesseract_langs(self._cfg(["ms", "en"])) == ["eng", "msa"]
        assert required_tesseract_langs(self._cfg(["en"])) == ["eng"]

    def test_noop_when_engine_not_tesseract(self):
        from unittest.mock import patch
        from src.fetcher.extractors import ocr_stage1
        with patch("urllib.request.urlretrieve") as dl:
            ocr_stage1.ensure_tesseract_langs(self._cfg(["ms"], ocr_engine="paddleocr"))
        dl.assert_not_called()

    def test_downloads_only_missing_pack(self, tmp_path):
        from unittest.mock import patch
        from src.fetcher.extractors import ocr_stage1
        (tmp_path / "eng.traineddata").write_bytes(b"x")  # eng present, msa missing
        with (
            patch.object(ocr_stage1, "_find_tessdata_dir", return_value=tmp_path),
            patch("urllib.request.urlretrieve") as dl,
        ):
            ocr_stage1.ensure_tesseract_langs(self._cfg(["ms", "en"]))
        # Only msa downloaded (eng already there)
        assert dl.call_count == 1
        assert "msa.traineddata" in str(dl.call_args[0][1])

    def test_install_failure_is_non_fatal(self, tmp_path):
        from unittest.mock import patch
        from src.fetcher.extractors import ocr_stage1
        with (
            patch.object(ocr_stage1, "_find_tessdata_dir", return_value=tmp_path),
            patch("urllib.request.urlretrieve", side_effect=OSError("network down")),
        ):
            ocr_stage1.ensure_tesseract_langs(self._cfg(["ms"]))  # must not raise
