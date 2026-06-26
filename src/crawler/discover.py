"""
Strategy-driven Zone 1 discovery. [PRD: Pillar-Agnostic Portal Strategy]

Replaces the probe → BFS crawl → currency → rank pipeline with a single
discover() step driven by per-portal strategy declarations in economies/*.yaml.

Public API:
    discover(economy_config, pillar, taxonomy, known_urls, output_dir) -> list[Zone1Result]
    build_pillar_keywords(taxonomy, pillar) -> list[str]
"""

from __future__ import annotations

import logging
import os
import time
import urllib.parse
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

from src.crawler.crawler import _normalise_url, _registered_domain
from src.crawler.seed_loader import normalise_title
from src.crawler.transport import fetch as transport_fetch
from src.fetcher.models import Zone1Result

if TYPE_CHECKING:
    from src.config.economy_config import EconomyConfig, Portal

logger = logging.getLogger(__name__)

# ── Environment config ─────────────────────────────────────────────────────────

ZONE2_MAX_ACTS = int(os.getenv("ZONE2_MAX_ACTS", "3"))  # NEW (discovered) acts cap
# Round 1 KNOWN seed acts are ground truth (Companies/Income Tax/etc. carry real
# storage/retention provisions) — keep all of them, bounded, rather than letting
# the NEW cap truncate them by title-BM25 rank.
ZONE2_MAX_KNOWN = int(os.getenv("ZONE2_MAX_KNOWN", "12"))
_DISCOVER_BUDGET_S = float(os.getenv("DISCOVER_BUDGET_S", "120.0"))
_INDEX_FETCH_TIMEOUT_S = float(os.getenv("INDEX_FETCH_TIMEOUT_S", "30.0"))
# Normalised BM25 title score a NEW (not-in-seed) act must clear to be kept.
# Raised from 0.05 → 0.2: low values let acts that merely share a generic word
# (e.g. "Personal" in "Personal Mobility Devices") rank as P7 matches.
_NEW_SCORE_THRESHOLD = float(os.getenv("NEW_SCORE_THRESHOLD", "0.2"))


# ── Pillar-scoped exclusion lists ──────────────────────────────────────────────

def build_pillar_excludes(taxonomy: list[dict], pillar: int) -> tuple[set[str], set[str]]:
    """
    Gather exclude_act_titles and exclude_keywords for the pillar's indicators.

    Returns (exclude_title_substrings, exclude_keywords) — both lowercased — used
    to drop obviously-irrelevant acts (e.g. banking/tax acts) before they become
    NEW candidates. KNOWN seed acts are never excluded.
    """
    prefix = f"P{pillar}-"
    titles: set[str] = set()
    keywords: set[str] = set()
    for ind in taxonomy:
        if not ind.get("indicator_id", "").startswith(prefix):
            continue
        titles.update(t.lower().strip() for t in ind.get("exclude_act_titles", []) if t.strip())
        keywords.update(k.lower().strip() for k in ind.get("exclude_keywords", []) if k.strip())
    return titles, keywords


# ── Pillar-scoped keyword builder ──────────────────────────────────────────────

def build_pillar_keywords(taxonomy: list[dict], pillar: int) -> list[str]:
    """
    Return the probe_keywords for indicators in the given pillar only.

    Filters taxonomy by indicator_id prefix 'P{pillar}-' so that a pillar-7
    run only uses P7-* keywords and never touches P6 evidence.
    """
    prefix = f"P{pillar}-"
    keywords: list[str] = []
    seen: set[str] = set()
    for ind in taxonomy:
        if not ind.get("indicator_id", "").startswith(prefix):
            continue
        for kw in ind.get("probe_keywords", []):
            if kw not in seen:
                seen.add(kw)
                keywords.append(kw)
    return keywords


# ── Index page parsing ─────────────────────────────────────────────────────────

def _parse_index_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """
    Parse (title, absolute_url) pairs from an SSO-style browse index page.

    Targets hrefs that contain '/Act/' or '/SL/' — the two entry types on SSO
    in-force browse indexes. Generic enough for other portals that use similar
    path patterns.
    """
    soup = BeautifulSoup(html, "html.parser")
    portal_domain = urllib.parse.urlparse(base_url).netloc
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue

        # Build absolute URL
        abs_url = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(abs_url)

        # Keep only same-domain links with /Act/ or /SL/ in the path
        if parsed.netloc != portal_domain:
            continue
        path = parsed.path
        if not (("/Act/" in path) or ("/SL/" in path)):
            continue

        norm = _normalise_url(abs_url)
        if norm in seen:
            continue
        seen.add(norm)

        title = a.get_text(strip=True)
        if not title or len(title) < 3:
            continue

        candidates.append((title, abs_url))

    return candidates


# ── BM25 title ranker ──────────────────────────────────────────────────────────

def _rank_by_keywords(
    candidates: list[tuple[str, str]],
    keywords: list[str],
) -> list[tuple[float, str, str]]:
    """
    BM25-rank candidates (title, url) against keyword set.
    Returns list of (score, title, url) sorted descending.
    """
    if not candidates or not keywords:
        return [(0.0, t, u) for t, u in candidates]

    try:
        from rank_bm25 import BM25Okapi  # type: ignore[import]
    except ImportError:
        logger.warning("[DISCOVER] rank_bm25 not installed — returning unranked candidates")
        return [(1.0, t, u) for t, u in candidates]

    corpus = [title.lower().split() for title, _ in candidates]
    bm25 = BM25Okapi(corpus)
    query_tokens = " ".join(keywords).lower().split()
    raw_scores = bm25.get_scores(query_tokens)

    max_score = max(raw_scores) if max(raw_scores) > 0 else 1.0
    scored = [
        (float(raw_scores[i]) / max_score, candidates[i][0], candidates[i][1])
        for i in range(len(candidates))
    ]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


# ── Index-strategy discovery ───────────────────────────────────────────────────

async def _discover_index(
    portal: "Portal",
    pillar: int,
    taxonomy: list[dict],
    known_urls: set[str],
    budget_deadline: float,
    known_titles: set[str] | None = None,
) -> list[tuple[str, str, str]] | None:
    """
    Fetch portal index_urls and return (title, url, discovery_tag) triples.

    Returns None on timeout/failure → caller falls back to seed KNOWN URLs.
    """
    index_urls: list[str] = getattr(portal, "index_urls", [])
    if not index_urls:
        logger.warning("[DISCOVER] portal '%s' has discovery:index but no index_urls", portal.name)
        return None

    known_norm = {_normalise_url(u) for u in known_urls}
    known_titles_norm = {normalise_title(t) for t in (known_titles or set())}
    exclude_titles, exclude_keywords = build_pillar_excludes(taxonomy, pillar)
    keywords = build_pillar_keywords(taxonomy, pillar)
    all_candidates: dict[str, tuple[str, str]] = {}  # norm_url → (title, abs_url)

    for idx_url in index_urls:
        if time.monotonic() > budget_deadline:
            logger.warning("[DISCOVER] budget exhausted before fetching %s", idx_url)
            break

        logger.info("[DISCOVER] fetching index %s", idx_url)
        html, status = await transport_fetch(idx_url, portal)

        if not html:
            logger.warning("[DISCOVER] index fetch failed (%d) for %s", status, idx_url)
            continue

        base = f"{urllib.parse.urlparse(idx_url).scheme}://{urllib.parse.urlparse(idx_url).netloc}"
        links = _parse_index_links(html, base)
        logger.info("[DISCOVER] parsed %d act links from %s", len(links), idx_url)

        for title, url in links:
            norm = _normalise_url(url)
            if norm not in all_candidates:
                all_candidates[norm] = (title, url)

    if not all_candidates:
        return None

    # Rank against pillar-scoped keywords
    candidate_list = list(all_candidates.values())
    scored = _rank_by_keywords(candidate_list, keywords)

    results: list[tuple[str, str, str]] = []
    for score, title, url in scored:
        norm = _normalise_url(url)
        # KNOWN if the URL OR the (normalised) title matches a Round 1 seed entry.
        # Round 1 KNOWN acts are ground truth — Companies/Income Tax/Banking carry
        # real storage/retention/secrecy provisions (RDTII 6.2 / 7.3 / 7.1) — so
        # they are NEVER excluded, even if they appear in a pillar's exclude list.
        is_known = (norm in known_norm) or (normalise_title(title) in known_titles_norm)
        if is_known:
            results.append((title, url, "KNOWN"))
            continue
        # Exclusion applies to NEW (non-seed) candidates only — drops noise such as
        # an unrelated customs/immigration act matched on a generic keyword.
        tl = title.lower()
        if any(x in tl for x in exclude_titles) or any(x in tl for x in exclude_keywords):
            logger.debug("[DISCOVER] excluded by taxonomy filter: %s", title)
            continue
        if score >= _NEW_SCORE_THRESHOLD:
            results.append((title, url, "NEW"))
        # Below threshold and not KNOWN → dropped

    return results


# ── Seed-only fallback ─────────────────────────────────────────────────────────

def _seed_fallback(
    portal: "Portal",
    economy_iso: str,
    known_urls: set[str],
    reason: str,
) -> list[tuple[str, str, str]]:
    """Return KNOWN seed URLs when discovery is unavailable or failed."""
    logger.warning("[DISCOVER] %s — falling back to seed KNOWN URLs (%s)", portal.name, reason)
    portal_domain = _registered_domain(str(portal.url))
    results: list[tuple[str, str, str]] = []
    for url in known_urls:
        if _registered_domain(url) == portal_domain:
            results.append(("", url, "KNOWN"))
    if not results:
        # No seed URLs for this portal — include all known urls
        results = [("", u, "KNOWN") for u in known_urls]
    return results


# ── Public API ─────────────────────────────────────────────────────────────────

async def discover(
    economy_config: "EconomyConfig",
    pillar: int,
    taxonomy: list[dict],
    known_urls: set[str],
    output_dir: str = "logs",
    known_titles: set[str] | None = None,
) -> list[Zone1Result]:
    """
    Pillar-agnostic per-economy portal discovery.

    For each portal:
      1. Check discovery strategy (index / seed_only / TBD)
      2. Run strategy-driven discovery with wall-clock budget
      3. Merge Round 1 KNOWN seeds (always retained)
      4. Rank by pillar-scoped keywords; drop NEW below threshold
      5. Cap output at ZONE2_MAX_ACTS

    Degrades gracefully to seed KNOWN URLs on failure/TBD, never hangs.
    Returns list[Zone1Result] ready for Zone 2.
    """
    economy_iso = economy_config.iso_code
    budget_deadline = time.monotonic() + _DISCOVER_BUDGET_S
    known_norm = {_normalise_url(u) for u in known_urls}

    all_results: list[tuple[str, str, str]] = []  # (title, url, discovery_tag)
    seen_norm: set[str] = set()

    for portal in economy_config.portals:
        discovery_strategy = getattr(portal, "discovery", "TBD")
        portal_name = portal.name

        if time.monotonic() > budget_deadline:
            logger.warning("[DISCOVER] global budget exhausted, skipping %s", portal_name)
            break

        logger.info("[DISCOVER] portal=%s strategy=%s", portal_name, discovery_strategy)

        if discovery_strategy == "index":
            raw = await _discover_index(portal, pillar, taxonomy, known_urls, budget_deadline, known_titles)
            if raw is None:
                raw = _seed_fallback(portal, economy_iso, known_urls, "index discovery failed")
        elif discovery_strategy == "seed_only":
            raw = _seed_fallback(portal, economy_iso, known_urls, "seed_only strategy")
        elif discovery_strategy == "TBD":
            logger.info("[DISCOVER] portal '%s' discovery=TBD — skipping (not yet implemented)", portal_name)
            continue
        else:
            # search / search_js / etc. not yet implemented → degrade
            raw = _seed_fallback(portal, economy_iso, known_urls, f"strategy '{discovery_strategy}' not implemented")

        for title, url, tag in raw:
            norm = _normalise_url(url)
            if norm not in seen_norm:
                seen_norm.add(norm)
                all_results.append((title, url, tag))

    # Ensure all KNOWN seed URLs are included even if not discovered from portals
    for url in known_urls:
        norm = _normalise_url(url)
        if norm not in seen_norm:
            seen_norm.add(norm)
            all_results.append(("", url, "KNOWN"))

    # Sort: KNOWN first, then NEW sorted by (insertion order = relevance rank)
    known_results = [(t, u, tag) for t, u, tag in all_results if tag == "KNOWN"]
    new_results   = [(t, u, tag) for t, u, tag in all_results if tag == "NEW"]

    # Keep ALL Round 1 KNOWN acts (up to ZONE2_MAX_KNOWN), then add up to
    # ZONE2_MAX_ACTS NEW discoveries. KNOWN are ground truth and must not be
    # truncated by the NEW cap.
    known_keep = known_results[:ZONE2_MAX_KNOWN]
    new_keep = new_results[:ZONE2_MAX_ACTS]
    combined = known_keep + new_keep
    cap = ZONE2_MAX_KNOWN

    logger.info(
        "[DISCOVER] %s P%d: %d KNOWN + %d NEW → %d acts (cap=%d)",
        economy_iso, pillar,
        len(known_results), len(new_results),
        len(combined), cap,
    )

    zone1_results: list[Zone1Result] = []
    for title, url, tag in combined:
        zone1_results.append(Zone1Result(
            url=url,
            economy=economy_iso,
            act_title=title,
            discovery_tag=tag,
            archive_url="",
        ))

    return zone1_results
