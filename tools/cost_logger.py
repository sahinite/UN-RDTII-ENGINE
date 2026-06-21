"""
Standalone cost measurement tool. [Z2-4 ST6]

Usage:
    python tools/cost_logger.py --pdf data/benchmark/benchmark_50pages.pdf \
        --economy Singapore --pillar 6

Runs the full pipeline on a single document and reports measured cost.
Writes logs/cost_report.json with token counts, per-component costs
(OCR / embedding / LLM / translation), and wall-clock processing time.
Required by the hackathon rubric — judges verify these against the code.
"""

import argparse
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Measure RDTII engine cost per document")
    parser.add_argument("--pdf", required=True, help="Path to PDF file")
    parser.add_argument("--economy", required=True, help="Economy name or ISO code")
    parser.add_argument("--pillar", required=True, type=int, choices=[6, 7], help="RDTII pillar number")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"Error: PDF not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    # Lazy import so tool is importable without all deps installed
    try:
        from main import process_single_document
    except ImportError as e:
        print(f"Error: cannot import pipeline — {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Running pipeline on: {args.pdf}")
    print(f"Economy: {args.economy} | Pillar: {args.pillar}")
    print("..." )

    cost_summary = process_single_document(args.pdf, args.economy, args.pillar)

    print(f"\n=== COST REPORT ===")
    print(f"Document:         {args.pdf}")
    print(f"Economy:          {args.economy} | Pillar: {args.pillar}")
    print(f"Model:            {cost_summary.get('model_version', 'unknown')}")
    print(f"LLM cost:         ${cost_summary.get('llm_cost_usd', 0):.6f}")
    print(f"OCR cost:         ${cost_summary.get('ocr_cost_usd', 0):.6f}")
    print(f"Translation cost: ${cost_summary.get('translation_cost_usd', 0):.6f}")
    print(f"Embedding cost:   $0.000000 (local model)")
    print(f"TOTAL:            ${cost_summary.get('total_cost_usd', 0):.6f}")
    print(f"Processing time:  {cost_summary.get('processing_time_s', 0):.1f}s")
    print(f"\nFull report: logs/cost_report.json")


if __name__ == "__main__":
    main()
