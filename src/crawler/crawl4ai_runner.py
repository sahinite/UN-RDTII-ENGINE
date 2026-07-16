"""
Crawl4AI integration (Playwright-based, MIT license).

Dedicated wrapper around Crawl4AI/Playwright for JS-rendered government portals.
Extracted from crawler.py so it can be imported, swapped, or mocked independently.

Chosen over Scrapy/custom crawler for JS-rendering support — see
RDTII_Engine_Technical_Plan_v2.docx §6 Technology Stack.

Anti-blocking: government portals (notably Singapore SSO) fingerprint headless
browsers and return 403 / serve an empty shell. We counter with Crawl4AI's
stealth + "magic" mode (patches navigator.webdriver, simulates a real user,
realistic UA + headers). See _stealth_browser_config / _build_run_config.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

# ── Module-level defaults (used when a portal doesn't declare its own values) ──

# Default wait selector for Act-listing pages — portals that need a different
# selector should set playwright_wait_for in their YAML Portal entry.
SSO_WAIT_FOR = "css:a[href*='/Act/']"
SSO_TIMEOUT_MS = 20000

# A realistic desktop Chrome fingerprint. Headless Chromium's default UA leaks
# "HeadlessChrome", which several gov portals block outright.
_REAL_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_REAL_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


# ── Config builders (stealth / anti-bot) ───────────────────────────────────────

def _stealth_browser_config(headless: bool = True):
    """BrowserConfig tuned to look like a real desktop Chrome session."""
    from crawl4ai import BrowserConfig  # type: ignore[import]

    return BrowserConfig(
        headless=headless,
        enable_stealth=True,            # patch navigator.webdriver, plugins, etc.
        user_agent=_REAL_USER_AGENT,
        headers=_REAL_HEADERS,
        viewport_width=1366,
        viewport_height=900,
        ignore_https_errors=True,
    )


def _build_run_config(wait_for: str | None, timeout_ms: int):
    """CrawlerRunConfig with magic mode (anti-bot) and optional wait selector.

    NOTE: ``magic=True`` already bundles user-simulation and navigator spoofing.
    We deliberately omit ``simulate_user`` / ``mean_delay`` here — those add
    several seconds of synthetic mouse movement per page, which across a
    multi-page run turns a 1-minute fetch into 30+ minutes.
    """
    from crawl4ai import CacheMode, CrawlerRunConfig  # type: ignore[import]

    kwargs = dict(
        cache_mode=CacheMode.BYPASS,
        page_timeout=timeout_ms,
        magic=True,                 # bundle of anti-detection tricks
        override_navigator=True,    # spoof navigator props
        verbose=False,
    )
    if wait_for:
        kwargs["wait_for"] = wait_for
    return CrawlerRunConfig(**kwargs)


# ── Shared browser (reused across fetch_with_playwright calls) ──────────────────
#
# Launching a fresh Chromium per URL costs ~3-8s each. We keep ONE browser open
# and reuse it across fetch_with_playwright() calls for the process lifetime
# (it is torn down when the process exits).

_shared_crawler = None  # type: ignore[var-annotated]


async def _get_shared_crawler():
    global _shared_crawler
    if _shared_crawler is None:
        from crawl4ai import AsyncWebCrawler  # type: ignore[import]
        _shared_crawler = AsyncWebCrawler(config=_stealth_browser_config())
        await _shared_crawler.start()
        logger.info("[CRAWL] shared Chromium started (reused for all URLs)")
    return _shared_crawler


async def fetch_isolated(url: str, timeout_ms: int) -> tuple[str, int]:
    """
    Render one URL with a DEDICATED crawler created and closed within the caller's
    current event loop. Returns ``(html, status)`` (200 ok / 503 crawl failure /
    0 timeout-or-error).

    Why not the shared crawler: the module-level ``_shared_crawler`` is bound to
    whichever event loop first ``start()``ed it. Zone-2 fetch wraps each page in
    its own ``asyncio.run()`` (a fresh loop per call), so the SECOND render would
    reuse a browser whose transport lives on the first, now-closed loop — every
    Playwright op then hangs until the hard ceiling (~80s). A per-call crawler,
    born and closed in the same loop, avoids that cross-loop reuse entirely.
    """
    from src.crawler.exceptions import CrawlerError  # noqa: PLC0415

    try:
        from crawl4ai import AsyncWebCrawler  # type: ignore[import]  # noqa: F401
    except ImportError as exc:
        raise CrawlerError(
            "crawl4ai not installed — run: pip install crawl4ai && playwright install chromium"
        ) from exc

    hard_ceiling = (timeout_ms / 1000) + 10
    crawler = AsyncWebCrawler(config=_stealth_browser_config())
    await crawler.start()
    try:
        run_cfg = _build_run_config(wait_for=None, timeout_ms=timeout_ms)
        result = await asyncio.wait_for(crawler.arun(url=url, config=run_cfg), timeout=hard_ceiling)
        if getattr(result, "success", False):
            return (result.html or result.cleaned_html or ""), 200
        return "", 503
    except asyncio.TimeoutError:
        logger.info("isolated render timed out: %s", url)
        return "", 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("isolated render failed for %s: %s", url, exc)
        return "", 0
    finally:
        try:
            await crawler.close()
        except Exception:  # noqa: BLE001
            pass


# ── Crawl4AI page fetch ────────────────────────────────────────────────────────

async def fetch_with_playwright(
    url: str,
    wait_for: str,
    timeout_ms: int,
) -> tuple[str, int]:
    """
    Fetch a JS-rendered URL via Crawl4AI + Playwright, in stealth/magic mode,
    reusing the shared browser instance.

    Returns ``(html_content, http_status)``.
    Status codes: 200 = success, 503 = Crawl4AI reported failure, 0 = exception/timeout.

    Single attempt with the ``wait_for`` selector; on timeout/failure we retry
    once WITHOUT the selector so a blocked/empty page returns fast and the caller
    can still inspect whatever rendered.
    """
    from src.crawler.exceptions import CrawlerError  # noqa: PLC0415

    try:
        from crawl4ai import AsyncWebCrawler  # type: ignore[import]  # noqa: F401
    except ImportError as exc:
        raise CrawlerError(
            "crawl4ai not installed — run: pip install crawl4ai && playwright install chromium"
        ) from exc

    hard_ceiling = (timeout_ms / 1000) + 10

    async def _attempt(sel: str | None) -> tuple[str, int]:
        crawler = await _get_shared_crawler()
        run_cfg = _build_run_config(wait_for=sel, timeout_ms=timeout_ms)
        result = await asyncio.wait_for(
            crawler.arun(url=url, config=run_cfg),
            timeout=hard_ceiling,
        )
        if getattr(result, "success", False):
            return (result.html or result.cleaned_html or ""), 200
        return "", 503

    # Attempt 1: with wait selector
    try:
        html, status = await _attempt(wait_for)
        if status == 200 and html:
            return html, status
    except asyncio.TimeoutError:
        logger.info("Playwright wait_for timed out for %s — retrying without selector", url)
    except Exception as exc:  # noqa: BLE001
        logger.info("Playwright wait_for attempt failed for %s: %s", url, exc)

    # Attempt 2: no wait selector — grab whatever rendered
    try:
        return await _attempt(None)
    except asyncio.TimeoutError:
        logger.info("SSO_LOAD_TIMEOUT: %s", url)
        return "", 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("Playwright fetch failed for %s: %s", url, exc)
        return "", 0
