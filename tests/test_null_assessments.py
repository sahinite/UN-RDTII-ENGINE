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


# ── Cross-document dedup (_dedup_cross_document) ────────────────────────────────

class _ProvRec:
    """Minimal record with the fields _dedup_cross_document reads."""
    def __init__(self, law_name, indicator_id, article, snippet, source_url,
                 confidence=1.0, location_reference=""):
        self.law_name = law_name
        self.indicator_id = indicator_id
        self.article = article
        self.verbatim_snippet = snippet
        self.source_url = source_url
        self.confidence = confidence
        self.location_reference = location_reference


def test_dedup_removes_same_act_fetched_under_two_urls():
    from main import _dedup_cross_document
    # Same provision, two documents: consolidated /Act/ vs as-enacted /acts-supp/.
    consolidated = _ProvRec(
        "Cybersecurity Act 2018", "P7-I3", "Section 29(1)(b)",
        "retain every such record for a period of not less than 3 years.",
        "https://sso.agc.gov.sg/Act/CA2018?ViewType=Pdf",
        location_reference="Page 80",
    )
    as_enacted = _ProvRec(
        "Cybersecurity Act 2018", "P7-I3", "Section 29(1)(b)",
        "retain every such record for a period of not less than 3 years.",
        "https://sso.agc.gov.sg/acts-supp/9-2018/?ViewType=Pdf",
        location_reference="Page unknown",
    )
    out = _dedup_cross_document([consolidated, as_enacted])
    assert len(out) == 1
    # keeps the consolidated /Act/ copy with the concrete location
    assert "/Act/" in out[0].source_url
    assert "unknown" not in out[0].location_reference.lower()


def test_dedup_keeps_distinct_provisions():
    from main import _dedup_cross_document
    a = _ProvRec("Companies Act 1967", "P7-I3", "Section 199(2)", "keep records 5 years.",
                 "https://sso.agc.gov.sg/Act/CoA1967")
    b = _ProvRec("Companies Act 1967", "P7-I3", "Section 344H(1)", "retain after dissolution.",
                 "https://sso.agc.gov.sg/Act/CoA1967")
    # same act+indicator but different article/snippet → NOT duplicates
    assert len(_dedup_cross_document([a, b])) == 2


def test_dedup_whitespace_and_case_insensitive():
    from main import _dedup_cross_document
    a = _ProvRec("Income Tax Act 1947", "P7-I3", "Section 67(1)(a)", "keep records for 5 years",
                 "https://sso.agc.gov.sg/Act/ITA1947", confidence=0.9)
    b = _ProvRec("income tax act 1947", "P7-I3", "Section  67(1)(a)", "keep   records for 5 years",
                 "https://sso.agc.gov.sg/Act/ITA1947", confidence=1.0)
    out = _dedup_cross_document([a, b])
    assert len(out) == 1
    assert out[0].confidence == 1.0  # higher-confidence copy wins the tie-break


# ── KNOWN cross-indicator prune (_prune_known_cross_indicator) ──────────────────

class _CIRec:
    def __init__(self, law, indicator_id, article, discovery_tag="KNOWN",
                 confidence=1.0, economy="X"):
        self.law_name = law
        self.indicator_id = indicator_id
        self.article = article
        self.discovery_tag = discovery_tag
        self.confidence = confidence
        self.economy = economy
        self.source_retrieval_method = "hybrid"
        self.source_rerank_score = 0.5


# Round 1: (act, section) -> indicators.  My Health s.77 -> I1 & I2 (two legit indicators).
_KSBI = {
    "P6-I1": {"my health records act": {"77"}},
    "P6-I2": {"my health records act": {"77"}},
    "P6-I4": {"personal data protection act": {"26"}},
}


class TestKnownCrossIndicatorPrune:
    def test_drops_out_of_set_known_keeps_round1_indicators(self):
        from main import _prune_known_cross_indicator
        recs = [_CIRec("My Health Records Act 2012", "P6-I1", "Section 77(1)"),
                _CIRec("My Health Records Act 2012", "P6-I2", "Section 77(1)"),
                _CIRec("My Health Records Act 2012", "P6-I3", "Section 77(1)")]  # spurious
        kept, mm = _prune_known_cross_indicator(recs, _KSBI)
        assert sorted(r.indicator_id for r in kept) == ["P6-I1", "P6-I2"]
        assert len(mm) == 1 and mm[0]["wrong_indicator"] == "P6-I3" and mm[0]["dropped"] is True

    def test_multi_indicator_known_is_preserved(self):
        # s.77 legitimately belongs to I1 AND I2 — both must survive.
        from main import _prune_known_cross_indicator
        recs = [_CIRec("My Health Records Act 2012", "P6-I1", "Section 77(1)"),
                _CIRec("My Health Records Act 2012", "P6-I2", "Section 77(1)")]
        kept, mm = _prune_known_cross_indicator(recs, _KSBI)
        assert len(kept) == 2 and mm == []

    def test_new_provision_untouched(self):
        from main import _prune_known_cross_indicator
        recs = [_CIRec("Privacy Act 1988", "P6-I1", "Section 26(1)", discovery_tag="NEW"),
                _CIRec("Privacy Act 1988", "P6-I3", "Section 26(1)", discovery_tag="NEW")]
        kept, mm = _prune_known_cross_indicator(recs, _KSBI)
        assert len(kept) == 2 and mm == []  # NEW never pruned

    def test_sole_wrong_indicator_copy_is_kept_not_lost(self):
        from main import _prune_known_cross_indicator
        recs = [_CIRec("My Health Records Act 2012", "P6-I3", "Section 77(1)")]  # only wrong copy
        kept, mm = _prune_known_cross_indicator(recs, _KSBI)
        assert len(kept) == 1                       # provision NOT lost
        assert len(mm) == 1 and mm[0]["dropped"] is False

    def test_sectionless_and_unmapped_rows_pass_through(self):
        from main import _prune_known_cross_indicator
        recs = [_CIRec("Cybersecurity Act 2018", "P6-I2", "Preamble"),        # no section token
                _CIRec("Some Act", "P6-I1", "Section 999")]                    # not in R1 map
        kept, mm = _prune_known_cross_indicator(recs, _KSBI)
        assert len(kept) == 2 and mm == []

    def test_fuzzy_titled_known_is_grouped_and_pruned(self):
        # A KNOWN row emitted with an abbreviated title ("PDPA") must resolve to its
        # Round 1 key and be subject to the same ground-truth prune — not bypass it.
        from main import _prune_known_cross_indicator
        recs = [_CIRec("PDPA", "P6-I4", "Section 26(2)"),          # correct indicator
                _CIRec("PDPA", "P6-I1", "Section 26(2)")]          # wrong indicator
        kept, mm = _prune_known_cross_indicator(recs, _KSBI)
        assert [r.indicator_id for r in kept] == ["P6-I4"]
        assert len(mm) == 1 and mm[0]["wrong_indicator"] == "P6-I1" and mm[0]["dropped"] is True

    def test_empty_ksbi_is_noop(self):
        from main import _prune_known_cross_indicator
        recs = [_CIRec("My Health Records Act 2012", "P6-I3", "Section 77(1)")]
        kept, mm = _prune_known_cross_indicator(recs, {})
        assert kept == recs and mm == []
