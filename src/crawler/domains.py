"""
Shared registered-domain utility.

Uses tldextract with the Public Suffix List snapshot bundled in the package:
  - suffix_list_urls=()  → never fetch the PSL over HTTP
  - cache_dir=None       → never write a disk cache

By default tldextract fetches publicsuffix.org on first use and writes to
~/.cache, which adds a network dependency, a startup cost, and a warning in
sandboxed/CI environments (it still works there via the snapshot, but noisily).
The bundled snapshot is sufficient for gov/domain matching, so we pin it —
deterministic and offline. This is the single source of truth for domain
extraction; crawler and fetcher both import from here (no duplication).
"""

from __future__ import annotations

import tldextract

_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


def registered_domain(url: str) -> str:
    """Registered (eTLD+1) domain of a URL.

    'https://www.pdp.gov.my/ppdpv1/x' -> 'pdp.gov.my'
    'https://ccid.rmp.gov.my/a'       -> 'rmp.gov.my'
    """
    ext = _EXTRACT(url)
    return getattr(ext, "top_domain_under_public_suffix", None) or ext.registered_domain
