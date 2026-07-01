"""
Shared SPA-vs-server-rendered probe. [ADR-045 / unified-portal-strategy.md D6]

One primitive, two consumers:
  - the `auto` discovery adapter — SSR → crawl static links; SPA → render-then-scrape
  - fetch routing — SSR → static-HTML branch; SPA → JS-render (html_js) branch

`detect_type` can already sniff PDF/scanned/HTML from bytes; the ONE thing it
cannot decide is whether an HTML page's substantive content is present in the
static markup (SSR) or only appears after JavaScript runs (SPA) — both return a
`200 text/html` shell (the Australia `/latest/` trap).

Calibrated against real ground truth (2026-07-01):
  - AU FRL (Angular SPA): static body text ≈ 1770 chars — ABOVE a naive length
    threshold — but all nav chrome; the reliable tell is the `ng-version` marker.
  - SG SSO browse (SSR): ≈ 7309 chars of real content, no framework markers.
So length alone is insufficient: markers are the primary signal, length secondary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from src.config.economy_config import Portal

RenderMode = Literal["ssr", "spa"]

# Default minimum visible body-text length for an SSR page. Below this (and with
# no substantive content), a page is treated as a bare SPA shell.
DEFAULT_MIN_TEXT_CHARS = 200

# Strong client-side-rendering markers. Presence of any of these means the page
# mounts a JS app; gov portals using these frameworks are CSR in practice.
_JS_TEXT_MARKERS = (
    "ng-version",           # Angular (AU FRL)
    "you need to enable javascript",
    "please enable javascript",
    "enable javascript to run this app",
)
# Empty mount points that a framework fills at runtime (only counts as SPA when
# the element carries little/no server-rendered text).
_MOUNT_SELECTORS = (
    ("app-root", None),
    ("div", "root"),
    ("div", "app"),
)


@dataclass
class RenderProbe:
    mode: RenderMode
    body_text_len: int
    spa_markers: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def is_spa(self) -> bool:
        return self.mode == "spa"


def _empty_mount_markers(soup: BeautifulSoup) -> list[str]:
    """Return names of framework mount points that are (near-)empty in static HTML."""
    found: list[str] = []
    for tag_name, elem_id in _MOUNT_SELECTORS:
        attrs = {"id": elem_id} if elem_id else {}
        for el in soup.find_all(tag_name, attrs=attrs):
            if len(el.get_text(strip=True)) < 40:
                found.append(f"<{tag_name}{'#' + elem_id if elem_id else ''}>")
                break
    return found


def classify_render(html: str, *, min_text_chars: int = DEFAULT_MIN_TEXT_CHARS) -> RenderProbe:
    """
    Classify already-fetched static HTML as SSR or SPA. Pure + deterministic.

    Decision (calibrated):
      1. any strong CSR marker (ng-version / enable-javascript / empty mount) → SPA
      2. else visible body text < min_text_chars → SPA (bare shell)
      3. else → SSR (content is present in the static markup)
    """
    if not html or not html.strip():
        return RenderProbe(mode="spa", body_text_len=0, reason="empty response")

    soup = BeautifulSoup(html, "html.parser")
    lowered = html.lower()

    markers = [m for m in _JS_TEXT_MARKERS if m in lowered]
    markers += _empty_mount_markers(soup)

    body = soup.body
    body_text_len = len(body.get_text(strip=True)) if body is not None else 0

    if markers:
        return RenderProbe(
            mode="spa",
            body_text_len=body_text_len,
            spa_markers=markers,
            reason=f"client-side-render marker(s): {', '.join(markers)}",
        )
    if body_text_len < min_text_chars:
        return RenderProbe(
            mode="spa",
            body_text_len=body_text_len,
            reason=f"bare shell: body text {body_text_len} < {min_text_chars} chars",
        )
    return RenderProbe(
        mode="ssr",
        body_text_len=body_text_len,
        reason=f"content present in static markup ({body_text_len} chars, no CSR markers)",
    )


async def probe_render(url: str, portal: "Portal | None" = None) -> RenderProbe:
    """
    Fetch a URL's STATIC HTML (no JS execution) and classify it.

    Uses the transport ladder's httpx rungs but deliberately never escalates to
    Playwright — the whole point is to observe what the server returns WITHOUT a
    browser, so a JS app appears as its shell. Returns an SPA verdict on fetch
    failure (safest for the caller: it will try the render path).
    """
    from src.crawler.transport import _fetch_plain, _fetch_with_headers

    html, status = await _fetch_with_headers(url)
    if not html:
        html, status = await _fetch_plain(url)
    if not html:
        return RenderProbe(mode="spa", body_text_len=0, reason=f"fetch failed (status={status})")
    return classify_render(html)
