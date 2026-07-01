"""
SG discovery golden-output regression (ADR-048 / unified-portal-strategy.md D9).

This is the safety net that lets the unified-strategy refactor proceed WITHOUT
manually re-testing Singapore. It re-runs the pinned, offline, deterministic
Singapore Zone-1 discovery (real singapore.yaml + real taxonomy.json + a pinned
Round 1 DB snapshot + the sso_browse_index.html fixture) and asserts the result
is byte-identical to the frozen baseline in tests/golden/.

If a change to the shared discovery path (e.g. lifting rank/exclude/tag out of
`_discover_index` into the `discover()` tail) alters SG's output, these tests
fail — that is the point. When SG's output legitimately changes (e.g. the
taxonomy or singapore.yaml is intentionally updated), regenerate the baseline:

    python -m tests.golden._sg_discovery_harness freeze

and review the diff in the golden JSON files as part of that change.
"""

from __future__ import annotations

import json

import pytest

from tests.golden._sg_discovery_harness import CASES, golden_path, run_sg_discovery


@pytest.mark.parametrize("pillar,mode,max_new", CASES, ids=[f"P{p}_{m}" for p, m, _ in CASES])
def test_sg_discovery_matches_golden(pillar: int, mode: str, max_new: int):
    path = golden_path(pillar, mode)
    assert path.exists(), (
        f"Missing golden {path.name}. Generate it with: "
        f"python -m tests.golden._sg_discovery_harness freeze"
    )
    expected = json.loads(path.read_text(encoding="utf-8"))
    actual = run_sg_discovery(pillar, max_new)
    assert actual == expected, (
        f"SG P{pillar}/{mode} discovery output drifted from the golden baseline.\n"
        f"If this change is intentional, regenerate with:\n"
        f"    python -m tests.golden._sg_discovery_harness freeze\n"
        f"and review the diff. Otherwise the refactor changed SG behavior."
    )


def test_sg_discovery_is_deterministic():
    """Two runs in the same process must be identical (order-insensitive contract)."""
    a = run_sg_discovery(7, 0)
    b = run_sg_discovery(7, 0)
    assert a == b
