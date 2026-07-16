"""Unit tests for probe.py taxonomy helpers."""

from __future__ import annotations

import pytest

from src.crawler.exceptions import ConfigError
from src.crawler.probe import validate_taxonomy


# ── Taxonomy validation ────────────────────────────────────────────────────────

def test_missing_keywords_raises_config_error(full_taxonomy):
    bad = [dict(ind) for ind in full_taxonomy]
    bad[3]["probe_keywords"] = []  # empty list for P6-I4
    with pytest.raises(ConfigError, match="P6-I4"):
        validate_taxonomy(bad)


def test_missing_probe_keywords_field_raises_config_error(full_taxonomy):
    bad = [dict(ind) for ind in full_taxonomy]
    del bad[0]["probe_keywords"]  # remove field entirely from P6-I1
    with pytest.raises(ConfigError, match="P6-I1"):
        validate_taxonomy(bad)


def test_valid_taxonomy_does_not_raise(full_taxonomy):
    validate_taxonomy(full_taxonomy)  # should not raise
