"""
Regression tests for load_seed_data() economy/pillar matching.

Guards the silent-empty bug: the Round 1 DB stores the economy as a full name
("Singapore" -> normalised "SG") while the engine passes the ISO-3 code ("SGP").
The matcher only normalised the row side, so "SG" != "SGP" skipped every row and
seed data came back empty -> discovery tagged everything NEW. The fix normalises
BOTH sides of the comparison.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.crawler.seed_loader import load_seed_data, normalise_title

_ROUND1_DB = Path("data/sample_kit/ESCAP-RDTII-2.1_ Round 1 Database.xlsx")

pytestmark = pytest.mark.skipif(
    not _ROUND1_DB.exists(), reason="Round 1 DB not present in this checkout"
)


@pytest.mark.parametrize("economy_code", ["SGP", "Singapore", "SG"])
def test_singapore_p7_seeds_load_for_any_economy_spelling(economy_code: str):
    """ISO-3, full name, and ISO-2 must all resolve to the same Singapore seeds."""
    seed = load_seed_data(economy_code, "P7", round1_db_path=str(_ROUND1_DB))
    assert seed.known_titles, f"no known_titles loaded for {economy_code!r}"


def test_iso3_input_is_not_silently_empty():
    """The exact failure mode: ISO-3 'SGP' must not return an empty seed set."""
    seed = load_seed_data("SGP", "P7", round1_db_path=str(_ROUND1_DB))
    assert len(seed.known_titles) > 0


def test_known_titles_include_core_p7_acts():
    """The acts the P7 run depends on must be title-matchable as KNOWN seeds."""
    seed = load_seed_data("SGP", "P7", round1_db_path=str(_ROUND1_DB))
    for act in ("Personal Data Protection Act", "Cybersecurity Act",
                "Criminal Procedure Code", "Companies Act", "Income Tax Act"):
        assert normalise_title(act) in seed.known_titles, f"missing seed title: {act}"


def test_unrelated_economy_does_not_borrow_singapore_seeds():
    """A different economy must not pick up Singapore rows (matcher still filters)."""
    sg = load_seed_data("SGP", "P7", round1_db_path=str(_ROUND1_DB))
    au = load_seed_data("AUS", "P7", round1_db_path=str(_ROUND1_DB))
    assert sg.known_titles != au.known_titles
