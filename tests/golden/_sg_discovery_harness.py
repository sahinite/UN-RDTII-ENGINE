"""
Deterministic, offline reproduction of Singapore Zone-1 discovery output.

This is the harness behind the SG golden-output regression baseline (ADR-048 /
docs/prd/unified-portal-strategy.md D9). It pins every input so that `discover()`
can be run identically before and after the unified-strategy refactor:

  - config   : the real economies/singapore.yaml (the thing under protection)
  - taxonomy : the real taxonomy.json (the thing under protection)
  - seeds    : a pinned SNAPSHOT of the Round 1 DB (tests/golden/round1_db_sg.snapshot.xlsx),
               copied from data/database/ at capture time so the baseline is
               self-contained/CI-safe and never moves when data/database/ churns
               (e.g. Round 2). Regenerate the snapshot + goldens deliberately if
               the ground-truth DB legitimately changes.
  - network  : transport_fetch is mocked to the sso_browse_index.html fixture

Two modes are captured per pillar:
  - "known_only" (max_new=0)  — the production build-gate config (KNOWN-only)
  - "with_new"   (max_new=5)  — exercises the NEW ranking + threshold + cap path

Output is serialised order-insensitively (sorted) because `discover()` iterates
seed `set`s whose iteration order is not stable across processes; the
membership + tag + title of the result is the deterministic contract, not row order.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_DIR = Path(__file__).resolve().parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
_SSO_INDEX_HTML = (FIXTURES / "sso_browse_index.html").read_text(encoding="utf-8")
_SEED_DB = GOLDEN_DIR / "round1_db_sg.snapshot.xlsx"

# (pillar, mode label, max_new) — the full baseline matrix.
CASES = [
    (6, "known_only", 0),
    (6, "with_new", 5),
    (7, "known_only", 0),
    (7, "with_new", 5),
]


def golden_path(pillar: int, mode: str) -> Path:
    return GOLDEN_DIR / f"sg_discovery_p{pillar}_{mode}.json"


def run_sg_discovery(pillar: int, max_new: int) -> list[dict]:
    """Reproduce SG discovery for one pillar/mode and return a canonical result list."""
    from src.config.economy_config import load_economy
    from src.crawler.discover import discover
    from src.crawler.probe import load_taxonomy
    from src.crawler.seed_loader import load_seed_data

    config = load_economy("singapore")
    taxonomy = load_taxonomy(str(REPO_ROOT / "taxonomy.json"))
    seed = load_seed_data(
        "SG",
        f"P{pillar}",
        round1_db_path=str(_SEED_DB),
        economy_name="Singapore",
    )

    async def _mock_fetch(url, portal):  # noqa: ANN001
        return _SSO_INDEX_HTML, 200

    with (
        patch("src.crawler.discover.transport_fetch", side_effect=_mock_fetch),
        patch("src.crawler.discover._MAX_NEW_ACTS", max_new),
    ):
        results = asyncio.run(
            discover(
                config,
                pillar,
                taxonomy,
                seed.known_urls,
                known_titles=seed.known_titles,
                known_titles_by_indicator=seed.known_titles_by_indicator,
            )
        )

    rows = [
        {
            "url": r.url,
            "economy": r.economy,
            "act_title": r.act_title,
            "discovery_tag": r.discovery_tag,
        }
        for r in results
    ]
    rows.sort(key=lambda d: (d["url"], d["act_title"], d["discovery_tag"]))
    return rows


if __name__ == "__main__":
    import json
    import sys

    # `python -m tests.golden._sg_discovery_harness [freeze]`
    #   no arg  → print all cases to stdout
    #   freeze  → (re)write the golden json files
    freeze = len(sys.argv) > 1 and sys.argv[1] == "freeze"
    for pillar, mode, max_new in CASES:
        rows = run_sg_discovery(pillar, max_new)
        if freeze:
            golden_path(pillar, mode).write_text(
                json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"froze P{pillar}/{mode}: {len(rows)} rows -> {golden_path(pillar, mode).name}")
        else:
            print(f"# P{pillar}/{mode}: {len(rows)} rows")
            print(json.dumps(rows, ensure_ascii=False, indent=2))
