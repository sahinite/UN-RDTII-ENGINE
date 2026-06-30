"""
Fetch + Route + OCR Stage 1 (language-based). [Z2-1 ST1]

Entry point for Zone 2. Downloads a document from a Zone1Result URL,
detects its type, and dispatches to the correct extractor. All routing
decisions come from document type + economy YAML — zero manual flags.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Literal

import httpx

from src.fetcher.extractors.html_extractor import ExtractionError as HTMLExtractionError
from src.fetcher.extractors.html_extractor import extract_html
from src.fetcher.extractors.ocr_stage1 import OCRQualityError, extract_ocr_stage1
from src.fetcher.extractors.pdf_text import ExtractionError as PDFExtractionError
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


# ── Portal strategy helpers ────────────────────────────────────────────────────

def _find_portal_for_url(url: str, economy_config: "EconomyConfig"):
    """Return the Portal config whose URL domain matches the given URL, or None."""
    import tldextract
    def _reg_domain(u: str) -> str:
        ext = tldextract.extract(u)
        return getattr(ext, "top_domain_under_public_suffix", None) or ext.registered_domain

    url_domain = _reg_domain(url)
    for portal in economy_config.portals:
        if _reg_domain(str(portal.url)) == url_domain:
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

DocType = Literal["TEXT_PDF", "SCANNED_PDF", "PDF", "HTML", "IMAGE", "UNKNOWN"]


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

    snip = first_512.lstrip()
    if snip[:5].lower().startswith(b"<html") or snip[:9].lower().startswith(b"<!doctype"):
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

    raw_bytes, content_type, resolved_url = download(fetch_url)

    # Patch resolved_url back into zone1_result for downstream use
    zone1_result_resolved = Zone1Result(
        url=resolved_url,
        economy=zone1_result.economy,
        act_title=zone1_result.act_title,
        discovery_tag=zone1_result.discovery_tag,
        archive_url=zone1_result.archive_url,
    )

    doc_type = detect_type(raw_bytes, content_type)

    if doc_type == "UNKNOWN":
        raise UnsupportedDocTypeError(zone1_result.url, doc_type)

    if doc_type == "HTML":
        return extract_html(raw_bytes, zone1_result_resolved, content_type=content_type)

    if doc_type == "IMAGE":
        return extract_ocr_stage1(raw_bytes, zone1_result_resolved, economy_config)

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

        if doc_type == "TEXT_PDF":
            try:
                return extract_text_pdf(raw_bytes, zone1_result_resolved, economy_config)
            except ReclassifyToScannedError:
                return _try_ocr(raw_bytes, zone1_result_resolved, economy_config)

        return _try_ocr(raw_bytes, zone1_result_resolved, economy_config)

    raise UnsupportedDocTypeError(zone1_result.url, doc_type)


def _try_ocr(
    raw_bytes: bytes,
    zone1_result: Zone1Result,
    economy_config: "EconomyConfig",
    is_segment: bool = False,
) -> FetchedDocument:
    """Run OCR Stage 1; on CER failure automatically escalates to Stage 2."""
    try:
        return extract_ocr_stage1(raw_bytes, zone1_result, economy_config, is_segment=is_segment)
    except OCRQualityError as exc:
        logger.info({
            "event": "ocr_stage1_quality_failed_escalating",
            "stage1_cer": round(exc.cer, 4),
            "stage1_engine": exc.engine_used,
            "url": zone1_result.url,
            "economy": zone1_result.economy,
        })
        from src.ocr.processor import run_ocr_stage2
        return run_ocr_stage2(
            raw_bytes=raw_bytes,
            zone1_result=zone1_result,
            economy_config=economy_config,
            stage1_cer=exc.cer,
            stage1_engine=exc.engine_used,
            is_segment=is_segment,
        )
