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


class PDPAGateError(MappingError):
    """Singapore PDPA-first gate failed — engine must not proceed."""


class ConfigError(MappingError):
    """Misconfiguration — missing env var, unknown economy, invalid YAML."""
