"""Custom exceptions for the crawler / probe modules."""


class ProbeError(Exception):
    """Raised when all portals for an economy return zero results."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ConfigError(Exception):
    """Raised when taxonomy.json or economy config is missing required fields."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class CrawlerError(Exception):
    """Raised when the crawler fails to find any candidate acts."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
