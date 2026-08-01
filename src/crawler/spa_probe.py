"""
Shared SPA-vs-server-rendered probe. [ADR-045 / unified-portal-strategy.md D6]

One primitive, two consumers:
  - the `auto` discovery adapter — SSR → crawl static links; SPA → render-then-scrape
  - fetch routing — SSR → static-HTML branch; SPA → JS-render (html_js) branch

Decides the one thing detect_type can't: whether an HTML page's content is in the
static markup (SSR) or only appears after JS runs (SPA) — both serve a 200 shell.
Framework markers (e.g. `ng-version`) are the primary signal, body length secondary:
an Angular SPA (AU FRL) has ~1770 chars of nav chrome, above a naive length gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from bs4 import BeautifulSoup

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
