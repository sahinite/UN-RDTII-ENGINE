"""OCR accuracy on sample scanned PDFs — both cascade stages. [Z2-1, Z2-5]"""
import pytest

def test_stage1_engine_selected_by_economy_script_type():
    pytest.skip("Implement after [Z2-1] — tesseract for sg.yaml, paddleocr for th.yaml")

def test_stage2_fallback_triggers_above_cer_threshold():
    pytest.skip("Implement after [Z2-5] — CER >= 5% triggers Azure/Mistral fallback")
