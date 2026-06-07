"""
Batch runner — runs main.py sequentially across multiple economies/pillars.

Usage:
    python batch_run.py --economies Singapore Australia Malaysia --pillar 6 7

NOTE: Per planning decision, economies run ONE AT A TIME (not simultaneously) —
this script is a thin sequential wrapper, not a parallel orchestrator.
"""

def main():
    raise NotImplementedError("Sequential wrapper around main.py — implement after [Z2-6] is done")


if __name__ == "__main__":
    main()
