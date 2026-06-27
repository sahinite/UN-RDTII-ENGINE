"""
Validate + Archive + Confidence Flagging. [Z2-5]

ST1 — Live URL Validator: HTTP GET each source_url (retry on 429/5xx,
      soft-404 detection, broken-URL flagging).
ST2 — Wayback Machine Archiver: POST to save/{url} at output time,
      store archive URL in notes + JSON envelope.
ST6 — Confidence Flagging + ValidatedResult dataclass + Orchestrator:
      confidence < 0.80 → append "Recommend human review — OCR/translation source".
"""

from __future__ import annotations

import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

import httpx

from src.fetcher.logger import get_logger

if TYPE_CHECKING:
    from src.mapping.models import ExtractionResult

logger = get_logger("validator")

# ── Constants ──────────────────────────────────────────────────────────────────

REVIEW_NOTE = "Recommend human review — OCR/translation source"

_URL_VALIDATE_TIMEOUT = 15
_URL_MAX_RETRIES = 3
_WAYBACK_TIMEOUT = 30
# Wayback is flaky (429/520) and unreachable from some networks. Keep retries
# cheap and fall back to a local snapshot. `max_tries` caps waybackpy's OWN
# internal retry loop (default 8 → minutes of wasted backoff per URL when the
# endpoint is blocked). Once a save hard-fails, disable Wayback for the rest of
# the run so only the first URL pays the probe cost.
_WAYBACK_RETRY_WAIT = float(os.getenv("WAYBACK_RETRY_WAIT_S", "2.0"))
_WAYBACK_MAX_TRIES = int(os.getenv("WAYBACK_MAX_TRIES", "2"))
_WAYBACK_BEST_EFFORT = os.getenv("WAYBACK_BEST_EFFORT", "true").lower() in ("1", "true", "yes")
_wayback_disabled = False  # process-wide latch: set True after first hard failure
# Local snapshot fallback: save the exact bytes we fetched when Wayback fails.
_LOCAL_ARCHIVE_FALLBACK = os.getenv("LOCAL_ARCHIVE_FALLBACK", "true").lower() in ("1", "true", "yes")
_LOCAL_ARCHIVE_DIR = os.getenv("LOCAL_ARCHIVE_DIR", "outputs/archive")

_USER_AGENT = (
    "RDTII-Engine/1.0 (UN ESCAP Hackathon; +https://github.com/un-escap/rdtii-engine) "
    "waybackpy/3.0.6"
)

# Real-browser header set for validating/archiving source URLs. Header-gated
# portals (e.g. Singapore SSO) return 403 to the bot User-Agent above; the
# document download path uses browser headers, so validation must too.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_BROWSER_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

try:
    from waybackpy import WaybackMachineSaveAPI as _WaybackSaveAPI
    _WAYBACKPY_AVAILABLE = True
except ImportError:
    _WaybackSaveAPI = None  # type: ignore[assignment,misc]
    _WAYBACKPY_AVAILABLE = False

# Patterns that indicate a soft-404 / error page even on HTTP 200
_ERROR_PAGE_PATTERNS = (
    "page not found",
    "404 not found",
    "file not found",
    "this page cannot be found",
    "no longer available",
    "does not exist",
    "resource not found",
    "error 404",
    "the requested url was not found",
)

_sleep = time.sleep  # alias for testable rate-limiting

URLStatusType = Literal["ok", "broken", "soft_404", "error", "redirected"]


# ── Dataclass ──────────────────────────────────────────────────────────────────

@dataclass
class ValidatedResult:
    """ExtractionResult extended with URL-validation and archiving metadata."""
    record: "ExtractionResult"
    url_status: URLStatusType
    url_http_status: Optional[int]
    archive_url: str           # "" when archiving skipped or failed
    validated_at: str          # ISO 8601 UTC timestamp


# ── ST1: Live URL Validator ────────────────────────────────────────────────────

def _is_error_page(body: str) -> bool:
    lower = body.lower()
    return any(pat in lower for pat in _ERROR_PAGE_PATTERNS)


def validate_url(url: str) -> tuple[URLStatusType, Optional[int]]:
    """
    HTTP GET the URL, follow same-domain redirects, detect soft-404 pages.
    Returns (status, http_status_code).
    """
    headers = dict(_BROWSER_HEADERS)
    last_status: Optional[int] = None

    for attempt in range(1, _URL_MAX_RETRIES + 1):
        try:
            with httpx.Client(
                follow_redirects=True,
                max_redirects=5,
                timeout=_URL_VALIDATE_TIMEOUT,
                headers=headers,
            ) as client:
                resp = client.get(url)

            last_status = resp.status_code

            if resp.status_code == 200:
                body = resp.text
                if _is_error_page(body):
                    logger.info({
                        "event": "url_validation_soft_404",
                        "url": url,
                        "http_status": resp.status_code,
                        "economy": "",
                    })
                    return "soft_404", resp.status_code

                # Check if we were redirected to a different domain
                final_url = str(resp.url)
                original_host = url.split("/")[2] if "//" in url else ""
                final_host = final_url.split("/")[2] if "//" in final_url else ""
                if original_host and final_host and original_host != final_host:
                    logger.info({
                        "event": "url_validation_cross_domain_redirect",
                        "url": url,
                        "final_url": final_url,
                        "economy": "",
                    })
                    return "redirected", resp.status_code

                logger.info({
                    "event": "url_validation_ok",
                    "url": url,
                    "http_status": resp.status_code,
                    "economy": "",
                })
                return "ok", resp.status_code

            if resp.status_code in (404, 410):
                logger.info({
                    "event": "url_validation_broken",
                    "url": url,
                    "http_status": resp.status_code,
                    "economy": "",
                })
                return "broken", resp.status_code

            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", 30))
                logger.warning({
                    "event": "url_validation_rate_limited",
                    "url": url,
                    "retry_after_s": wait,
                    "attempt": attempt,
                    "economy": "",
                })
                _sleep(wait)
                continue

            if resp.status_code >= 500:
                logger.warning({
                    "event": "url_validation_server_error",
                    "url": url,
                    "http_status": resp.status_code,
                    "attempt": attempt,
                    "economy": "",
                })
                if attempt < _URL_MAX_RETRIES:
                    _sleep(2 ** (attempt - 1))
                    continue
                return "error", resp.status_code

            # 3xx that httpx didn't follow (shouldn't happen with follow_redirects=True)
            return "broken", resp.status_code

        except httpx.TimeoutException:
            logger.warning({
                "event": "url_validation_timeout",
                "url": url,
                "attempt": attempt,
                "economy": "",
            })
            if attempt < _URL_MAX_RETRIES:
                _sleep(2 ** (attempt - 1))
                continue
            return "error", last_status
        except httpx.RequestError as exc:
            logger.warning({
                "event": "url_validation_request_error",
                "url": url,
                "error": str(exc),
                "attempt": attempt,
                "economy": "",
            })
            return "broken", last_status

    return "error", last_status


# ── ST2: Wayback Machine Archiver ──────────────────────────────────────────────

def archive_wayback(url: str) -> str:
    """
    Submit a URL to the Wayback Machine using waybackpy.
    Returns the archive URL or "" on failure.
    """
    global _wayback_disabled

    if not _WAYBACKPY_AVAILABLE:
        logger.warning({
            "event": "wayback_unavailable",
            "reason": "waybackpy not installed (pip install waybackpy)",
            "url": url,
            "economy": "",
        })
        return ""

    if _wayback_disabled:
        # An earlier URL already hard-failed → skip the probe, go straight to
        # the local-snapshot fallback in archive_source.
        return ""

    for attempt in range(1, 3):
        try:
            api = _WaybackSaveAPI(url, _USER_AGENT, max_tries=_WAYBACK_MAX_TRIES)
            archive_url = api.save()
            logger.info({
                "event": "wayback_archived",
                "url": url,
                "archive_url": archive_url,
                "economy": "",
            })
            return archive_url

        except Exception as exc:
            err = str(exc).lower()
            if "429" in err or "too many" in err or "rate" in err:
                logger.warning({
                    "event": "wayback_rate_limited",
                    "url": url,
                    "attempt": attempt,
                    "economy": "",
                })
                _sleep(_WAYBACK_RETRY_WAIT)
                continue
            # Hard failure (unreachable/timeout/blocked): latch off Wayback for
            # the rest of the run so subsequent URLs fail fast to local archive.
            _wayback_disabled = True
            logger.warning({
                "event": "wayback_failed",
                "url": url,
                "error": str(exc),
                "attempt": attempt,
                "disabled_for_run": True,
                "economy": "",
            })
            break

    return ""


# ── Local snapshot archive (Wayback fallback) ──────────────────────────────────

def _safe_slug(url: str) -> str:
    """Filesystem-safe filename derived from a URL's path + query."""
    parsed = urllib.parse.urlparse(url)
    raw = parsed.path + (f"_{parsed.query}" if parsed.query else "")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("_")
    return (slug[:120] or "archive")


def archive_local(url: str, dest_dir: str | None = None) -> str:
    """
    Download the source document once and save a local snapshot.

    Returns the relative path to the saved file, or "" on failure. Used as a
    fallback when the Wayback Machine is unavailable — a local copy of the exact
    bytes we extracted from is stronger provenance than a best-effort web snapshot.
    """
    dest_dir = dest_dir or _LOCAL_ARCHIVE_DIR
    headers = dict(_BROWSER_HEADERS)
    try:
        with httpx.Client(timeout=_URL_VALIDATE_TIMEOUT, headers=headers, follow_redirects=True) as client:
            resp = client.get(url)
        if resp.status_code != 200 or not resp.content:
            logger.warning({
                "event": "local_archive_failed",
                "url": url,
                "http_status": resp.status_code,
                "economy": "",
            })
            return ""
        ctype = resp.headers.get("content-type", "").lower()
        bare = url.lower().split("?")[0]
        ext = ".pdf" if ("pdf" in ctype or bare.endswith(".pdf")) else (".html" if "html" in ctype else ".bin")
        Path(dest_dir).mkdir(parents=True, exist_ok=True)
        path = Path(dest_dir) / f"{_safe_slug(url)}{ext}"
        path.write_bytes(resp.content)
        logger.info({
            "event": "local_archive_saved",
            "url": url,
            "path": str(path),
            "bytes": len(resp.content),
            "economy": "",
        })
        return str(path)
    except Exception as exc:  # noqa: BLE001 — best-effort archival, never fatal
        logger.warning({
            "event": "local_archive_error",
            "url": url,
            "error": str(exc),
            "economy": "",
        })
        return ""


def reset_wayback_latch() -> None:
    """Re-enable Wayback archiving (call between independent runs/economies)."""
    global _wayback_disabled
    _wayback_disabled = False


def archive_source(url: str) -> str:
    """
    Archive one source URL: Wayback (best-effort) → local snapshot fallback.

    Returns a Wayback URL when available, else a local snapshot path, else "".
    """
    archive_url = ""
    if _WAYBACK_BEST_EFFORT:
        archive_url = archive_wayback(url)
    if not archive_url and _LOCAL_ARCHIVE_FALLBACK:
        archive_url = archive_local(url)
    return archive_url


# ── ST6: Confidence Flagging ───────────────────────────────────────────────────

_CONFIDENCE_THRESHOLD = 0.80


def _flag_confidence(record: "ExtractionResult") -> None:
    """Append REVIEW_NOTE to record.notes when confidence < 0.80 (in-place)."""
    if record.confidence is None or record.confidence >= _CONFIDENCE_THRESHOLD:
        return
    existing = record.notes or ""
    if REVIEW_NOTE in existing:
        return
    record.notes = f"{existing}; {REVIEW_NOTE}".lstrip("; ") if existing else REVIEW_NOTE


# ── Full Validation Orchestrator ───────────────────────────────────────────────

def validate_and_flag(
    records: "list[ExtractionResult]",
    *,
    archive: bool = True,
    wayback_rate_limit_s: float = 1.0,
) -> list[ValidatedResult]:
    """
    ST6 orchestrator: validates URLs, archives them, flags low confidence.

    Args:
        records: ExtractionResult list from the mapper.
        archive: Set False to skip Wayback archiving (useful in tests/offline runs).
        wayback_rate_limit_s: Seconds to wait between Wayback API calls.

    Returns:
        list[ValidatedResult] — one per input record, with URL status + archive URL.
    """
    # NOTE: the Wayback latch (_wayback_disabled) intentionally persists across
    # the whole process — validate_and_flag is called once PER DOCUMENT, so
    # resetting here would re-probe the (blocked) endpoint for every act. Tests
    # reset it via an autouse fixture in conftest; batch_run resets per economy
    # by calling reset_wayback_latch().
    results: list[ValidatedResult] = []
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    # Dedupe by source URL — many provisions share one act URL (e.g. all 10 PDPA
    # records). Validate + archive each unique URL once, not once per record.
    url_status_cache: dict[str, tuple[URLStatusType, Optional[int]]] = {}
    archive_cache: dict[str, str] = {}

    for record in records:
        src = record.source_url
        # ST1: validate source URL (cached per unique URL)
        if src not in url_status_cache:
            url_status_cache[src] = validate_url(src)
        url_status, http_code = url_status_cache[src]

        if url_status == "broken":
            logger.warning({
                "event": "output_url_broken",
                "url": record.source_url,
                "http_status": http_code,
                "indicator_id": record.indicator_id,
                "economy": record.economy,
            })
            # Append broken-URL note to record.notes
            broken_note = f"BROKEN URL (HTTP {http_code})"
            existing = record.notes or ""
            record.notes = (
                f"{existing}; {broken_note}".lstrip("; ")
                if existing
                else broken_note
            )

        # ST6: confidence flagging (before archiving so note is in ValidatedResult)
        _flag_confidence(record)

        # ST2: archiving (only live URLs) — Wayback best-effort → local fallback,
        # cached per unique URL so we don't re-archive the same act per provision.
        arch_url = ""
        if archive and url_status in ("ok", "redirected"):
            if src not in archive_cache:
                archive_cache[src] = archive_source(src)
                _sleep(wayback_rate_limit_s)
            arch_url = archive_cache[src]

        results.append(
            ValidatedResult(
                record=record,
                url_status=url_status,
                url_http_status=http_code,
                archive_url=arch_url,
                validated_at=now_iso,
            )
        )

        logger.info({
            "event": "record_validated",
            "url": record.source_url,
            "url_status": url_status,
            "archive_url": arch_url,
            "confidence": record.confidence,
            "has_review_note": REVIEW_NOTE in (record.notes or ""),
            "indicator_id": record.indicator_id,
            "economy": record.economy,
        })

    logger.info({
        "event": "validation_completed",
        "total": len(records),
        "broken": sum(1 for r in results if r.url_status == "broken"),
        "soft_404": sum(1 for r in results if r.url_status == "soft_404"),
        "archived": sum(1 for r in results if r.archive_url),
        "review_flagged": sum(
            1 for r in results if REVIEW_NOTE in (r.record.notes or "")
        ),
        "economy": records[0].economy if records else "",
    })

    return results
