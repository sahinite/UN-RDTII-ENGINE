"""
Shared registered-domain utility — the single source of truth for domain
extraction (crawler and fetcher both import from here).

Uses tldextract pinned to the PSL snapshot bundled in the package
(suffix_list_urls=() → no HTTP fetch; cache_dir=None → no disk cache). By default
tldextract fetches publicsuffix.org on first use, adding a network dependency,
startup cost, and CI warnings; the bundled snapshot is deterministic and offline.
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
