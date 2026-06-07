"""Schema validation tests — happy path + 4 failure modes. [Z1-1.4]"""
import pytest

def test_load_singapore():
    pytest.skip("Implement once src/config/economy_config.py [Z1-1.1/.2] exists")

def test_load_thailand():
    pytest.skip("Implement once src/config/economy_config.py [Z1-1.1/.2] exists")

def test_missing_required_field_rejected():
    pytest.skip("Negative path — missing economy_name/script_type/portals")

def test_malformed_portal_url_rejected():
    pytest.skip("Negative path — bad URL format")

def test_unsupported_ocr_engine_rejected():
    pytest.skip("Negative path — ocr_engine not in {tesseract, paddleocr, azure, mistral_ocr}")

def test_empty_portals_list_rejected():
    pytest.skip("Negative path — portals: []")
