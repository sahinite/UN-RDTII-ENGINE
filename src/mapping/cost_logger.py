"""
Redirect — canonical CostLogger lives in src/output/cost_logger.py (W6 fix).

This module is kept as a thin re-export to avoid breaking any future imports,
but the implementation has been consolidated into src/output/cost_logger.py.
"""

from src.output.cost_logger import CostLogger, compute_llm_cost  # noqa: F401
