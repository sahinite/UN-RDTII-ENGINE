"""
Auto-probe portal discovery. [Z1-2]

For each portal in the economy YAML, sends all indicator keyword sets to the
portal's search endpoint and returns a ranked list of portals that have
non-zero hits. Zero-result and failed portals are filtered before being
passed to crawler.py.

Public API:
    run_probe(economy_config, taxonomy, output_dir) -> list[ProbeResult]
    validate_taxonomy(taxonomy) -> None
    load_taxonomy(path) -> list[dict]
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, urljoin

import httpx
from bs4 import BeautifulSoup

from src.config.economy_config import EconomyConfig
from src.crawler.exceptions import ConfigError, ProbeError

logger = logging.getLogger(__name__)

# ── Environment config ─────────────────────────────────────────────────────────

_PROBE_JITTER_MS = int(os.getenv("PROBE_JITTER_MS", "300"))
_PROBE_TIMEOUT_SEC = float(os.getenv("PROBE_TIMEOUT_SEC", "10"))

# DeepL cost: $5 per 1M chars (pro plan)
_DEEPL_COST_PER_CHAR = 5.0 / 1_000_000

_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4.1 Safari/605.1.15"
    ),
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# DeepL uses uppercase language codes with some variations
_DEEPL_LANG_MAP: dict[str, str] = {
    "ms": "MS",
    "th": "TH",
    "zh": "ZH",
    "hi": "HI",
    "id": "ID",
    "ko": "KO",
    "ja": "JA",
    "fr": "FR",
    "de": "DE",
    "es": "ES",
    "pt": "PT-PT",
}

# Patterns to detect a result count in search result page text
_RESULT_COUNT_PATTERNS = [
    re.compile(r"([\d,]+)\s+results?", re.IGNORECASE),
    re.compile(r"([\d,]+)\s+acts?\s+found", re.IGNORECASE),
    re.compile(r"showing\s+\d+[–\-]\d+\s+of\s+([\d,]+)", re.IGNORECASE),
    re.compile(r"found\s+([\d,]+)", re.IGNORECASE),
    re.compile(r"([\d,]+)\s+match(?:es)?", re.IGNORECASE),
    re.compile(r"([\d,]+)\s+records?", re.IGNORECASE),
    re.compile(r"([\d,]+)\s+laws?", re.IGNORECASE),
    re.compile(r"([\d,]+)\s+legislat", re.IGNORECASE),
]


# ── Data classes ───────────────────────────────────────────────────────────────


@dataclass
class ProbeRawResult:
    """Raw result from probing a single (portal, keyword) pair."""

    portal_url: str
    keyword: str
    hit_count: int
    status: str  # "ok" | "timeout" | "error" | "zero"
    result_urls: list[str] = field(default_factory=list)


@dataclass
class ProbeResult:
    """Aggregated probe result for one portal, consumed by crawler.py."""

    url: str
    portal_name: str
    portal_type: str    # "primary" | "secondary"
    total_hit_count: int
    is_active: bool     # True if total_hit_count > 0
    language: str       # ISO 639-1 primary language of this portal
    probe_status: str   # "ok" | "partial" | "failed"


# ── Translation (Layer 1) ──────────────────────────────────────────────────────

# In-session translation cache: keyed by "{keyword}_{target_lang}"
_translation_memory: dict[str, str] = {}


def _load_translation_cache(lang: str, cache_dir: str = "cache") -> None:
    path = Path(cache_dir) / f"probe_keyword_translations_{lang}.json"
    if path.exists():
        try:
            _translation_memory.update(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except Exception as exc:
            logger.warning(f"Could not load translation cache: {exc}")


def _save_translation_cache(lang: str, cache_dir: str = "cache") -> None:
    path = Path(cache_dir) / f"probe_keyword_translations_{lang}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_translation_memory, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _log_translation_cost(provider: str, chars: int, cost_usd: float) -> None:
    event = {
        "component": "probe_translation",
        "provider": provider,
        "chars": chars,
        "cost_usd": cost_usd,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    Path("logs").mkdir(exist_ok=True)
    with open("logs/cost_events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def _translate_keyword(keyword: str, target_lang: str, provider: str | None) -> str:
    """
    Translate a single keyword to target_lang.
    Tries DeepL first (if DEEPL_API_KEY is set), falls back to Google Translate.
    Results are cached in _translation_memory.
    """
    cache_key = f"{keyword}_{target_lang}"
    if cache_key in _translation_memory:
        return _translation_memory[cache_key]

    translated: str | None = None
    api_key = os.getenv("DEEPL_API_KEY", "")

    if api_key:
        try:
            import deepl as deepl_module  # lazy import — optional dependency

            deepl_lang = _DEEPL_LANG_MAP.get(target_lang, target_lang.upper())
            translator = deepl_module.Translator(api_key)
            result = translator.translate_text(keyword, target_lang=deepl_lang)
            translated = result.text
            _log_translation_cost("deepl", len(keyword), len(keyword) * _DEEPL_COST_PER_CHAR)
            logger.debug(f"DeepL: '{keyword}' → '{translated}' ({target_lang})")
        except Exception as exc:
            logger.warning(f"DeepL translation failed ({exc}). Falling back to Google Translate.")

    if translated is None:
        try:
            from googletrans import Translator as GTranslator  # lazy import

            gt = GTranslator()
            result = gt.translate(keyword, dest=target_lang)
            translated = result.text
            _log_translation_cost("google", len(keyword), 0.0)
            logger.debug(f"Google Translate: '{keyword}' → '{translated}' ({target_lang})")
        except Exception as exc:
            logger.warning(f"Google Translate also failed ({exc}). Using original keyword.")
            translated = keyword

    _translation_memory[cache_key] = translated
    return translated


def translate_keywords(keywords: list[str], economy: EconomyConfig) -> list[str]:
    """
    Return keywords translated to the economy's primary language if needed.
    No-op for English-only economies (translation_provider is None).
    """
    if economy.translation_provider is None:
        return keywords  # English-only economy — zero translation calls

    # Primary non-English language (first non-'en' entry, or first if all English)
    primary_lang = next(
        (lang for lang in economy.languages if lang != "en"),
        economy.languages[0],
    )
    if primary_lang == "en":
        return keywords

    _load_translation_cache(primary_lang)
    translated = [
        _translate_keyword(kw, primary_lang, economy.translation_provider)
        for kw in keywords
    ]
    _save_translation_cache(primary_lang)
    return translated


# ── HTML parsing helpers ───────────────────────────────────────────────────────


def _extract_result_count(soup: BeautifulSoup) -> int | None:
    """Try to parse a numeric result count from page text. Returns None if not found."""
    text = soup.get_text(" ", strip=True)
    for pat in _RESULT_COUNT_PATTERNS:
        m = pat.search(text)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


def _extract_result_urls(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Extract all unique, non-anchor href links from the page."""
    seen: set[str] = set()
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        full = urljoin(base_url, href)
        if full.startswith("http") and full not in seen:
            seen.add(full)
            urls.append(full)
    return urls


def _is_js_rendered(html: str) -> bool:
    """Return True if the response looks like a JS SPA with no server-side content."""
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body
    if body is None:
        return True
    body_text = body.get_text(strip=True)
    if len(body_text) < 200:
        return True
    # Common SPA root markers
    spa_markers = ('id="root"', "id='root'", 'id="app"', "id='app'")
    if any(m in html for m in spa_markers) and len(body_text) < 500:
        return True
    # Explicit JS requirement messages
    js_required = ("You need to enable JavaScript", "Please enable JavaScript")
    if any(msg in html for msg in js_required):
        return True
    return False


def _parse_search_page(html: str, base_url: str) -> tuple[int, list[str]]:
    """
    Parse a search results page.
    Returns (hit_count, result_urls) where hit_count is text-based if found,
    else len(result_urls).
    """
    soup = BeautifulSoup(html, "html.parser")
    result_urls = _extract_result_urls(soup, base_url)
    text_count = _extract_result_count(soup)
    hit_count = text_count if text_count is not None else len(result_urls)
    return hit_count, result_urls


# ── Search URL construction ────────────────────────────────────────────────────


def _build_search_url(
    portal_url: str, keyword: str, search_url_pattern: str | None
) -> str:
    """Build the search URL for a given portal and keyword."""
    if search_url_pattern:
        return search_url_pattern.format(keyword=quote_plus(keyword))
    base = portal_url.rstrip("/")
    return f"{base}/search?q={quote_plus(keyword)}"


# ── HTTP probing (ST2) ─────────────────────────────────────────────────────────


async def _jitter() -> None:
    """Random wait to reduce anti-bot detection risk."""
    await asyncio.sleep(random.randint(100, _PROBE_JITTER_MS) / 1000)


async def _probe_with_httpx(search_url: str) -> tuple[int, list[str], str]:
    """
    Probe a search URL with httpx.
    Returns (hit_count, result_urls, status).
    status == "js_rendered" signals caller to fall back to Playwright.
    """
    ua = random.choice(_USER_AGENTS)
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=_PROBE_TIMEOUT_SEC
        ) as client:
            resp = await client.get(search_url, headers={"User-Agent": ua})

        if resp.status_code in (403, 429):
            logger.warning(f"HTTP {resp.status_code} from {search_url} — skipping portal")
            return 0, [], "error"
        if resp.status_code != 200:
            logger.warning(f"HTTP {resp.status_code} from {search_url}")
            return 0, [], "error"

        html = resp.text
        if _is_js_rendered(html):
            logger.info(f"JS-rendered response detected at {search_url} — retrying with Playwright")
            return -1, [], "js_rendered"

        count, urls = _parse_search_page(html, str(resp.url))
        status = "ok" if count > 0 else "zero"
        return count, urls, status

    except httpx.TimeoutException:
        logger.warning(f"Timeout probing {search_url}")
        return 0, [], "timeout"
    except Exception as exc:
        logger.warning(f"httpx probe error for {search_url}: {exc}")
        return 0, [], "error"


async def _probe_with_playwright(portal_url: str, keyword: str) -> tuple[int, list[str], str]:
    """
    Use Crawl4AI (Playwright) to probe a JS-rendered portal search page.
    Returns (hit_count, result_urls, status).

    Delegates to crawl4ai_runner.probe_js_page — all Crawl4AI imports are
    centralised there (see src/crawler/crawl4ai_runner.py).
    """
    from src.crawler.crawl4ai_runner import probe_js_page  # noqa: PLC0415

    search_url = _build_search_url(portal_url, keyword, None)
    try:
        html, success = await probe_js_page(search_url)
        if not success:
            logger.warning(f"Playwright crawl failed for {search_url}")
            return 0, [], "error"
        count, urls = _parse_search_page(html, search_url)
        return count, urls, "ok" if count > 0 else "zero"
    except Exception as exc:
        logger.warning(f"Playwright probe failed for {portal_url}: {exc}")
        return 0, [], "error"


async def _probe_portal_keyword(
    portal_url: str,
    keyword: str,
    search_url_pattern: str | None,
) -> ProbeRawResult:
    """
    Probe one portal for one keyword.
    Tries httpx first; if JS-rendered, falls back to Playwright automatically.
    """
    await _jitter()
    search_url = _build_search_url(portal_url, keyword, search_url_pattern)
    hit_count, result_urls, status = await _probe_with_httpx(search_url)

    if status == "js_rendered":
        hit_count, result_urls, status = await _probe_with_playwright(
            portal_url, keyword
        )

    return ProbeRawResult(
        portal_url=portal_url,
        keyword=keyword,
        hit_count=max(0, hit_count),
        status=status,
        result_urls=result_urls,
    )


async def _probe_base_url_only(portal_url: str) -> list[ProbeRawResult]:
    """
    For portals without a search_url_pattern: check base URL reachability only.
    Returns a single ProbeRawResult — 'ok' if the portal responds, 'error' otherwise.
    """
    await _jitter()
    ua = random.choice(_USER_AGENTS)
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=_PROBE_TIMEOUT_SEC) as client:
            resp = await client.get(portal_url, headers={"User-Agent": ua})
        reachable = resp.status_code < 400
    except Exception:
        reachable = False

    status = "ok" if reachable else "error"
    logger.info("Base URL probe %s → %s (%s)", portal_url, status,
                "reachable" if reachable else "unreachable")
    return [ProbeRawResult(
        portal_url=portal_url,
        keyword="",
        hit_count=1 if reachable else 0,
        status=status,
        result_urls=[],
    )]


async def _probe_portal_all_keywords(
    portal_url: str,
    search_url_pattern: str | None,
    keywords: list[str],
) -> list[ProbeRawResult]:
    """Probe one portal against all keywords (sequential to respect jitter).

    Early-exit on consecutive 403/error responses — portal is blocked, no
    point spawning Playwright for every remaining keyword.
    """
    results = []
    consecutive_errors = 0
    _EARLY_EXIT_THRESHOLD = 2

    for kw in keywords:
        raw = await _probe_portal_keyword(portal_url, kw, search_url_pattern)
        results.append(raw)

        if raw.status == "error":
            consecutive_errors += 1
            if consecutive_errors >= _EARLY_EXIT_THRESHOLD:
                logger.warning(
                    "%d consecutive errors from %s — skipping remaining %d keywords",
                    consecutive_errors, portal_url, len(keywords) - len(results),
                )
                break
        else:
            consecutive_errors = 0

    return results


# ── Aggregation & ranking (ST3) ────────────────────────────────────────────────


def _aggregate_portal_results(
    raw: list[ProbeRawResult],
    portal_name: str,
    portal_type: str,
    language: str,
) -> ProbeResult:
    """
    Aggregate raw keyword results for one portal into a ProbeResult.
    Deduplicates result URLs across keyword queries (same URL in two keyword
    results counted only once).
    """
    url = raw[0].portal_url if raw else ""
    ok_count = sum(1 for r in raw if r.status == "ok")
    total = len(raw)

    # Collect unique result URLs across all ok-status keyword probes
    seen_urls: set[str] = set()
    url_based_count = 0
    text_only_count = 0

    for r in raw:
        if r.status != "ok":
            continue
        if r.result_urls:
            new_urls = [u for u in r.result_urls if u not in seen_urls]
            seen_urls.update(new_urls)
            url_based_count += len(new_urls)
        else:
            # No URLs extracted (text-count only) — add directly (may double-count)
            text_only_count += r.hit_count

    total_hits = url_based_count + text_only_count

    if ok_count == total:
        probe_status = "ok"
    elif ok_count == 0:
        probe_status = "failed"
    else:
        probe_status = "partial"

    return ProbeResult(
        url=url,
        portal_name=portal_name,
        portal_type=portal_type,
        total_hit_count=total_hits,
        is_active=total_hits > 0,
        language=language,
        probe_status=probe_status,
    )


def _rank_portals(results: list[ProbeResult]) -> list[ProbeResult]:
    """Sort by total_hit_count descending; primary portal wins on ties."""

    def _sort_key(r: ProbeResult) -> tuple[int, int]:
        type_rank = 0 if r.portal_type == "primary" else 1
        return (-r.total_hit_count, type_rank)

    return sorted(results, key=_sort_key)


# ── Structured logging (ST4) ───────────────────────────────────────────────────


def _write_probe_logs(
    all_results: list[ProbeResult],
    economy_name: str,
    output_dir: str,
) -> None:
    """Write probe_skip_*.jsonl and probe_summary_*.json to output_dir."""
    log_dir = Path(output_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    skipped = [r for r in all_results if not r.is_active]
    active = [r for r in all_results if r.is_active]

    if skipped:
        skip_path = log_dir / f"probe_skip_{economy_name}_{ts}.jsonl"
        with skip_path.open("w", encoding="utf-8") as f:
            for r in skipped:
                reason = (
                    "all_probes_failed" if r.probe_status == "failed"
                    else "zero_results"
                )
                entry = {
                    "portal_url": r.url,
                    "portal_name": r.portal_name,
                    "reason": reason,
                    "economy": economy_name,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                f.write(json.dumps(entry) + "\n")

    summary = {
        "economy": economy_name,
        "total_portals_checked": len(all_results),
        "active_portals": len(active),
        "skipped_portals": len(skipped),
        "active_portal_urls": [r.url for r in active],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    summary_path = log_dir / f"probe_summary_{economy_name}_{ts}.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    logger.info(
        f"[PROBE] {economy_name}: {len(active)}/{len(all_results)} portals active."
        f" {len(skipped)} skipped (zero results)."
    )


# ── Taxonomy helpers ───────────────────────────────────────────────────────────


def validate_taxonomy(taxonomy: list[dict]) -> None:
    """
    Validate all indicators have a non-empty probe_keywords list.
    Raises ConfigError with the offending indicator_id if any are missing.
    """
    for indicator in taxonomy:
        iid = indicator.get("indicator_id", "<unknown>")
        keywords = indicator.get("probe_keywords")
        if not keywords or not isinstance(keywords, list) or len(keywords) == 0:
            raise ConfigError(
                f"Indicator '{iid}' is missing 'probe_keywords' in taxonomy.json. "
                "Add a non-empty probe_keywords array for every indicator."
            )


def load_taxonomy(taxonomy_path: str = "taxonomy.json") -> list[dict]:
    """Load and return taxonomy.json as a list of indicator dicts."""
    path = Path(taxonomy_path)
    if not path.exists():
        raise FileNotFoundError(f"taxonomy.json not found at '{path.resolve()}'")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("taxonomy.json must be a JSON array of indicator objects")
    return data


# ── Public API (ST6) ──────────────────────────────────────────────────────────


async def run_probe(
    economy_config: EconomyConfig,
    taxonomy: list[dict],
    output_dir: str = "logs",
) -> list[ProbeResult]:
    """
    Probe all portals in the economy config against all indicator keywords.

    Returns only active portals (is_active=True), sorted by total_hit_count
    descending (primary portal type wins on ties).

    Raises:
        ConfigError  — taxonomy missing probe_keywords for any indicator
        ProbeError   — all portals returned zero results
    """
    validate_taxonomy(taxonomy)

    # Collect all unique keywords from all indicators
    all_keywords: list[str] = []
    seen: set[str] = set()
    for indicator in taxonomy:
        for kw in indicator.get("probe_keywords", []):
            if kw not in seen:
                seen.add(kw)
                all_keywords.append(kw)

    # Layer 1 translation for non-English portals
    keywords = translate_keywords(all_keywords, economy_config)

    # Determine primary language for ProbeResult.language
    primary_language = economy_config.languages[0]

    # Probe each portal
    all_results: list[ProbeResult] = []
    for portal in economy_config.portals:
        portal_url = str(portal.url)

        if not portal.search_url_pattern:
            # No search pattern — just check base URL reachability (one request).
            # Full keyword scanning would probe the same URL 44+ times pointlessly.
            raw = await _probe_base_url_only(portal_url)
        else:
            raw = await _probe_portal_all_keywords(
                portal_url,
                portal.search_url_pattern,
                keywords,
            )
        result = _aggregate_portal_results(
            raw,
            portal_name=portal.name,
            portal_type=portal.type,
            language=primary_language,
        )
        all_results.append(result)

    # Release the shared Chromium started by probe_js_page — the crawler stage
    # runs in a separate event loop and must start its own browser.
    from src.crawler.crawl4ai_runner import close_shared_crawler  # noqa: PLC0415
    await close_shared_crawler()

    _write_probe_logs(all_results, economy_config.economy_name, output_dir)

    ranked = _rank_portals(all_results)
    active = [r for r in ranked if r.is_active]

    if not active:
        raise ProbeError(
            f"All {len(all_results)} portal(s) returned zero results for economy "
            f"'{economy_config.economy_name}'. "
            "Check that the portal URLs are correct and the portals are accessible."
        )

    return active
