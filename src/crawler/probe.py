"""
Taxonomy loading + validation.

Once the home of the auto-probe portal discovery (the retired
probe → crawl → currency → rank pipeline); that path was replaced by the
strategy-driven discover.py. Only the taxonomy helpers remain — used by
main.py (startup validation) and Zone 1 discovery.

Public API:
    validate_taxonomy(taxonomy) -> None
    load_taxonomy(path) -> list[dict]
"""

from __future__ import annotations

import json
from pathlib import Path

from src.crawler.exceptions import ConfigError


def validate_taxonomy(taxonomy: list[dict]) -> None:
    """
    Validate all indicators have a non-empty probe_keywords list.
    Raises ConfigError with the offending indicator_id if any are missing.
    """
    for indicator in taxonomy:
        iid = indicator.get("indicator_id", "<unknown>")
        keywords = indicator.get("probe_keywords")
        if not keywords or not isinstance(keywords, list) or len(keywords) == 0:
            raise ConfigError(
                f"Indicator '{iid}' is missing 'probe_keywords' in taxonomy.json. "
                "Add a non-empty probe_keywords array for every indicator."
            )


def load_taxonomy(taxonomy_path: str = "taxonomy.json") -> list[dict]:
    """Load and return taxonomy.json as a list of indicator dicts."""
    path = Path(taxonomy_path)
    if not path.exists():
        raise FileNotFoundError(f"taxonomy.json not found at '{path.resolve()}'")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("taxonomy.json must be a JSON array of indicator objects")
    return data
