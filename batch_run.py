"""
Batch runner — runs the RDTII pipeline sequentially across multiple economies/pillars.

Usage:
    python batch_run.py --economies Singapore Australia Malaysia --pillar 6 7
    python batch_run.py --economies Singapore --pillar 7 --output-dir outputs/

NOTE: Economies run ONE AT A TIME (ADR-005) — sequential, never parallel.
Cost telemetry per economy must be clean and the hackathon rubric verifies actual costs.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run RDTII pipeline sequentially across economies and pillars"
    )
    parser.add_argument(
        "--economies", nargs="+", required=True,
        help="Economy names to process (e.g. Singapore Australia Malaysia)"
    )
    parser.add_argument(
        "--pillar", nargs="+", type=int, default=[6, 7],
        help="Pillar(s) to process (default: 6 7, e.g. --pillar 6 7 8)"
    )
    parser.add_argument(
        "--output-dir", default="outputs",
        help="Directory for output files (default: outputs/)"
    )
    parser.add_argument(
        "--format", choices=["csv", "json", "both"], default="both",
        help="Output format (default: both)"
    )
    args = parser.parse_args()

    from main import run_pipeline

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    total_runs = len(args.economies) * len(args.pillar)
    completed = 0
    failed = 0
    t_batch_start = time.monotonic()

    print(f"\nBatch run: {len(args.economies)} economies x {len(args.pillar)} pillars = {total_runs} runs")
    print(f"Output directory: {output_dir}\n")

    for economy in args.economies:
        for pillar in args.pillar:
            t0 = time.monotonic()
            print(f"[{completed + failed + 1}/{total_runs}] {economy} | Pillar {pillar} ...")
            try:
                run_pipeline(
                    economy=economy,
                    pillar=pillar,
                    output_dir=output_dir,
                    fmt=args.format,
                )
                elapsed = time.monotonic() - t0
                print(f"  Done ({elapsed:.1f}s)")
                completed += 1
            except KeyboardInterrupt:
                print("\nBatch run interrupted.", file=sys.stderr)
                sys.exit(1)
            except Exception as exc:
                elapsed = time.monotonic() - t0
                print(f"  FAILED ({elapsed:.1f}s): {exc}", file=sys.stderr)
                failed += 1

    total_elapsed = time.monotonic() - t_batch_start
    print(f"\nBatch complete: {completed} succeeded, {failed} failed, {total_elapsed:.1f}s total")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
