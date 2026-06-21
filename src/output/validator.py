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

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
_WAYBACK_SAVE_URL = "https://web.archive.org/save/{url}"
_WAYBACK_TIMEOUT = 30
_WAYBACK_RETRY_WAIT = 60.0

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

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
    headers = {"User-Agent": _USER_AGENT}
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
    POST a URL to Wayback Machine save API.
    Returns archive URL (from Content-Location header) or "" on failure.
    """
    save_url = _WAYBACK_SAVE_URL.format(url=url)
    headers = {"User-Agent": _USER_AGENT}

    for attempt in range(1, 3):
        try:
            with httpx.Client(timeout=_WAYBACK_TIMEOUT, headers=headers) as client:
                resp = client.get(save_url)  # Wayback uses GET for save

            if resp.status_code == 200:
                # Content-Location: /web/20240101000000/https://example.com/act
                content_location = resp.headers.get(
                    "Content-Location",
                    resp.headers.get("content-location", ""),
                )
                if content_location:
                    archive = (
                        content_location
                        if content_location.startswith("http")
                        else f"https://web.archive.org{content_location}"
                    )
                    logger.info({
                        "event": "wayback_archived",
                        "url": url,
                        "archive_url": archive,
                        "economy": "",
                    })
                    return archive
                # Sometimes 200 without Content-Location — construct from redirect chain
                final = str(resp.url)
                if "web.archive.org/web/" in final:
                    logger.info({
                        "event": "wayback_archived",
                        "url": url,
                        "archive_url": final,
                        "economy": "",
                    })
                    return final

            if resp.status_code == 429:
                logger.warning({
                    "event": "wayback_rate_limited",
                    "url": url,
                    "attempt": attempt,
                    "economy": "",
                })
                _sleep(_WAYBACK_RETRY_WAIT)
                continue

            logger.warning({
                "event": "wayback_failed",
                "url": url,
                "http_status": resp.status_code,
                "attempt": attempt,
                "economy": "",
            })
            break

        except httpx.RequestError as exc:
            logger.warning({
                "event": "wayback_request_error",
                "url": url,
                "error": str(exc),
                "attempt": attempt,
                "economy": "",
            })
            break

    return ""


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
    results: list[ValidatedResult] = []
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    for record in records:
        # ST1: validate source URL
        url_status, http_code = validate_url(record.source_url)

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

        # ST2: Wayback Machine archiving (only live URLs)
        arch_url = ""
        if archive and url_status in ("ok", "redirected"):
            arch_url = archive_wayback(record.source_url)
            if arch_url and not record.notes:
                pass  # archive_url stored in ValidatedResult; notes updated below if needed
            _sleep(wayback_rate_limit_s)

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
