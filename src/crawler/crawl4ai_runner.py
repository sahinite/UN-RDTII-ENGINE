"""
Crawl4AI integration (Playwright-based, MIT license). [Z1-3]

Dedicated wrapper around Crawl4AI/Playwright for JS-rendered government portals.
Extracted from crawler.py so it can be imported, swapped, or mocked independently.

Chosen over Scrapy/custom crawler for JS-rendering support — see
RDTII_Engine_Technical_Plan_v2.docx §6 Technology Stack.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config.economy_config import Portal

logger = logging.getLogger(__name__)

# ── Module-level defaults (used when a portal doesn't declare its own values) ──

# Default wait selector for Act-listing pages — portals that need a different
# selector should set playwright_wait_for in their YAML Portal entry.
SSO_WAIT_FOR = "css:a[href*='/Act/']"
SSO_TIMEOUT_MS = 20000


# ── Portal type detection ──────────────────────────────────────────────────────

def is_js_portal(portal: "Portal") -> bool:
    """Return True when the Portal's YAML config declares js_required=true."""
    return portal.js_required


# ── Crawl4AI page fetch ────────────────────────────────────────────────────────

async def probe_js_page(url: str) -> tuple[str, bool]:
    """
    Minimal Crawl4AI fetch for portal probing (no wait selector, no scroll JS).

    Used by probe.py to check hit counts on JS-rendered search pages.
    Returns ``(html_content, success)``.
    """
    from src.crawler.exceptions import CrawlerError  # noqa: PLC0415

    try:
        from crawl4ai import AsyncWebCrawler  # type: ignore[import]
    except ImportError as exc:
        raise CrawlerError(
            "crawl4ai not installed — run: pip install crawl4ai && playwright install chromium"
        ) from exc

    try:
        async with AsyncWebCrawler(headless=True) as crawler:
            result = await crawler.arun(url=url)
        html = getattr(result, "html", "") or ""
        return html, result.success
    except Exception as exc:
        logger.warning("Playwright probe failed for %s: %s", url, exc)
        return "", False


async def fetch_with_playwright(
    url: str,
    wait_for: str,
    timeout_ms: int,
) -> tuple[str, int]:
    """
    Fetch a JS-rendered URL via Crawl4AI + Playwright.

    Returns ``(html_content, http_status)``.
    Status codes: 200 = success, 503 = Crawl4AI reported failure, 0 = exception/timeout.
    """
    # Deferred import to keep startup fast and allow test mocking of the module name
    from src.crawler.exceptions import CrawlerError  # noqa: PLC0415

    try:
        from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig  # type: ignore[import]
    except ImportError as exc:
        raise CrawlerError(
            "crawl4ai not installed — run: pip install crawl4ai && playwright install chromium"
        ) from exc

    try:
        config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            wait_for=wait_for,
            page_timeout=timeout_ms,
            verbose=False,
            js_code="window.scrollTo(0, document.body.scrollHeight);",
        )
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(url=url, config=config)
            if result.success:
                return result.html or result.cleaned_html or "", 200
            return "", 503
    except Exception as exc:
        logger.warning("Playwright fetch failed for %s: %s", url, exc)
        if "SSO_LOAD_TIMEOUT" in str(exc) or "timeout" in str(exc).lower():
            logger.info("SSO_LOAD_TIMEOUT: %s", url)
        return "", 0
