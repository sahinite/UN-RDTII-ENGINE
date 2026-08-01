"""
Custom exceptions for the mapping module.
"""


class MappingError(Exception):
    """Base class for all mapping module errors."""


class ProviderRateLimitError(MappingError):
    def __init__(self, provider: str, detail: str):
        super().__init__(f"[{provider}] Rate limit: {detail}")
        self.provider = provider


class ProviderAPIError(MappingError):
    def __init__(self, provider: str, detail: str):
        super().__init__(f"[{provider}] API error: {detail}")
        self.provider = provider


class ProviderTimeoutError(MappingError):
    def __init__(self, provider: str, detail: str):
        super().__init__(f"[{provider}] Timeout: {detail}")
        self.provider = provider


class AllProvidersExhaustedError(MappingError):
    """All 5 cascade tiers failed for a single LLM call."""


class ParseError(MappingError):
    """LLM response could not be parsed as valid JSON."""


class QualityGateError(MappingError):
    """Configurable economy/pillar quality gate failed."""


class PDPAGateError(QualityGateError):
    """Deprecated compatibility exception for the former Singapore-only gate."""


class ConfigError(MappingError):
    """Misconfiguration — missing env var, unknown economy, invalid YAML."""
