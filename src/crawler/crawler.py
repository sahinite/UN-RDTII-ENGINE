"""Shared URL helpers + HTML act-link extraction.

Once the home of a BFS crawler (the old probe → crawl → currency → rank
pipeline); that pipeline was replaced by the strategy-driven discover.py, so
only the reusable URL/link helpers remain here. They are imported by
discover.py, seed_loader.py, and provision_tag.py.
"""

import urllib.parse
from pathlib import Path

from bs4 import BeautifulSoup

# Domain extraction lives in the shared, offline-pinned utility; re-exported here
# under the historical name for existing importers (discover, seed_loader).
from src.crawler.domains import registered_domain as _registered_domain

_SKIP_EXTENSIONS = frozenset([
    ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map",
])


# ── URL helpers ────────────────────────────────────────────────────────────────

def _normalise_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url.lower().rstrip("/"))
    params = urllib.parse.parse_qs(parsed.query)
    keep = {k: v for k, v in params.items() if k not in ("lang", "language", "locale")}
    new_query = urllib.parse.urlencode(keep, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


def _is_same_domain(url: str, portal_domain: str) -> bool:
    return _registered_domain(url) == portal_domain


def _is_skip_url(url: str) -> bool:
    path = urllib.parse.urlparse(url).path.lower()
    ext = Path(path).suffix
    return ext in _SKIP_EXTENSIONS


def _make_absolute(href: str, base_url: str) -> str:
    return urllib.parse.urljoin(base_url, href)


# ── HTML parsing ───────────────────────────────────────────────────────────────

def _extract_act_links(html: str, base_url: str, portal_domain: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    acts: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        abs_url = _make_absolute(href, base_url)
        if _is_skip_url(abs_url):
            continue
        if not _is_same_domain(abs_url, portal_domain):
            continue
        norm = _normalise_url(abs_url)
        if norm in seen:
            continue
        title = a.get_text(strip=True)
        if not title or len(title) < 5:
            continue
        seen.add(norm)
        parent = a.find_parent(["li", "div", "tr", "p"])
        snippet = (parent.get_text(separator=" ", strip=True)[:500] if parent else "")
        acts.append({"url": abs_url, "title": title, "snippet": snippet})
    return acts
