"""
Tests for evaluate() — provision-level NEW/KNOWN scoring. [86ey13cyh Seam 6]
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_csv(rows: list[dict], path: Path) -> Path:
    """Write a minimal engine-output CSV for testing."""
    columns = [
        "economy", "law_name", "law_number_ref", "last_amended", "indicator_id",
        "article", "discovery_tag", "location_reference", "verbatim_snippet",
        "mapping_rationale", "source_url", "confidence", "notes",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            full_row = {c: row.get(c, "") for c in columns}
            writer.writerow(full_row)
    return path


def _patch_sample_kit(ground_truth: dict, provision_keys: set):
    """Return context managers that patch load_sample_kit and _load_known_provision_keys."""
    import evaluate as ev
    from unittest.mock import patch as _patch
    return (
        _patch.object(ev, "load_sample_kit", return_value=ground_truth),
        _patch.object(ev, "_load_known_provision_keys", return_value=provision_keys),
    )


class TestEvaluateNewScore:
    def test_3_new_provisions_gives_score_12(self, tmp_path):
        import evaluate as ev

        rows = [
            {"indicator_id": "P6-I1", "discovery_tag": "NEW", "law_name": "Novel Data Act", "article": "Section 1"},
            {"indicator_id": "P6-I2", "discovery_tag": "NEW", "law_name": "Novel Data Act", "article": "Section 2"},
            {"indicator_id": "P6-I3", "discovery_tag": "NEW", "law_name": "Novel Data Act", "article": "Section 3"},
        ]
        csv_path = _make_csv(rows, tmp_path / "output.csv")
        ground_truth = {"P6-I1": [], "P6-I2": []}

        p1, p2 = _patch_sample_kit(ground_truth, set())
        with p1, p2:
            report = ev.evaluate(tmp_path, "Singapore", csv_path=csv_path)

        assert report["scores"]["new_score"] == 12.0

    def test_only_known_provisions_gives_zero_new_score(self, tmp_path):
        import evaluate as ev

        rows = [
            {"indicator_id": "P7-I1", "discovery_tag": "KNOWN", "law_name": "PDPA", "article": "Section 24"},
        ]
        csv_path = _make_csv(rows, tmp_path / "output.csv")
        ground_truth = {"P7-I1": []}

        p1, p2 = _patch_sample_kit(ground_truth, set())
        with p1, p2:
            report = ev.evaluate(tmp_path, "Singapore", csv_path=csv_path)

        assert report["scores"]["new_score"] == 0.0

    def test_new_score_capped_at_20(self, tmp_path):
        import evaluate as ev

        rows = [
            {"indicator_id": f"P6-I{i}", "discovery_tag": "NEW",
             "law_name": "Novel Act", "article": f"Section {i}"}
            for i in range(1, 8)  # 7 new provisions → 7*4=28, capped at 20
        ]
        csv_path = _make_csv(rows, tmp_path / "output.csv")
        ground_truth = {}

        p1, p2 = _patch_sample_kit(ground_truth, set())
        with p1, p2:
            report = ev.evaluate(tmp_path, "Singapore", csv_path=csv_path)

        assert report["scores"]["new_score"] == 20.0

    def test_known_score_all_indicators_matched(self, tmp_path):
        import evaluate as ev

        indicators = [f"P7-I{i}" for i in range(1, 6)] + [f"P6-I{i}" for i in range(1, 6)]
        rows = [
            {"indicator_id": iid, "discovery_tag": "KNOWN", "law_name": "PDPA", "article": f"Section {i}"}
            for i, iid in enumerate(indicators, start=1)
        ]
        csv_path = _make_csv(rows, tmp_path / "output.csv")
        ground_truth = {iid: [] for iid in indicators}

        p1, p2 = _patch_sample_kit(ground_truth, set())
        with p1, p2:
            report = ev.evaluate(tmp_path, "Singapore", csv_path=csv_path)

        assert report["scores"]["known_score"] == 40.0

    def test_new_tagged_but_in_known_provisions_excluded(self, tmp_path):
        """A 'NEW'-tagged row whose (law_name, article) matches a known provision → not counted."""
        import evaluate as ev

        rows = [
            {"indicator_id": "P6-I1", "discovery_tag": "NEW",
             "law_name": "Personal Data Protection Act 2012", "article": "Section 26"},
        ]
        csv_path = _make_csv(rows, tmp_path / "output.csv")
        ground_truth = {}
        # known provision key matching this provision
        known_keys = {("personal data protection act 2012", "section 26")}

        p1, p2 = _patch_sample_kit(ground_truth, known_keys)
        with p1, p2:
            report = ev.evaluate(tmp_path, "Singapore", csv_path=csv_path)

        assert report["scores"]["new_score"] == 0.0


# ── Grouped report + file output (per-pillar grouping + PDF) ─────────────────────

_DB = Path("data/sample_kit/ESCAP-RDTII-2.1_ Round 1 Database.xlsx")


def _grouped_report_fixture() -> dict:
    pillar = {
        "pillar": 6, "csv_path": "x.csv",
        "matched_known": ["P6-I1", "P6-I2"], "missed_known": ["P6-I3"],
        "new_provisions": [{"indicator_id": "P6-I1", "law_name": "PDPA 2012", "article": "s.26"}],
        "scores": {"known_matched": 2, "known_total": 3, "known_score": 26.7,
                   "new_discovered": 1, "new_score": 4.0, "total_score": 30.7, "max_score": 60.0},
    }
    return {
        "economy": "Singapore",
        "pillars": [pillar],
        "overall": {"pillars_evaluated": [6], "known_matched": 2, "known_total": 3,
                    "new_discovered": 1},
    }


def test_write_report_produces_pdf_only(tmp_path):
    import evaluate as ev
    paths = ev.write_report(_grouped_report_fixture(), tmp_path)
    assert {p.suffix for p in paths} == {".pdf"}
    assert not list(tmp_path.glob("*.json")) and not list(tmp_path.glob("*.txt"))
    for p in paths:
        assert p.exists() and p.stat().st_size > 0


def test_formatted_report_groups_by_pillar(tmp_path):
    import evaluate as ev
    text = ev._format_report(_grouped_report_fixture())
    assert "PILLAR 6" in text
    assert "P6-I3" in text  # missed indicator is itemised


def test_report_does_not_show_scores(tmp_path):
    import evaluate as ev
    report = _grouped_report_fixture()
    text = ev._format_report(report)
    for token in ("/40", "/20", "/60", "/120", "SCORE", "Score"):
        assert token not in text, f"score token {token!r} leaked into report"


@pytest.mark.skipif(not _DB.exists(), reason="Round 1 DB not present")
def test_build_economy_report_has_two_pillar_sections(tmp_path):
    import evaluate as ev
    # Empty output_dir → scores will be 0, but the structure must group P6 + P7.
    r = ev.build_economy_report(Path("data/sample_kit"), "Singapore", None, None, tmp_path)
    assert [pr["pillar"] for pr in r["pillars"]] == [6, 7]
    # P6 (4) + P7 (5) ground-truth indicators; no score fields in overall.
    assert r["overall"]["known_total"] == 9
    assert "total_score" not in r["overall"] and "max_score" not in r["overall"]
