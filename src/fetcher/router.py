"""
Fetch + Route + OCR Stage 1 (language-based).

Entry point for Zone 2. Downloads a document from a Zone1Result URL,
detects its type, and dispatches to the correct extractor. All routing
decisions come from document type + economy YAML — zero manual flags.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Literal

import httpx

from src.fetcher.extractors.docx_text import extract_docx
from src.fetcher.extractors.html_extractor import extract_html
from src.fetcher.extractors.ocr_stage1 import extract_ocr_stage1
from src.fetcher.extractors.pdf_text import ReclassifyToScannedError, extract_text_pdf
from src.fetcher.logger import get_logger
from src.fetcher.models import FetchedDocument, Zone1Result
from src.fetcher.segmenter import segment_volume

if TYPE_CHECKING:
    from src.config.economy_config import EconomyConfig

logger = get_logger("router")

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# JS render (force_render strategies) is flaky under anti-bot — a session is
# occasionally served an empty/challenge page. Retry a few times, backing off
# between attempts so the portal's rate-limit window can clear.
_RENDER_RETRIES = 3
_RENDER_BACKOFF_S = 3.0


# ── Portal strategy helpers ────────────────────────────────────────────────────

def _find_portal_for_url(url: str, economy_config: "EconomyConfig"):
    """Return the Portal config whose URL domain matches the given URL, or None."""
    from src.crawler.domains import registered_domain

    url_domain = registered_domain(url)
    for portal in economy_config.portals:
        if registered_domain(str(portal.url)) == url_domain:
            return portal
    return None


def _rewrite_to_pdf_url(url: str, pdf_view_suffix: str) -> str:
    """Append pdf_view_suffix to the URL, preserving existing query params."""
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    # Strip any existing fragment; append suffix as query param extension
    if parsed.query:
        new_url = f"{url}{pdf_view_suffix.replace('?', '&', 1)}"
    else:
        new_url = f"{url}{pdf_view_suffix}"
    return new_url


# ── api_versioned_pdf fetch (resolve latest version date → dated PDF URL) ─────────

# AU register id, e.g. C2004A03712 (Act) or F2021L00289 (legislative instrument).
_TITLE_ID_RE = re.compile(r"[cf]\d{4}[a-z]\d{5}", re.IGNORECASE)
# Max compilation versions to probe for an existing PDF before giving up (bounds latency).
_MAX_PDF_VERSION_TRIES = 10


def _extract_title_id(url: str) -> str | None:
    """Pull the title/register id out of any legislation.gov.au URL form
    (`/C2004A03712`, `/c2004a03712/latest/text`, `/details/c2023c00106`)."""
    m = _TITLE_ID_RE.search(url)
    return m.group(0).upper() if m else None


def _inforce_version_dates(api_base: str, title_id: str, timeout: int = 30) -> list[str]:
    """All in-force compilation start dates (YYYY-MM-DD), latest first. FRL frequently
    has NOT yet generated the text/original/pdf for the newest compilations (they 404),
    so the caller walks this list until it finds a date whose PDF actually exists.
    Rows with a registerId are registered compilations; future/unregistered rows
    (registerId=null) are skipped."""
    import urllib.parse
    crit = urllib.parse.quote("affects(Amend,Disallow)")
    filt = urllib.parse.quote(f"titleId eq '{title_id}'")
    order = urllib.parse.quote("start desc")
    url = (
        f"{api_base}/versions/search(criteria='{crit}')"
        f"?$filter={filt}&$select=start,isLatest,registerId&$orderby={order}&$top=20"
    )
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            return []
        rows = resp.json().get("value", [])
    except (httpx.HTTPError, ValueError):
        return []
    dates: list[str] = []
    for r in rows:
        if r.get("registerId") and r.get("start"):
            d = r["start"].split("T")[0]
            if d not in dates:
                dates.append(d)
    return dates


def _url_serves_pdf(url: str, timeout: int = 30) -> bool:
    """True when url returns a real PDF (checks the %PDF magic bytes), streaming only
    the first chunk so we never download a full multi-MB file just to probe it."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return False
                for chunk in resp.iter_bytes():
                    return chunk[:5] == b"%PDF-"
    except httpx.HTTPError:
        return False
    return False


def _render_spa_sync(url: str, timeout_ms: int = 30000) -> str:
    """Best-effort JS render of a SPA page via Crawl4AI/Playwright ('' on failure).
    Used by fetch: auto/html_js for JS shells.

    Uses fetch_isolated (per-call crawler), NOT the shared singleton: this runs in
    its own asyncio.run() loop, and the shared crawler bound to an earlier loop
    would hang on reuse (~80s). See fetch_isolated.
    """
    import asyncio
    try:
        from src.crawler.crawl4ai_runner import fetch_isolated
        html, _status = asyncio.run(fetch_isolated(url, timeout_ms))
        return html or ""
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning({"event": "auto_render_failed", "url": url, "error": str(exc)})
        return ""


def _fetch_via_browser(
    url: str, session_url: str = "", timeout_ms: int = 45000
) -> tuple[bytes, str, str]:
    """Fetch ``url`` through a real stealth browser — the escalation for anti-bot
    gates that answer plain httpx with 202/empty or a challenge shell (e.g. SSO's
    ``?ViewType=Pdf``). Navigates ``session_url`` first to clear the gate and hold
    its cookies, then requests the target through the same browser context.
    Returns (raw_bytes, content_type, resolved_url).

    Invoked only when a portal declares ``transport_fallback: playwright_stealth``.
    """
    import asyncio

    async def _run() -> tuple[bytes, str, str]:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(user_agent=_USER_AGENT)
                page = await ctx.new_page()
                if session_url and session_url != url:
                    try:
                        await page.goto(session_url, wait_until="domcontentloaded",
                                        timeout=timeout_ms)
                    except Exception:  # gate-priming visit is best-effort
                        pass
                resp = await ctx.request.get(url, timeout=timeout_ms)
                body = await resp.body()
                ctype = resp.headers.get("content-type", "")
                return body, ctype, (resp.url or url)
            finally:
                await browser.close()

    return asyncio.run(_run())


def _render_wholedoc(url: str, timeout_ms: int = 45000) -> str:
    """Fully render a JS "whole document" page and return its HTML ('' on failure).

    Drives Playwright directly and waits for ``networkidle`` (not crawl4ai's
    load/domcontentloaded): provisions lazy-load after the initial DOM, so an
    earlier wait yields only the arrangement-of-sections TOC, not the operative
    bodies. Waiting for the network to settle yields the complete act text.
    """
    import asyncio

    async def _run() -> str:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(user_agent=_USER_AGENT)
                page = await ctx.new_page()
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                # A short settle lets any final lazy-loaded provisions paint.
                await page.wait_for_timeout(1500)
                return await page.content()
            finally:
                await browser.close()

    try:
        return asyncio.run(_run())
    except Exception as exc:  # pragma: no cover - defensive (network/anti-bot)
        logger.warning({"event": "wholedoc_render_failed", "url": url, "error": str(exc)})
        return ""


# pdf.js viewers wrap the real PDF in a ?file=<pdf> query param, e.g.
# "pdfjs/web/viewer.html?file=../../../ilims/.../Act 854.pdf&embedded=true".
_PDF_VIEWER_FILE_RE = re.compile(r"[?&]file=([^&]+)", re.IGNORECASE)


def _pdf_candidate(raw: str | None, base_url: str) -> str | None:
    """Turn one embed/anchor value into an absolute PDF URL, or None.

    Unwraps a pdf.js `?file=` param, resolves `../` relative paths against the
    page URL, and %-encodes spaces (AGC filenames contain literal spaces). Returns
    the URL only if it actually points at a .pdf.
    """
    import urllib.parse
    if not raw:
        return None
    cand = raw.strip()
    m = _PDF_VIEWER_FILE_RE.search(cand)
    if m:
        cand = urllib.parse.unquote(m.group(1))
    abs_url = urllib.parse.urljoin(base_url, cand)
    if ".pdf" not in urllib.parse.urlparse(abs_url).path.lower():
        return None
    # Re-encode spaces without double-encoding existing %xx (% is in `safe`).
    return urllib.parse.quote(abs_url, safe=":/?&=#%+")


def _resolve_pdf_link(html_bytes: bytes, base_url: str, portal) -> str | None:
    """Standard HTML→PDF resolver for `fetch: pdf_link` — one resolver for every
    portal that embeds/links a PDF on an HTML landing page (new ones need no code).
    Cascade, first hit wins:

      1. portal.pdf_link_selector (CSS) — declarative hint for stubborn pages.
      2. <embed>/<object>/<iframe> src that is a PDF or pdf.js viewer (JPDP, AGC LOM).
      3. First <a href> ending in .pdf ("Download PDF" links, LHDN).

    Returns an absolute PDF URL, or None if the page embeds/links no PDF.
    """
    try:
        from bs4 import BeautifulSoup
    except Exception:  # pragma: no cover - bs4 is a hard dep of html_extractor
        return None
    soup = BeautifulSoup(html_bytes, "html.parser")

    # Lazy-loaded embeds put the URL in data-src, not src (AGC LOM's pdf.js
    # iframes are class="lazy" with data-src=...), so check both.
    def _src_of(tag) -> str | None:
        return (tag.get("src") or tag.get("data-src") or tag.get("data")
                or tag.get("href"))

    selector = getattr(portal, "pdf_link_selector", None)
    if selector:
        el = soup.select_one(selector)
        if el is not None:
            got = _pdf_candidate(_src_of(el), base_url)
            if got:
                return got

    for tag in soup.find_all(["embed", "iframe", "object"]):
        got = _pdf_candidate(_src_of(tag), base_url)
        if got:
            return got

    for a in soup.find_all("a", href=True):
        got = _pdf_candidate(a["href"], base_url)
        if got:
            return got
    return None


def _resolve_versioned_pdf_url(act_url: str, portal) -> str | None:
    """Build the dated PDF URL for an api_versioned_pdf portal, or None if it
    can't be resolved (caller then downloads the original URL and lets detect_type
    route it)."""
    title_id = _extract_title_id(act_url)
    api_base = getattr(portal, "api_base", None)
    if not title_id or not api_base:
        return None
    dates = _inforce_version_dates(api_base, title_id)
    if not dates:
        return None
    suffix = (getattr(portal, "pdf_path_suffix", None) or "text/original/pdf").strip("/")
    portal_base = str(portal.url).rstrip("/")
    # Walk newest→older until a compilation whose PDF exists (FRL lags on generating
    # PDFs for the newest compilations → the latest date often 404s, older ones serve).
    for d in dates[:_MAX_PDF_VERSION_TRIES]:
        candidate = f"{portal_base}/{title_id}/{d}/{d}/{suffix}"
        if _url_serves_pdf(candidate):
            if d != dates[0]:
                logger.info({
                    "event": "api_versioned_pdf_older_compilation",
                    "title_id": title_id, "latest": dates[0], "used": d,
                })
            return candidate
    return None


# ── Custom exceptions ──────────────────────────────────────────────────────────

class DownloadError(Exception):
    def __init__(self, url: str, reason: str = "") -> None:
        self.url = url
        super().__init__(f"DownloadError [{url}]: {reason}")


class UnsupportedDocTypeError(Exception):
    def __init__(self, url: str, doc_type: str) -> None:
        self.url = url
        self.doc_type = doc_type
        super().__init__(f"Unsupported document type '{doc_type}' for URL: {url}")


# ── HTTP download ──────────────────────────────────────────────────────────────

def download(url: str, timeout: int = 30) -> tuple[bytes, str, str]:
    """
    Returns (raw_bytes, content_type_header, resolved_url).
    Retries once on timeout. Raises DownloadError on failure.

    Local files (a `file://` URI or a bare existing path) are read directly —
    httpx only speaks http(s). This is what backs `main.py --pdf path/to/law.pdf`.
    """
    import urllib.parse
    from pathlib import Path as _Path

    parsed = urllib.parse.urlparse(url)
    is_file_uri = parsed.scheme == "file"
    if is_file_uri or (not parsed.scheme and _Path(url).exists()):
        local = _Path(urllib.parse.unquote(parsed.path) if is_file_uri else url)
        try:
            data = local.read_bytes()
        except OSError as exc:
            raise DownloadError(url, f"local file read failed: {exc}") from exc
        ctype = "application/pdf" if local.suffix.lower() == ".pdf" else "application/octet-stream"
        logger.info({"event": "local_file_read", "path": str(local),
                     "bytes": len(data), "content_type": ctype, "economy": ""})
        return data, ctype, url

    headers = {"User-Agent": _USER_AGENT}

    for attempt in range(1, 3):
        start = time.monotonic()
        logger.info({
            "event": "download_started",
            "url": url,
            "attempt": attempt,
            "economy": "",
        })
        try:
            with httpx.Client(
                follow_redirects=True,
                max_redirects=5,
                timeout=timeout,
                headers=headers,
            ) as client:
                response = client.get(url)

            elapsed_ms = (time.monotonic() - start) * 1000
            resolved_url = str(response.url)

            logger.info({
                "event": "download_completed",
                "url": url,
                "http_status": response.status_code,
                "content_type": response.headers.get("content-type", ""),
                "bytes": len(response.content),
                "elapsed_ms": round(elapsed_ms, 1),
                "economy": "",
            })

            if response.status_code != 200:
                raise DownloadError(url, f"HTTP {response.status_code}")

            if len(response.content) == 0:
                raise DownloadError(url, "Empty response body (0 bytes)")

            content_type = response.headers.get("content-type", "")
            return response.content, content_type, resolved_url

        except DownloadError:
            raise
        except httpx.TimeoutException as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.error({
                "event": "download_failed",
                "url": url,
                "attempt": attempt,
                "error_type": "TimeoutException",
                "message": str(exc),
                "elapsed_ms": round(elapsed_ms, 1),
                "economy": "",
            })
            if attempt == 2:
                raise DownloadError(url, "Timed out after 2 attempts") from exc
        except httpx.RequestError as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.error({
                "event": "download_failed",
                "url": url,
                "attempt": attempt,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "elapsed_ms": round(elapsed_ms, 1),
                "economy": "",
            })
            raise DownloadError(url, str(exc)) from exc

    raise DownloadError(url, "Exhausted retry attempts")


# ── Document type detection ────────────────────────────────────────────────────

DocType = Literal["TEXT_PDF", "SCANNED_PDF", "PDF", "HTML", "IMAGE", "DOCX", "UNKNOWN"]

# Content-Type headers that denote a modern Word (.docx / OOXML) document.
_DOCX_CONTENT_TYPES = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-word",
)


def _is_docx(raw_bytes: bytes) -> bool:
    """True if the bytes are a .docx (a ZIP whose members include word/document.xml).

    Distinguishes .docx from other OOXML zips (.xlsx/.pptx) and plain zips by
    checking for the Word document part, so a bare `PK` header alone is not enough.
    """
    if raw_bytes[:2] != b"PK":
        return False
    import io
    import zipfile
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
            return "word/document.xml" in zf.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def classify_pdf(raw_bytes: bytes) -> Literal["TEXT_PDF", "SCANNED_PDF"]:
    import io
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            sample_pages = pdf.pages[:3]
            text = " ".join(
                (p.extract_text(x_tolerance=3, y_tolerance=3) or "") for p in sample_pages
            )
            return "TEXT_PDF" if len(text.strip()) >= 50 else "SCANNED_PDF"
    except Exception:
        return "SCANNED_PDF"


def detect_type(raw_bytes: bytes, content_type: str) -> DocType:
    ct = content_type.lower().split(";")[0].strip()
    method: str = "header"

    # Priority 0: .docx is a ZIP with word/document.xml — unambiguous, so trust the
    # bytes over the header (handles proper CT, octet-stream, and mislabeled files).
    # Legacy .doc (OLE) is not matched → falls through to UNKNOWN.
    if ct in _DOCX_CONTENT_TYPES or _is_docx(raw_bytes):
        logger.info({"event": "doc_type_detected", "url": "", "doc_type": "DOCX", "method": method, "economy": ""})
        return "DOCX"

    # Priority 1: Content-Type application/pdf
    if "application/pdf" in ct:
        result = classify_pdf(raw_bytes)
        logger.info({"event": "doc_type_detected", "url": "", "doc_type": result, "method": method, "economy": ""})
        return result

    # Priority 2: Content-Type text/html
    if "text/html" in ct:
        logger.info({"event": "doc_type_detected", "url": "", "doc_type": "HTML", "method": method, "economy": ""})
        return "HTML"

    # Priority 3: image MIME
    if ct.startswith("image/"):
        logger.info({"event": "doc_type_detected", "url": "", "doc_type": "IMAGE", "method": method, "economy": ""})
        return "IMAGE"

    # Byte sniffing fallback
    method = "byte_sniff"
    first_512 = raw_bytes[:512]

    if b"%PDF" in first_512:
        result = classify_pdf(raw_bytes)
        logger.info({"event": "doc_type_detected", "url": "", "doc_type": result, "method": method, "economy": ""})
        return result

    leading_bytes = first_512.lstrip()
    if leading_bytes[:5].lower().startswith(b"<html") or leading_bytes[:9].lower().startswith(b"<!doctype"):
        logger.info({"event": "doc_type_detected", "url": "", "doc_type": "HTML", "method": method, "economy": ""})
        return "HTML"

    logger.info({"event": "doc_type_detected", "url": "", "doc_type": "UNKNOWN", "method": method, "economy": ""})
    return "UNKNOWN"


# ── Consolidated volume check ──────────────────────────────────────────────────

_MULTI_ACT_RE = re.compile(
    r"^(Act|Law|Ordinance|Chapter)\s+\d+",
    re.IGNORECASE | re.MULTILINE,
)

def is_consolidated_volume(raw_bytes: bytes) -> bool:
    import io
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
            page_count = len(pdf.pages)
            if page_count >= 200:
                return True
            # Check first 10 pages for multiple act headers
            sample_text = " ".join(
                (p.extract_text() or "") for p in pdf.pages[:10]
            )
            matches = _MULTI_ACT_RE.findall(sample_text)
            return len(matches) >= 2
    except Exception:
        return False


# ── Main router ────────────────────────────────────────────────────────────────

def route(zone1_result: Zone1Result, economy_config: "EconomyConfig") -> FetchedDocument | list[FetchedDocument]:
    """
    Download + detect + extract. Returns a single FetchedDocument,
    or a list when a consolidated volume is split into segments.

    Honors fetch: pdf_endpoint — rewrites the act URL to its PDF view before
    downloading, routing to the existing TEXT_PDF → pdfplumber path (no OCR).
    """
    # ── pdf_endpoint rewrite: act URL → PDF print endpoint ────────────────────
    fetch_url = zone1_result.url
    single_act_fetch = False
    auto_render = False
    force_render = False
    wholedoc_render = False
    resolve_pdf_link = False
    portal = _find_portal_for_url(fetch_url, economy_config)
    if portal is not None:
        fetch_strategy = getattr(portal, "fetch", "TBD")
        pdf_suffix = getattr(portal, "pdf_view_suffix", None)
        if fetch_strategy == "pdf_endpoint" and pdf_suffix:
            # We fetched exactly one act's PDF — it is NOT a consolidated volume,
            # so skip volume detection/segmentation (avoids double-processing).
            single_act_fetch = True
            rewritten = _rewrite_to_pdf_url(fetch_url, pdf_suffix)
            logger.info({
                "event": "pdf_endpoint_rewrite",
                "original_url": fetch_url,
                "pdf_url": rewritten,
                "portal": portal.name,
                "economy": zone1_result.economy,
            })
            fetch_url = rewritten
        elif fetch_strategy == "api_versioned_pdf":
            # Resolve the act's latest-version dated PDF URL via the versions API.
            # Also a single act's PDF → not a consolidated volume.
            single_act_fetch = True
            resolved = _resolve_versioned_pdf_url(fetch_url, portal)
            if resolved:
                logger.info({
                    "event": "api_versioned_pdf_resolved",
                    "original_url": fetch_url,
                    "pdf_url": resolved,
                    "portal": portal.name,
                    "economy": zone1_result.economy,
                })
                fetch_url = resolved
            else:
                logger.warning({
                    "event": "api_versioned_pdf_unresolved",
                    "original_url": fetch_url,
                    "portal": portal.name,
                    "economy": zone1_result.economy,
                })
        elif fetch_strategy == "auto":
            # Best-effort: download as-is; if the page is a JS SPA shell, the HTML
            # branch below renders it with Playwright before extraction.
            auto_render = True
        elif fetch_strategy == "html_js":
            # Portal whose pages ALWAYS need JS (e.g. pdpc.gov.sg). Renders
            # unconditionally — unlike `auto`, it skips classify_render, whose probe
            # mis-reads a menu-wrapped SPA shell as SSR.
            force_render = True
        elif fetch_strategy == "html_wholedoc":
            # Portal serves the full act as a JS-rendered whole-doc view via a URL
            # suffix (e.g. SSO ?WholeDoc=1), since the default page paginates. Append
            # pdf_view_suffix, then force a JS render (provisions lazy-load).
            single_act_fetch = True
            force_render = True
            wholedoc_render = True
            if pdf_suffix:
                rewritten = _rewrite_to_pdf_url(fetch_url, pdf_suffix)
                logger.info({
                    "event": "html_wholedoc_rewrite",
                    "original_url": fetch_url,
                    "wholedoc_url": rewritten,
                    "portal": portal.name,
                    "economy": zone1_result.economy,
                })
                fetch_url = rewritten
        elif fetch_strategy == "pdf_link":
            # Portal whose act page embeds/links a PDF (JPDP, AGC pdf.js, LHDN).
            # Download the HTML, resolve the PDF URL (HTML branch below), fetch it.
            # A single act, not a volume — skip segmentation.
            single_act_fetch = True
            resolve_pdf_link = True

    # Render up front for JS-populated strategies (html_js, html_wholedoc); this also
    # sidesteps anti-bot gates that 202 a plain httpx GET (e.g. SSO). The render is
    # flaky under anti-bot, so retry a few times before falling through to download.
    if force_render:
        rendered = ""
        for _attempt in range(1, _RENDER_RETRIES + 1):
            # Whole-doc pages lazy-load their provisions, so use the direct-Playwright
            # networkidle renderer (complete text); fall back to crawl4ai if it comes
            # back empty. Simple SPAs (html_js) use the crawl4ai path directly.
            if wholedoc_render:
                rendered = _render_wholedoc(fetch_url) or _render_spa_sync(fetch_url)
            else:
                rendered = _render_spa_sync(fetch_url)
            if rendered:
                break
            logger.info({"event": "forced_js_render_retry", "url": fetch_url,
                         "attempt": _attempt, "economy": zone1_result.economy})
            # Backoff: the empty render is anti-bot rate-limiting, which needs a
            # pause to clear — retrying immediately just gets throttled again.
            if _attempt < _RENDER_RETRIES:
                time.sleep(_RENDER_BACKOFF_S * _attempt)
        if rendered:
            logger.info({"event": "forced_js_render", "url": fetch_url,
                         "economy": zone1_result.economy})
            zone1_result_rendered = Zone1Result(
                url=fetch_url,
                economy=zone1_result.economy,
                act_title=zone1_result.act_title,
                discovery_tag=zone1_result.discovery_tag,
                archive_url=zone1_result.archive_url,
            )
            return extract_html(rendered.encode("utf-8"), zone1_result_rendered,
                                content_type="text/html")
        logger.warning({"event": "forced_js_render_failed", "url": fetch_url,
                        "economy": zone1_result.economy})

    # When a portal declares transport_fallback: playwright_stealth, escalate to a
    # real browser session for anti-bot gates that answer plain httpx with an
    # empty/202 body or a challenge shell (e.g. SSO's PDF view).
    browser_fallback = (
        portal is not None
        and getattr(portal, "transport_fallback", None) == "playwright_stealth"
    )

    try:
        raw_bytes, content_type, resolved_url = download(fetch_url)
        doc_type = detect_type(raw_bytes, content_type)
        # A 200 that yields no usable document (anti-bot HTML shell / XML notice)
        # also warrants escalation when a browser fallback is configured.
        if doc_type == "UNKNOWN" and browser_fallback:
            raise DownloadError(fetch_url, f"unusable doc_type after download: {doc_type}")
    except DownloadError:
        if not browser_fallback:
            raise
        logger.info({"event": "browser_fallback_fetch", "url": fetch_url,
                     "portal": portal.name, "economy": zone1_result.economy})
        raw_bytes, content_type, resolved_url = _fetch_via_browser(
            fetch_url, session_url=zone1_result.url
        )
        doc_type = detect_type(raw_bytes, content_type)

    # Patch resolved_url back into zone1_result for downstream use
    zone1_result_resolved = Zone1Result(
        url=resolved_url,
        economy=zone1_result.economy,
        act_title=zone1_result.act_title,
        discovery_tag=zone1_result.discovery_tag,
        archive_url=zone1_result.archive_url,
    )

    if doc_type == "UNKNOWN":
        raise UnsupportedDocTypeError(zone1_result.url, doc_type)

    if doc_type == "HTML":
        # fetch: pdf_link — the act text lives in a PDF the page embeds/links, not
        # in the HTML. Resolve that PDF URL, download it, and route to the PDF path.
        # Falls through to HTML extraction only if no PDF is found (best effort).
        if resolve_pdf_link:
            pdf_url = _resolve_pdf_link(raw_bytes, resolved_url, portal)
            if pdf_url:
                logger.info({"event": "pdf_link_resolved", "page_url": resolved_url,
                             "pdf_url": pdf_url, "economy": zone1_result.economy})
                pdf_bytes, pdf_ctype, pdf_resolved = download(pdf_url)
                pdf_type = detect_type(pdf_bytes, pdf_ctype)
                if pdf_type in ("TEXT_PDF", "SCANNED_PDF"):
                    zone1_pdf = Zone1Result(
                        url=pdf_resolved,
                        economy=zone1_result.economy,
                        act_title=zone1_result.act_title,
                        discovery_tag=zone1_result.discovery_tag,
                        archive_url=zone1_result.archive_url,
                    )
                    return _extract_single_pdf(pdf_bytes, pdf_type, zone1_pdf, economy_config)
                logger.warning({"event": "pdf_link_not_pdf", "pdf_url": pdf_url,
                                "doc_type": pdf_type, "economy": zone1_result.economy})
            else:
                logger.warning({"event": "pdf_link_unresolved", "page_url": resolved_url,
                                "economy": zone1_result.economy})
        # fetch: html_js — this portal's pages always need JS; render unconditionally.
        if force_render:
            rendered = _render_spa_sync(resolved_url)
            if rendered:
                logger.info({"event": "forced_js_render", "url": resolved_url,
                             "economy": zone1_result.economy})
                return extract_html(rendered.encode("utf-8"), zone1_result_resolved,
                                    content_type="text/html")
            logger.warning({"event": "forced_js_render_failed", "url": resolved_url,
                            "economy": zone1_result.economy})
        # fetch: auto — if the static HTML is a JS SPA shell, render it first.
        if auto_render:
            from src.crawler.spa_probe import classify_render
            if classify_render(raw_bytes.decode("utf-8", "replace")).is_spa:
                rendered = _render_spa_sync(resolved_url)
                if rendered:
                    logger.info({"event": "auto_render_spa", "url": resolved_url,
                                 "economy": zone1_result.economy})
                    return extract_html(rendered.encode("utf-8"), zone1_result_resolved,
                                        content_type="text/html")
        return extract_html(raw_bytes, zone1_result_resolved, content_type=content_type)

    if doc_type == "DOCX":
        return extract_docx(raw_bytes, zone1_result_resolved, economy_config)

    if doc_type == "IMAGE":
        return _try_ocr(raw_bytes, zone1_result_resolved, economy_config)

    # PDF path — check consolidated volume first (skipped for single-act fetches)
    if doc_type in ("TEXT_PDF", "SCANNED_PDF"):
        if not single_act_fetch and is_consolidated_volume(raw_bytes):
            logger.info({
                "event": "consolidated_volume_detected",
                "url": zone1_result.url,
                "page_count": "unknown",
                "act_count": "detecting",
                "economy": zone1_result.economy,
            })
            segments = segment_volume(raw_bytes, economy_config)
            results: list[FetchedDocument] = []
            for seg in segments:
                seg_zone1 = Zone1Result(
                    url=zone1_result.url,
                    economy=zone1_result.economy,
                    act_title=seg.act_title,
                    discovery_tag=zone1_result.discovery_tag,
                    archive_url=zone1_result.archive_url,
                )
                if doc_type == "TEXT_PDF":
                    try:
                        doc = extract_text_pdf(seg.raw_bytes, seg_zone1, economy_config)
                    except ReclassifyToScannedError:
                        doc = _try_ocr(seg.raw_bytes, seg_zone1, economy_config, is_segment=True)
                else:
                    doc = _try_ocr(seg.raw_bytes, seg_zone1, economy_config, is_segment=True)
                doc.is_segment = True
                doc.segment_index = seg.segment_index
                results.append(doc)
            return results

        return _extract_single_pdf(raw_bytes, doc_type, zone1_result_resolved, economy_config)

    raise UnsupportedDocTypeError(zone1_result.url, doc_type)


def _extract_single_pdf(
    raw_bytes: bytes,
    doc_type: "DocType",
    zone1_result: Zone1Result,
    economy_config: "EconomyConfig",
) -> FetchedDocument:
    """Extract one non-volume act PDF: text-layer first, OCR on failure/scan.

    Shared by the direct-PDF path and the fetch: pdf_link path so both handle
    text-vs-scanned identically.
    """
    if doc_type == "TEXT_PDF":
        try:
            return extract_text_pdf(raw_bytes, zone1_result, economy_config)
        except ReclassifyToScannedError:
            return _try_ocr(raw_bytes, zone1_result, economy_config)
    return _try_ocr(raw_bytes, zone1_result, economy_config)


def _try_ocr(
    raw_bytes: bytes,
    zone1_result: Zone1Result,
    economy_config: "EconomyConfig",
    is_segment: bool = False,
) -> FetchedDocument:
    """Cloud-first OCR: Mistral → Azure (if configured) → LLM-vision → local floor.

    Cloud engines read scanned government PDFs far better and faster than local
    Tesseract/Paddle, which now serve only as the offline last resort (no cloud key,
    or every cloud tier failed). Falling straight to cloud also avoids the old wasted
    full Tesseract pass whose output was thrown away on the CER gate."""
    from src.ocr.processor import run_ocr_cloud

    doc = run_ocr_cloud(raw_bytes, zone1_result, economy_config, is_segment=is_segment)
    if doc is not None:
        return doc

    # No cloud tier available/succeeded → local floor. gate_cer=False: CER is advisory
    # (flags for review) and never raises, so we always return the best local text.
    logger.info({
        "event": "ocr_cloud_unavailable_local_floor",
        "url": zone1_result.url,
        "economy": zone1_result.economy,
    })
    return extract_ocr_stage1(
        raw_bytes, zone1_result, economy_config, is_segment=is_segment, gate_cer=False,
    )
