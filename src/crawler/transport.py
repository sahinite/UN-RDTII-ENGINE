"""
Transport ladder for portal fetching. [PRD: Pillar-Agnostic Portal Strategy]

Single fetch primitive that escalates:
  httpx (plain) → httpx (browser headers) → stealth Playwright

Stops at the first rung that returns a real 200 with meaningful content
(non-block, non-JS-shell). Playwright is invoked only when the portal's
transport_fallback is "playwright_stealth".
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from src.config.economy_config import Portal

logger = logging.getLogger(__name__)

# ── Browser-spoofing headers (bypass header-based bot detection) ───────────────

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
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

_TIMEOUT_S = 20.0
_JS_SHELL_MIN_CHARS = 200


# ── JS-shell / bot-block detection ────────────────────────────────────────────

def _is_real_response(html: str, status: int) -> bool:
    """Return True when the response is a real page, not a block or JS shell."""
    if status in (403, 429) or not html:
        return False
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body
    if body is None:
        return False
    text = body.get_text(strip=True)
    if len(text) < _JS_SHELL_MIN_CHARS:
        return False
    # Explicit JS-required markers
    js_markers = ("You need to enable JavaScript", "Please enable JavaScript")
    if any(m in html for m in js_markers):
        return False
    return True


# ── Transport rungs ────────────────────────────────────────────────────────────

async def _fetch_plain(url: str) -> tuple[str, int]:
    """Rung 1: plain httpx with no special headers."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT_S) as client:
            resp = await client.get(url)
        return resp.text, resp.status_code
    except Exception as exc:
        logger.debug("[TRANSPORT] plain httpx error %s: %s", url, exc)
        return "", 0


async def _fetch_with_headers(url: str) -> tuple[str, int]:
    """Rung 2: httpx with browser-spoofing headers."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=_TIMEOUT_S) as client:
            resp = await client.get(url, headers=_BROWSER_HEADERS)
        return resp.text, resp.status_code
    except Exception as exc:
        logger.debug("[TRANSPORT] header_spoof httpx error %s: %s", url, exc)
        return "", 0


async def _fetch_playwright(url: str) -> tuple[str, int]:
    """Rung 3: stealth Playwright via Crawl4AI."""
    from src.crawler.crawl4ai_runner import SSO_TIMEOUT_MS, SSO_WAIT_FOR, fetch_with_playwright
    try:
        html, status = await fetch_with_playwright(url, SSO_WAIT_FOR, SSO_TIMEOUT_MS)
        return html, status
    except Exception as exc:
        logger.warning("[TRANSPORT] Playwright error %s: %s", url, exc)
        return "", 0


# ── Public API ─────────────────────────────────────────────────────────────────

async def fetch(url: str, portal: "Portal") -> tuple[str, int]:
    """
    Transport ladder: escalates rungs until a real response is obtained.

    Rung 1 — plain httpx (cheapest, no fingerprint)
    Rung 2 — httpx with browser headers (skips rung 1 for header_spoof portals)
    Rung 3 — stealth Playwright (only when portal.transport_fallback == "playwright_stealth")

    Returns (html, http_status). Returns ("", 0) if all rungs fail.
    """
    anti_bot = getattr(portal, "anti_bot", "none")
    transport_fallback = getattr(portal, "transport_fallback", None)

    # Rung 1: plain httpx (skip for portals that are known to block plain requests)
    if anti_bot == "none":
        html, status = await _fetch_plain(url)
        if _is_real_response(html, status):
            logger.debug("[TRANSPORT] rung1/plain succeeded: %s", url)
            return html, status
        logger.debug("[TRANSPORT] rung1/plain blocked (%d): %s", status, url)

    # Rung 2: browser-spoofing headers
    html, status = await _fetch_with_headers(url)
    if _is_real_response(html, status):
        logger.debug("[TRANSPORT] rung2/headers succeeded: %s", url)
        return html, status
    logger.debug("[TRANSPORT] rung2/headers blocked (%d): %s", status, url)

    # Rung 3: stealth Playwright (only when explicitly configured)
    if transport_fallback == "playwright_stealth":
        logger.info("[TRANSPORT] escalating to Playwright: %s", url)
        html, status = await _fetch_playwright(url)
        if _is_real_response(html, status):
            logger.debug("[TRANSPORT] rung3/playwright succeeded: %s", url)
            return html, status
        logger.warning("[TRANSPORT] rung3/playwright also blocked (%d): %s", status, url)

    return "", 0
