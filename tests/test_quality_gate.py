from types import SimpleNamespace

import pytest

from src.mapping.exceptions import QualityGateError
from src.mapping.mapper import check_quality_gate


def _result(indicator_id: str, confidence: float | None):
    return SimpleNamespace(indicator_id=indicator_id, confidence=confidence)


def test_quality_gate_passes_for_requested_pillar():
    check_quality_gate("Malaysia", 7, [_result("P7-I1", 0.80)])


def test_quality_gate_rejects_wrong_pillar():
    with pytest.raises(QualityGateError):
        check_quality_gate("Australia", 7, [_result("P6-I1", 0.99)])


def test_quality_gate_rejects_low_confidence():
    with pytest.raises(QualityGateError):
        check_quality_gate("Singapore", 7, [_result("P7-I1", 0.79)])


def test_quality_gate_accepts_custom_threshold():
    check_quality_gate("Singapore", 7, [_result("P7-I1", 0.65)], min_confidence=0.60)
