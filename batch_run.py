"""
Batch runner — runs the RDTII pipeline across multiple economies/pillars.

Usage:
    python batch_run.py --economies Singapore Australia Malaysia --pillar 6 7
    python batch_run.py --economies Singapore Australia Malaysia --pillar 6 7 --max-parallel 2

By default each economy runs in its own **isolated subprocess, concurrently** — one
lane per economy (its pillars run sequentially within that lane). Separate processes
have separate embedder / reranker / Argos state, so cross-economy parallelism is safe
here (it is NOT safe with threads inside one process — those models are process-global
singletons, ADR-005/060/061). Each subprocess writes to its own log dir
(RDTII_LOG_DIR=logs/<economy>_P<pillar>) so the rotating log and cost_report.json don't
clash; CSV/JSON outputs are already per-economy-pillar-timestamped.

    --max-parallel 0 (default): one concurrent lane per economy (auto).
    --max-parallel N: cap concurrent economy lanes at N (use for many economies to
                      avoid LLM rate limits / memory pressure).
    --max-parallel 1: fully sequential, in-process (the original behaviour).
"""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def _run_job_subprocess(economy, pillar, output_dir, fmt):
    """Run one (economy, pillar) as an isolated `python main.py` subprocess."""
    log_dir = Path("logs") / f"{economy}_P{pillar}"
    log_dir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "RDTII_LOG_DIR": str(log_dir)}
    t0 = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "main.py",
         "--economy", economy, "--pillar", str(pillar),
         "--output-dir", str(output_dir), "--format", fmt],
        env=env, capture_output=True, text=True,
    )
    return economy, pillar, proc.returncode, proc.stdout, proc.stderr, time.monotonic() - t0


def _report(economy, pillar, rc, out, err, elapsed):
    if rc == 0:
        print(f"  ✓ {economy} P{pillar} ({elapsed:.1f}s)")
        return True
    print(f"  ✗ {economy} P{pillar} FAILED (exit {rc}, {elapsed:.1f}s)", file=sys.stderr)
    tail = (err or out or "").strip().splitlines()[-8:]
    print("    " + "\n    ".join(tail), file=sys.stderr)
    return False


def _run_sequential(economies, pillars, output_dir, fmt) -> tuple[int, int]:
    """In-process, one job at a time (original behaviour — used for a single lane)."""
    from main import run_pipeline

    completed = failed = 0
    jobs = [(e, p) for e in economies for p in pillars]
    for i, (economy, pillar) in enumerate(jobs, 1):
        t0 = time.monotonic()
        print(f"[{i}/{len(jobs)}] {economy} | Pillar {pillar} ...")
        try:
            run_pipeline(economy=economy, pillar=pillar, output_dir=output_dir, fmt=fmt)
            print(f"  Done ({time.monotonic() - t0:.1f}s)")
            completed += 1
        except KeyboardInterrupt:
            print("\nBatch run interrupted.", file=sys.stderr)
            sys.exit(1)
        except Exception as exc:  # noqa: BLE001 — one job's failure must not abort the batch
            print(f"  FAILED ({time.monotonic() - t0:.1f}s): {exc}", file=sys.stderr)
            failed += 1
    return completed, failed


def _run_parallel_by_economy(economies, pillars, output_dir, fmt, max_parallel) -> tuple[int, int]:
    """One concurrent lane per economy (its pillars run sequentially within the lane)."""

    def run_lane(economy):
        results = []
        for pillar in pillars:
            results.append(_run_job_subprocess(economy, pillar, output_dir, fmt))
        return results

    completed = failed = 0
    with ThreadPoolExecutor(max_workers=max_parallel) as ex:
        futures = [ex.submit(run_lane, e) for e in economies]
        try:
            for fut in as_completed(futures):
                for economy, pillar, rc, out, err, elapsed in fut.result():
                    if _report(economy, pillar, rc, out, err, elapsed):
                        completed += 1
                    else:
                        failed += 1
        except KeyboardInterrupt:
            print("\nBatch run interrupted — cancelling remaining lanes.", file=sys.stderr)
            for f in futures:
                f.cancel()
            sys.exit(1)
    return completed, failed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the RDTII pipeline across economies and pillars"
    )
    parser.add_argument("--economies", nargs="+", required=True,
                        help="Economy names (e.g. Singapore Australia Malaysia)")
    parser.add_argument("--pillar", nargs="+", type=int, default=[6, 7],
                        help="Pillar(s) to process (default: 6 7)")
    parser.add_argument("--output-dir", default="outputs",
                        help="Directory for output files (default: outputs/)")
    parser.add_argument("--format", choices=["csv", "json", "both"], default="both",
                        help="Output format (default: both)")
    parser.add_argument("--max-parallel", type=int, default=0,
                        help="Cap concurrent economy lanes (0 = auto: one per economy; "
                             "1 = fully sequential in-process)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    n_econ = len(args.economies)
    # Auto: one lane per economy. Never more lanes than economies.
    lanes = n_econ if args.max_parallel <= 0 else min(args.max_parallel, n_econ)

    total = n_econ * len(args.pillar)
    mode = "sequential (in-process)" if lanes <= 1 else f"{lanes} economies in parallel (isolated subprocesses)"
    print(f"\nBatch run: {n_econ} economies x {len(args.pillar)} pillars = {total} runs — {mode}")
    print(f"Output directory: {output_dir}\n")

    t_start = time.monotonic()
    if lanes <= 1:
        completed, failed = _run_sequential(args.economies, args.pillar, output_dir, args.format)
    else:
        completed, failed = _run_parallel_by_economy(
            args.economies, args.pillar, output_dir, args.format, lanes)

    print(f"\nBatch complete: {completed} succeeded, {failed} failed, "
          f"{time.monotonic() - t_start:.1f}s total")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
