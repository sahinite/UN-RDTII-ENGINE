"""
Zone 1 Evidence Discovery package. [Z1-2..Z1-5]

Public API:
    load_taxonomy(path)      -> list[dict]           — load + return taxonomy.json
    validate_taxonomy(tax)                            — assert all required fields present
    run_probe(economy, tax)  -> list[ProbeResult]    — probe portals for active document URLs
    run_crawler(probes, econ, pillar) -> list[CandidateAct]  — BFS crawl discovered URLs
    run_currency_check(acts) -> list[CurrencyResult] — validate act URLs + Wayback archive
    run_ranker(currency, seed, tax, econ, output_dir) -> list[RankedAct]  — score + gate
    discover(economy_config, pillar, taxonomy, known_urls) -> list[Zone1Result] — strategy-driven discovery
    build_pillar_keywords(taxonomy, pillar) -> list[str]    — pillar-scoped keyword set

Models:
    ProbeResult, CandidateAct, CurrencyResult, RankedAct, SeedData

Exceptions:
    ProbeError, CrawlerError, RankerError, ConfigError
"""

from src.crawler.probe import (
    ProbeResult,
    load_taxonomy,
    run_probe,
    validate_taxonomy,
)
from src.crawler.crawler import CandidateAct, run_crawler
from src.crawler.currency import run_currency_check
from src.crawler.ranker import RankedAct, RankerError, run_ranker
from src.crawler.seed_loader import SeedData, load_seed_data
from src.crawler.exceptions import ConfigError, CrawlerError, ProbeError
from src.crawler.discover import ZONE2_MAX_ACTS, build_pillar_keywords, discover

__all__ = [
    # Taxonomy
    "load_taxonomy",
    "validate_taxonomy",
    # Zone 1 pipeline steps
    "run_probe",
    "run_crawler",
    "run_currency_check",
    "run_ranker",
    "discover",
    # Pillar scoping
    "build_pillar_keywords",
    # Caps
    "ZONE2_MAX_ACTS",
    # Seed data
    "load_seed_data",
    # Models
    "ProbeResult",
    "CandidateAct",
    "RankedAct",
    "SeedData",
    # Exceptions
    "ConfigError",
    "CrawlerError",
    "ProbeError",
    "RankerError",
]
