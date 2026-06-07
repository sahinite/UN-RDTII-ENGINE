"""
RDTII Extraction Engine — CLI entry point.

Usage:
    python main.py --economy Singapore --pillar 6
    python main.py --economy Singapore --pillar 6 --pdf path/to/law.pdf

Wires together Zone 1 (Evidence Discovery) and Zone 2 (Intelligent Mapping).
See ClickUp list "UN ESCAP" — epics ZONE 1 / ZONE 2 — for the build sequence.

TODO:
  - argparse: --economy, --pillar (6|7|all), --output-dir, --format, --pdf
  - load economy config (src/config/economy_config.py)        [Z1-1]
  - run Zone 1 pipeline (src/crawler/*)                        [Z1-2..Z1-5]
  - run Zone 2 pipeline (src/fetcher/*, retrieval/*, mapping/*)[Z2-1..Z2-4]
  - write output (src/output/writer.py)                        [Z2-6]
"""

def main():
    raise NotImplementedError("Wire up CLI + pipeline — see ClickUp epics ZONE 1 / ZONE 2")


if __name__ == "__main__":
    main()
