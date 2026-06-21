"""
Custom exceptions for the mapping module. [Z2-4 ST6]
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


class PromptTooLongError(MappingError):
    def __init__(self, estimated: int, limit: int):
        super().__init__(f"Prompt too long: {estimated} estimated tokens > {limit} limit")
        self.estimated = estimated
        self.limit = limit


class PDPAGateError(MappingError):
    """Singapore PDPA-first gate failed — engine must not proceed."""


class ConfigError(MappingError):
    """Misconfiguration — missing env var, unknown economy, invalid YAML."""


class TaxonomyError(MappingError):
    """Invalid or missing indicator ID in taxonomy.json."""
