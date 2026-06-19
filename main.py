"""
RDTII Extraction Engine — CLI entry point.

Usage:
    python main.py --economy Singapore --pillar 7
    python main.py --economy Singapore --pillar 6 --pdf path/to/law.pdf

Wires together Zone 1 (Evidence Discovery) and Zone 2 (Intelligent Mapping).
See ClickUp list "UN ESCAP" — epics ZONE 1 / ZONE 2 — for the build sequence.

TODO:
  - argparse: --economy, --pillar (6|7|all), --output-dir, --format, --pdf  [Z1-2]
  - run Zone 1 pipeline (src/crawler/*)                                      [Z1-2..Z1-5]
  - run Zone 2 pipeline (src/fetcher/*, retrieval/*, mapping/*)              [Z2-1..Z2-4]
  - write output (src/output/writer.py)                                      [Z2-6]
"""

import json
import sys
from pathlib import Path

from src.config.economy_config import InvalidEconomyConfigError, UnknownEconomyError, load_economy
from src.crawler.exceptions import ConfigError
from src.crawler.probe import load_taxonomy, validate_taxonomy


def main() -> None:
    # ── Startup: load and validate taxonomy.json ───────────────────────────────
    try:
        taxonomy = load_taxonomy("taxonomy.json")
        validate_taxonomy(taxonomy)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    except ConfigError as exc:
        print(f"[ERROR] taxonomy.json validation failed: {exc}", file=sys.stderr)
        sys.exit(1)

    raise NotImplementedError(
        "Wire up CLI argparse + pipeline — see ClickUp epics ZONE 1 / ZONE 2. "
        "Taxonomy loaded and validated OK."
    )


if __name__ == "__main__":
    main()
