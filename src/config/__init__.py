"""
Economy configuration package.

Public API:
    load_economy(name)        -> EconomyConfig
    EconomyConfig             — Pydantic model for one economy's settings
    Portal                    — Pydantic model for one government portal entry
    UnknownEconomyError       — raised when economies/{name}.yaml is missing
    InvalidEconomyConfigError — raised when YAML fails schema validation
"""

from src.config.economy_config import (
    EconomyConfig,
    InvalidEconomyConfigError,
    Portal,
    UnknownEconomyError,
    load_economy,
)

__all__ = [
    "load_economy",
    "EconomyConfig",
    "Portal",
    "UnknownEconomyError",
    "InvalidEconomyConfigError",
]
