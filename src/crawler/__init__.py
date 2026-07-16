"""
Zone 1 Evidence Discovery package.

Public API:
    load_taxonomy(path)      -> list[dict]           — load + return taxonomy.json
    validate_taxonomy(tax)                            — assert all required fields present
    run_probe(economy, tax)  -> list[ProbeResult]    — probe portals for active document URLs
    discover(economy_config, pillar, taxonomy, known_urls) -> list[Zone1Result] — strategy-driven discovery
    build_pillar_keywords(taxonomy, pillar) -> list[str]    — pillar-scoped keyword set

Models:
    ProbeResult, SeedData

Exceptions:
    ProbeError, CrawlerError, ConfigError
"""

from src.crawler.probe import (
    ProbeResult,
    load_taxonomy,
    run_probe,
    validate_taxonomy,
)
from src.crawler.seed_loader import SeedData, load_seed_data
from src.crawler.exceptions import ConfigError, CrawlerError, ProbeError
from src.crawler.discover import ZONE2_MAX_ACTS, build_pillar_keywords, discover

__all__ = [
    # Taxonomy
    "load_taxonomy",
    "validate_taxonomy",
    # Zone 1 pipeline steps
    "run_probe",
    "discover",
    # Pillar scoping
    "build_pillar_keywords",
    # Caps
    "ZONE2_MAX_ACTS",
    # Seed data
    "load_seed_data",
    # Models
    "ProbeResult",
    "SeedData",
    # Exceptions
    "ConfigError",
    "CrawlerError",
    "ProbeError",
]
