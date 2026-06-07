"""
Measures ACTUAL (not estimated) per-document cost. [Z2-6]

Usage:
    python tools/cost_logger.py --pdf data/benchmark/benchmark_50pages.pdf \
        --economy Singapore --pillar 6

Writes logs/cost_report.json with token counts, per-component $ costs
(OCR / embedding / LLM / crawling), and wall-clock processing time.
Required by the hackathon rubric for UN sustainability assessment —
judges verify these numbers against the code.

TODO: def main(): parse args, run pipeline with instrumentation, write cost_report.json
"""

def main():
    raise NotImplementedError("Implement after [Z2-6] writer is in place — instrument each pipeline stage")


if __name__ == "__main__":
    main()
