"""
Tests for null/no-barrier assessment emission (_emit_null_assessments).

RDTII scores every indicator (0/0.5/1). For an indicator the Round 1 DB assessed
but where the engine found no qualifying provision, we emit an explicit
"no barrier (score 0)" record so the indicator is documented, not silently
absent. The records must pass output validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from main import _emit_null_assessments
from src.config.economy_config import load_economy
from src.crawler.seed_loader import load_seed_data
from src.output.writer import write_outputs

_ROUND1_DB = Path("data/database/ESCAP-RDTII-2.1_ Round 1 Database.xlsx")

pytestmark = pytest.mark.skipif(not _ROUND1_DB.exists(), reason="Round 1 DB not present")


class _Rec:
    def __init__(self, indicator_id: str):
        self.indicator_id = indicator_id


@pytest.fixture
def sg_p6_seed():
    return load_seed_data("SGP", "P6", round1_db_path=str(_ROUND1_DB))


@pytest.fixture
def sg_config():
    return load_economy("Singapore")


def test_emits_null_for_assessed_but_unmatched_indicator(sg_p6_seed, sg_config):
    # SG P6 assessed 6.1/6.2/6.3/6.4; we "found" all but 6.3.
    found = [_Rec("P6-I1"), _Rec("P6-I2"), _Rec("P6-I4")]
    nulls = _emit_null_assessments(found, sg_p6_seed, "Singapore", sg_config)
    ids = {n.indicator_id for n in nulls}
    assert "P6-I3" in ids, "score-0 indicator P6-I3 should get a null record"


def test_does_not_emit_for_matched_indicators(sg_p6_seed, sg_config):
    # Everything found → no nulls.
    found = [_Rec(f"P6-I{i}") for i in range(1, 6)]
    nulls = _emit_null_assessments(found, sg_p6_seed, "Singapore", sg_config)
    assert nulls == []


def test_null_record_carries_review_note_and_canonical_economy(sg_p6_seed, sg_config):
    nulls = _emit_null_assessments([], sg_p6_seed, "Singapore", sg_config)
    assert nulls, "expected null records for unmatched assessed indicators"
    for n in nulls:
        assert n.economy == "Singapore"
        assert "review" in (n.notes or "").lower()
        assert n.law_name.strip()           # non-empty (required field)
        assert n.verbatim_snippet.strip()   # non-empty (required field)


def test_null_records_pass_output_validation(sg_p6_seed, sg_config, tmp_path):
    nulls = _emit_null_assessments([_Rec("P6-I1")], sg_p6_seed, "Singapore", sg_config)
    summary = write_outputs(records=nulls, output_dir=str(tmp_path),
                            economy="Singapore", pillar=6, skip_invalid=True)
    assert summary.get("written", 0) == len(nulls)
    assert summary.get("violations_total", 0) == 0


def test_no_seed_mapping_returns_empty(sg_config):
    class _EmptySeed:
        known_titles_by_indicator: dict = {}
    nulls = _emit_null_assessments([], _EmptySeed(), "Singapore", sg_config)
    assert nulls == []
