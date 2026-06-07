"""
Currency / in-force check + Wayback Machine archiving. [Z1-4]

Verifies a retrieved act is the current, in-force version (auto-fetches the
replacement if superseded) and archives source_url -> archive_url at discovery
time so citations stay reproducible.

TODO: def check_currency(doc) -> CurrencyResult
TODO: def archive_url(url) -> str  (Wayback Machine API)
"""
