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
import re
import time
import urllib.parse
from typing import TYPE_CHECKING

import httpx
from bs4 import BeautifulSoup

from src.crawler.crawler import _normalise_url, _registered_domain
from src.crawler.seed_loader import normalise_title
from src.crawler.transport import _BROWSER_HEADERS
from src.crawler.transport import fetch as transport_fetch
from src.fetcher.models import Zone1Result

if TYPE_CHECKING:
    from src.config.economy_config import EconomyConfig, Portal

logger = logging.getLogger(__name__)

# ── Environment config ─────────────────────────────────────────────────────────

ZONE2_MAX_ACTS = int(os.getenv("ZONE2_MAX_ACTS", "5"))
# KNOWN seeds are Round 1 ground truth → fetch all of them, bounded only by a
# safety ceiling. The strict cap (ZONE2_MAX_NEW_ACTS) applies to speculative NEW
# discoveries only. ZONE2_MAX_ACTS is kept for back-compat as the NEW default.
_MAX_KNOWN_ACTS = int(os.getenv("ZONE2_MAX_KNOWN_ACTS", "12"))
# NEW discoveries default OFF for the Singapore build gate (reproducing Round 1
# ground truth is KNOWN-only; NEW acts add runtime + precision risk). Set
# ZONE2_MAX_NEW_ACTS>0 to re-enable speculative discovery.
_MAX_NEW_ACTS = int(os.getenv("ZONE2_MAX_NEW_ACTS", "0"))
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
    budget_deadline: float,
) -> list[tuple[str, str]] | None:
    """
    Index discovery adapter — fetch portal index_urls and emit raw (title, url)
    candidates. That is the adapter's ONLY job (D3): ranking, taxonomy exclusion,
    and KNOWN/NEW tagging are done once, for every adapter, by the shared
    `_rank_exclude_tag` tail in `discover()`.

    Returns None on empty/failure → caller falls back to seed KNOWN URLs.
    """
    index_urls: list[str] = getattr(portal, "index_urls", [])
    if not index_urls:
        logger.warning("[DISCOVER] portal '%s' has discovery:index but no index_urls", portal.name)
        return None

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

    return list(all_candidates.values())


async def _fetch_sitemap_xml(url: str) -> tuple[str, int]:
    """Fetch a sitemap.xml directly (own httpx call, not the transport ladder —
    that ladder's _is_real_response rejects any response without a >200-char
    <body>, which a well-formed sitemap has neither of). Isolated as its own
    function so the offline golden harness can mock it to a pinned fixture.
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
            resp = await client.get(url, headers=_BROWSER_HEADERS)
        return resp.text, resp.status_code
    except Exception as exc:
        logger.warning("[DISCOVER] sitemap fetch error for %s: %s", url, exc)
        return "", 0


def _slug_to_title(url: str) -> str:
    """Derive a human title from a URL's last path segment for BM25 ranking.

    "…/data-protection-obligations" → "data protection obligations".
    Sitemap <loc> entries carry no titles, so the slug is the only text signal.
    """
    path = urllib.parse.urlparse(url).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1] if path else ""
    return re.sub(r"[-_]+", " ", slug).strip()


async def _discover_sitemap(
    portal: "Portal",
    budget_deadline: float,
) -> list[tuple[str, str]] | None:
    """
    Sitemap discovery adapter — for JS-rendered portals (SPAs) that expose a
    standard sitemap.xml but have no crawlable HTML browse index (e.g. pdpc.gov.sg).
    Fetches the sitemap, extracts every <loc> page URL, and derives a title from
    each URL slug. Ranking / exclusion / KNOWN-NEW tagging are done by the shared
    `_rank_exclude_tag` tail, exactly like every other adapter (D3).

    Returns None on empty/failure → caller falls back to seed KNOWN URLs.
    """
    sitemap_url = getattr(portal, "sitemap_url", None)
    if not sitemap_url:
        logger.warning("[DISCOVER] portal '%s' has discovery:sitemap but no sitemap_url", portal.name)
        return None

    if time.monotonic() > budget_deadline:
        logger.warning("[DISCOVER] budget exhausted before fetching %s", sitemap_url)
        return None

    logger.info("[DISCOVER] fetching sitemap %s", sitemap_url)
    xml, status = await _fetch_sitemap_xml(sitemap_url)
    if status != 200 or not xml:
        logger.warning("[DISCOVER] sitemap fetch failed (%d) for %s", status, sitemap_url)
        return None

    # Robust to namespaces/attributes: match <loc> text directly. A nested sitemap
    # index (<loc> ending .xml) is skipped rather than recursed — flat sitemaps are
    # the common gov case; add recursion only if a portal needs it.
    locs = [u.strip() for u in re.findall(r"<loc>\s*(.*?)\s*</loc>", xml, re.DOTALL | re.IGNORECASE)]
    candidates: dict[str, tuple[str, str]] = {}  # norm_url → (title, url)
    for url in locs:
        if not url.lower().startswith("http") or url.lower().endswith(".xml"):
            continue
        norm = _normalise_url(url)
        if norm not in candidates:
            candidates[norm] = (_slug_to_title(url), url)

    logger.info("[DISCOVER] parsed %d page URLs from sitemap %s", len(candidates), sitemap_url)
    return list(candidates.values()) or None


# ── Shared rank + exclude + KNOWN/NEW tag (adapter-agnostic) ─────────────────────

def _rank_exclude_tag(
    candidates: list[tuple[str, str]],
    pillar: int,
    taxonomy: list[dict],
    known_urls: set[str],
    known_titles: set[str] | None = None,
) -> list[tuple[str, str, str]]:
    """
    Turn raw (title, url) candidates from ANY discovery adapter into tagged
    (title, url, discovery_tag) triples. This is the universal tail every economy
    gets for free (D3): BM25 rank against pillar keywords → taxonomy exclusion →
    KNOWN/NEW tag → NEW threshold drop. Runs per portal so each adapter's BM25
    corpus is unchanged from the pre-refactor behaviour.
    """
    known_norm = {_normalise_url(u) for u in known_urls}
    known_titles_norm = {normalise_title(t) for t in (known_titles or set())}
    exclude_titles, exclude_keywords = build_pillar_excludes(taxonomy, pillar)
    keywords = build_pillar_keywords(taxonomy, pillar)

    scored = _rank_by_keywords(candidates, keywords)

    results: list[tuple[str, str, str]] = []
    for score, title, url in scored:
        norm = _normalise_url(url)
        tl = title.lower()
        # Exclusion wins over everything — the pillar's exclude_act_titles are the
        # curated "never relevant" list, and the Round 1 seed itself includes
        # negative-example acts (banking/tax/companies/etc.) for SG P7. Applying
        # the filter BEFORE the KNOWN check stops those from being mapped.
        if any(x in tl for x in exclude_titles) or any(x in tl for x in exclude_keywords):
            logger.debug("[DISCOVER] excluded by taxonomy filter: %s", title)
            continue
        # KNOWN if the URL OR the (normalised) title matches a Round 1 seed entry.
        # Round 1 DB rows often carry titles but no act-level URL, so URL-only
        # matching would mis-tag known acts (e.g. the PDPA) as NEW.
        is_known = (norm in known_norm) or (normalise_title(title) in known_titles_norm)
        if is_known:
            results.append((title, url, "KNOWN"))
            continue
        if score >= _NEW_SCORE_THRESHOLD:
            results.append((title, url, "NEW"))
        # Below threshold and not KNOWN → dropped

    return results


# ── API-strategy discovery (OData / JSON) ───────────────────────────────────────

_API_STOPWORDS = {
    "and", "the", "for", "with", "from", "that", "this", "other", "under",
    "into", "act", "acts", "law", "laws", "regulation", "regulations",
}


def _api_search_terms(keywords: list[str], cap: int = 12) -> list[str]:
    """Distinct significant single words from pillar keywords, for name-contains
    API queries (multi-word phrases rarely appear verbatim in act names)."""
    out: list[str] = []
    seen: set[str] = set()
    for kw in keywords:
        for w in re.split(r"[^a-z0-9]+", kw.lower()):
            if len(w) >= 4 and w not in _API_STOPWORDS and w not in seen:
                seen.add(w)
                out.append(w)
                if len(out) >= cap:
                    return out
    return out


async def _api_titles_contains(
    client: "httpx.AsyncClient", api_base: str, collection: str, term: str, top: int = 50,
) -> list[dict]:
    """One OData query: in-force titles in `collection` whose name contains `term`."""
    crit = urllib.parse.quote(f"and(collection({collection}),status(InForce))")
    filt = urllib.parse.quote(f"contains(name,'{term}')")
    url = (
        f"{api_base}/titles/search(criteria='{crit}')"
        f"?$filter={filt}&$select=id,name&$top={top}"
    )
    try:
        resp = await client.get(url, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            logger.warning("[DISCOVER] api query HTTP %d for term '%s'", resp.status_code, term)
            return []
        return resp.json().get("value", [])
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("[DISCOVER] api query failed for term '%s': %s", term, exc)
        return []


async def _discover_api(
    portal: "Portal",
    pillar: int,
    taxonomy: list[dict],
    budget_deadline: float,
) -> list[tuple[str, str]] | None:
    """
    API discovery adapter — query a JSON/OData legislation API for pillar-relevant
    titles and emit raw (name, url) candidates. Like every adapter (D3), it only
    lists candidates; the shared `_rank_exclude_tag` tail ranks + tags them.

    Queries `contains(name, term)` for each significant pillar keyword term and
    unions the results (deduped by title id). Emits the canonical act URL
    `{portal.url}/{titleId}`, which `_normalise_url` lowercases to match the
    Round 1 seed form (`.../c2004a03712`). Returns None on empty/no-config.
    """
    api_base = getattr(portal, "api_base", None)
    if not api_base:
        logger.warning("[DISCOVER] portal '%s' has discovery:api but no api_base", portal.name)
        return None
    collection = getattr(portal, "api_collection", None) or "Act"
    terms = _api_search_terms(build_pillar_keywords(taxonomy, pillar))
    if not terms:
        return None

    portal_base = str(portal.url).rstrip("/")
    candidates: dict[str, tuple[str, str]] = {}  # titleId → (name, url)
    async with httpx.AsyncClient(timeout=_INDEX_FETCH_TIMEOUT_S, follow_redirects=True) as client:
        for term in terms:
            if time.monotonic() > budget_deadline:
                logger.warning("[DISCOVER] budget exhausted during api discovery (term=%s)", term)
                break
            rows = await _api_titles_contains(client, api_base, collection, term)
            logger.info("[DISCOVER] api term '%s' → %d titles", term, len(rows))
            for row in rows:
                tid = row.get("id")
                if tid and tid not in candidates:
                    candidates[tid] = (row.get("name", ""), f"{portal_base}/{tid}")

    if not candidates:
        return None
    return list(candidates.values())


# ── Auto discovery (best-effort zero-config safety net) ─────────────────────────

async def _render_spa(url: str) -> str:
    """Best-effort JS render of a SPA page via stealth Playwright ('' on failure)."""
    from src.crawler.transport import _fetch_playwright
    try:
        html, _status = await _fetch_playwright(url)
        return html or ""
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[DISCOVER] auto render failed for %s: %s", url, exc)
        return ""


async def _discover_auto(
    portal: "Portal",
    budget_deadline: float,
) -> list[tuple[str, str]] | None:
    """
    Zero-config best-effort discovery adapter (D7 / ADR-046). For each start URL
    (declared index_urls, else the portal root): fetch static HTML, classify it
    SSR vs SPA with the shared probe, render SPAs with Playwright, then extract
    same-domain candidate links. Emits raw (title, url) → shared `_rank_exclude_tag`.

    NOT a reliable universal crawler — it grabs whatever it can so an undeclared
    portal still yields *something*; declare a real strategy (index/api) for
    production quality. Returns None when nothing is found.
    """
    from src.crawler.crawler import _extract_act_links
    from src.crawler.spa_probe import classify_render

    start_urls = list(getattr(portal, "index_urls", []) or []) or [str(portal.url).rstrip("/")]
    portal_domain = _registered_domain(str(portal.url))
    all_candidates: dict[str, tuple[str, str]] = {}

    for url in start_urls:
        if time.monotonic() > budget_deadline:
            logger.warning("[DISCOVER] budget exhausted during auto discovery (%s)", url)
            break
        html, _status = await transport_fetch(url, portal)
        render = classify_render(html) if html else None
        if (not html) or (render is not None and render.is_spa):
            logger.info(
                "[DISCOVER] auto: %s is %s → rendering with Playwright",
                url, render.mode if render else "unreachable",
            )
            rendered = await _render_spa(url)
            if rendered:
                html = rendered
        if not html:
            continue
        for link in _extract_act_links(html, url, portal_domain):
            norm = _normalise_url(link["url"])
            if norm not in all_candidates:
                all_candidates[norm] = (link["title"], link["url"])

    if not all_candidates:
        return None
    logger.warning(
        "[DISCOVER] portal '%s' ran on AUTO best-effort (%d candidates) — declare a "
        "discovery strategy (index/api) for production quality",
        portal.name, len(all_candidates),
    )
    return list(all_candidates.values())


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


# ── Indicator-aware act selection ───────────────────────────────────────────────

def _indicator_aware_select(
    known_results: list[tuple[str, str, str]],
    titles_by_indicator: dict[str, set[str]],
    cap: int,
) -> list[tuple[str, str, str]]:
    """
    Pick up to `cap` known acts, spreading slots across indicators.

    Round-robin: each pass takes the next-highest-ranked unselected act for every
    indicator in turn, so one indicator's many seed acts (P7-I3 has 5: PDPA,
    Telecom, Companies, Income Tax, Employment) don't crowd the others out. Acts
    with no indicator mapping fill any leftover slots. Discovery rank order is
    preserved within each indicator. Falls back to plain rank order when no
    mapping is available.
    """
    if not titles_by_indicator or len(known_results) <= cap:
        return known_results[:cap]

    title_to_inds: dict[str, list[str]] = {}
    for ind, titles in titles_by_indicator.items():
        for t in titles:
            title_to_inds.setdefault(t, []).append(ind)

    queues: dict[str, list[tuple[str, str, str]]] = {}
    unmapped: list[tuple[str, str, str]] = []
    for item in known_results:
        inds = title_to_inds.get(normalise_title(item[0]))
        if inds:
            for ind in inds:
                queues.setdefault(ind, []).append(item)
        else:
            unmapped.append(item)

    selected: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    while len(selected) < cap and any(queues.values()):
        for ind in sorted(queues.keys()):
            if len(selected) >= cap:
                break
            q = queues[ind]
            while q:
                item = q.pop(0)
                key = _normalise_url(item[1])
                if key not in seen:
                    selected.append(item)
                    seen.add(key)
                    break
    for item in unmapped:
        if len(selected) >= cap:
            break
        key = _normalise_url(item[1])
        if key not in seen:
            selected.append(item)
            seen.add(key)
    return selected


# ── Public API ─────────────────────────────────────────────────────────────────

async def discover(
    economy_config: "EconomyConfig",
    pillar: int,
    taxonomy: list[dict],
    known_urls: set[str],
    output_dir: str = "logs",
    known_titles: set[str] | None = None,
    known_titles_by_indicator: dict[str, set[str]] | None = None,
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
            candidates = await _discover_index(portal, budget_deadline)
            if candidates is None:
                raw = _seed_fallback(portal, economy_iso, known_urls, "index discovery failed")
            else:
                raw = _rank_exclude_tag(candidates, pillar, taxonomy, known_urls, known_titles)
        elif discovery_strategy == "api":
            candidates = await _discover_api(portal, pillar, taxonomy, budget_deadline)
            if candidates is None:
                raw = _seed_fallback(portal, economy_iso, known_urls, "api discovery failed")
            else:
                raw = _rank_exclude_tag(candidates, pillar, taxonomy, known_urls, known_titles)
        elif discovery_strategy == "sitemap":
            candidates = await _discover_sitemap(portal, budget_deadline)
            if candidates is None:
                raw = _seed_fallback(portal, economy_iso, known_urls, "sitemap discovery failed")
            else:
                raw = _rank_exclude_tag(candidates, pillar, taxonomy, known_urls, known_titles)
        elif discovery_strategy == "auto":
            candidates = await _discover_auto(portal, budget_deadline)
            if candidates is None:
                raw = _seed_fallback(portal, economy_iso, known_urls, "auto discovery found nothing")
            else:
                raw = _rank_exclude_tag(candidates, pillar, taxonomy, known_urls, known_titles)
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

    # Fetch ALL KNOWN ground-truth seeds (bounded by a safety ceiling; if there
    # are more than the ceiling, indicator-aware selection keeps the spread fair).
    # NEW discoveries get a strict cap — and are dropped entirely if discovery
    # already consumed most of the wall-clock budget (runtime guard).
    known_selected = _indicator_aware_select(
        known_results, known_titles_by_indicator or {}, _MAX_KNOWN_ACTS
    )
    budget_left = budget_deadline - time.monotonic()
    if budget_left <= 0:
        new_selected: list[tuple[str, str, str]] = []
    else:
        new_selected = new_results[:_MAX_NEW_ACTS]
    combined = known_selected + new_selected

    logger.info(
        "[DISCOVER] %s P%d: %d KNOWN (→%d, ceiling=%d) + %d NEW (→%d, cap=%d) = %d acts; selected=%s",
        economy_iso, pillar,
        len(known_results), len(known_selected), _MAX_KNOWN_ACTS,
        len(new_results), len(new_selected), _MAX_NEW_ACTS,
        len(combined),
        [t or u for t, u, _ in combined],
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
