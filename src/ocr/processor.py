"""
OCR two-stage cascade. [Z2-1 Stage 1, Z2-5 Stage 2]

Stage 1 (language-based, automatic from economy YAML):
    Tesseract  -> Latin-script economies
    PaddleOCR  -> Asian-script economies
Stage 2 (quality-based fallback, automatic):
    triggers when CER >= 5% -> Azure Document Intelligence or Mistral OCR

No manual OCR engine choice at runtime — fully automatic cascade.
"""

from __future__ import annotations

import base64
import os
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from src.fetcher.extractors.ocr_stage1 import assemble_pages, pdf_to_images
from src.fetcher.logger import get_logger
from src.fetcher.models import CostLogEntry, FetchedDocument

if TYPE_CHECKING:
    from src.fetcher.models import Zone1Result
    from src.config.economy_config import EconomyConfig

logger = get_logger("ocr_stage2")

_sleep = time.sleep  # alias for testable rate-limiting

_CER_THRESHOLD = 0.05

# Per-page cloud OCR prices (used for cost accounting). Mistral rate is env-tunable.
_MISTRAL_OCR_PRICE_PER_PAGE = float(os.getenv("MISTRAL_OCR_COST_PER_PAGE", "0.001"))
_AZURE_OCR_PRICE_PER_PAGE = 0.0015


# ── OCR result container ────────────────────────────────────────────────────────

@dataclass
class OCRResult:
    text: str
    cer: float
    engine_used: str
    stage2_triggered: bool = False
    stage2_failed: bool = False


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _estimate_cer_from_text(text: str) -> float:
    """Heuristic CER from non-printable character ratio (no ground truth needed)."""
    if not text or not text.strip():
        return 1.0
    garbage = sum(
        1 for ch in text
        if unicodedata.category(ch) in ("Cc", "Cs", "Co", "Cn")
        and ch not in ("\n", "\t", "\r")
    )
    return min(garbage / len(text), 1.0)


# ── ST3: Azure Document Intelligence ──────────────────────────────────────────

def run_azure_di(image_bytes: bytes, timeout: int = 60) -> tuple[str, float]:
    """
    Call Azure Document Intelligence prebuilt-read model.
    Returns (text, cer_estimate).
    Raises RuntimeError when credentials missing or API call fails.
    """
    api_key = os.getenv("AZURE_DI_KEY", "")
    endpoint = os.getenv("AZURE_DI_ENDPOINT", "").rstrip("/")

    if not api_key or not endpoint:
        raise RuntimeError(
            "AZURE_DI_KEY and AZURE_DI_ENDPOINT must be set for Stage 2 Azure DI"
        )

    b64 = base64.b64encode(image_bytes).decode()
    analyze_url = (
        f"{endpoint}/documentintelligence/documentModels/prebuilt-read:analyze"
        "?api-version=2024-11-30"
    )
    headers = {
        "Ocp-Apim-Subscription-Key": api_key,
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(analyze_url, headers=headers, json={"base64Source": b64})
        if resp.status_code not in (200, 202):
            raise RuntimeError(
                f"Azure DI analyze POST failed: HTTP {resp.status_code} — {resp.text[:200]}"
            )

        operation_url = (
            resp.headers.get("Operation-Location")
            or resp.headers.get("operation-location", "")
        )
        if not operation_url:
            raise RuntimeError("Azure DI: no Operation-Location header in response")

        # Poll for completion (max 12 × 5 s = 60 s)
        result: dict = {}
        for _ in range(12):
            _sleep(5)
            poll = client.get(
                operation_url, headers={"Ocp-Apim-Subscription-Key": api_key}
            )
            if poll.status_code != 200:
                raise RuntimeError(f"Azure DI polling failed: HTTP {poll.status_code}")
            result = poll.json()
            status = result.get("status", "")
            if status == "succeeded":
                break
            if status == "failed":
                raise RuntimeError(
                    f"Azure DI analysis failed: {result.get('error', {})}"
                )
        else:
            raise RuntimeError("Azure DI: timed out waiting for analysis result")

    pages = result.get("analyzeResult", {}).get("pages", [])
    words_text: list[str] = []
    confidences: list[float] = []
    for page in pages:
        for word in page.get("words", []):
            words_text.append(word.get("content", ""))
            confidences.append(float(word.get("confidence", 1.0)))

    text = " ".join(words_text)
    cer = (
        1.0 - (sum(confidences) / len(confidences))
        if confidences
        else _estimate_cer_from_text(text)
    )

    logger.info({
        "event": "ocr_stage2_azure_di_completed",
        "words_extracted": len(words_text),
        "cer": round(cer, 4),
        "url": "",
        "economy": "",
    })
    return text, cer


# ── ST4: Mistral OCR ───────────────────────────────────────────────────────────

def run_mistral_ocr(image_bytes: bytes, timeout: int = 60) -> tuple[str, float]:
    """
    Call Mistral OCR API (vision-based extraction).
    Returns (text, cer_estimate).
    Raises RuntimeError when MISTRAL_API_KEY missing or API call fails.
    """
    api_key = os.getenv("MISTRAL_API_KEY", "")
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY must be set for Stage 2 Mistral OCR")

    b64 = base64.b64encode(image_bytes).decode()
    data_url = f"data:image/png;base64,{b64}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "mistral-ocr-latest",
        "document": {"type": "image_url", "image_url": data_url},
    }

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(
            "https://api.mistral.ai/v1/ocr",
            headers=headers,
            json=payload,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Mistral OCR failed: HTTP {resp.status_code} — {resp.text[:200]}"
            )

    result = resp.json()
    pages_data = result.get("pages", [])
    text = (
        "\n\n".join(p.get("markdown", "") for p in pages_data)
        if pages_data
        else result.get("text", "")
    )
    cer = _estimate_cer_from_text(text)

    logger.info({
        "event": "ocr_stage2_mistral_completed",
        "text_length": len(text),
        "cer_estimate": round(cer, 4),
        "url": "",
        "economy": "",
    })
    return text, cer


# ── ST5: Stage 2 Controller ────────────────────────────────────────────────────

def _azure_configured() -> bool:
    """True only when Azure DI has a key AND a real (non-placeholder) endpoint.

    A leftover placeholder like `AZURE_DI_ENDPOINT=https://...` is non-empty but has
    no real host, so it's treated as unconfigured — this stops the idna crash that
    otherwise fired on every page. Guards the whole Azure tier."""
    key = os.getenv("AZURE_DI_KEY", "").strip()
    endpoint = os.getenv("AZURE_DI_ENDPOINT", "").strip().rstrip("/")
    if not key or not endpoint:
        return False
    parsed = urllib.parse.urlparse(endpoint)
    # Real endpoint = http(s) scheme + a host with at least one non-dot label.
    return parsed.scheme in ("http", "https") and bool(parsed.netloc.strip("."))


def _route_stage2(image_bytes: bytes) -> tuple[str, float, str]:
    """
    Per-page cloud cascade for a single image: Mistral → Azure (if configured).
    Returns (text, cer, engine_used). Raises RuntimeError if all providers fail.
    """
    errors: list[str] = []

    if os.getenv("MISTRAL_API_KEY"):
        try:
            text, cer = run_mistral_ocr(image_bytes)
            return text, cer, "mistral_ocr"
        except Exception as exc:
            errors.append(f"mistral_ocr: {exc}")
            logger.warning({
                "event": "ocr_stage2_mistral_failed",
                "error": str(exc),
                "url": "",
                "economy": "",
            })

    if _azure_configured():
        try:
            text, cer = run_azure_di(image_bytes)
            return text, cer, "azure_di"
        except Exception as exc:
            errors.append(f"azure_di: {exc}")
            logger.warning({
                "event": "ocr_stage2_azure_di_failed",
                "error": str(exc),
                "url": "",
                "economy": "",
            })

    raise RuntimeError(f"All Stage 2 OCR providers failed: {'; '.join(errors)}")


def maybe_stage2_fallback(
    cer: float,
    image_bytes: bytes,
    stage1_text: str,
    stage1_engine: str,
) -> OCRResult:
    """
    ST5 controller: triggers Stage 2 when CER >= 5%.
    Returns OCRResult with final text, CER, and engine used.
    """
    if cer < _CER_THRESHOLD:
        return OCRResult(text=stage1_text, cer=cer, engine_used=stage1_engine)

    logger.info({
        "event": "ocr_stage2_triggered",
        "stage1_cer": round(cer, 4),
        "stage1_engine": stage1_engine,
        "url": "",
        "economy": "",
    })

    try:
        text, post_cer, engine = _route_stage2(image_bytes)
        logger.info({
            "event": "ocr_stage2_completed",
            "engine": engine,
            "post_cer": round(post_cer, 4),
            "url": "",
            "economy": "",
        })
        return OCRResult(text=text, cer=post_cer, engine_used=engine, stage2_triggered=True)
    except RuntimeError as exc:
        logger.warning({
            "event": "ocr_stage2_all_providers_failed",
            "error": str(exc),
            "stage1_cer": round(cer, 4),
            "url": "",
            "economy": "",
        })
        return OCRResult(
            text=stage1_text,
            cer=cer,
            engine_used=stage1_engine,
            stage2_triggered=True,
            stage2_failed=True,
        )


# ── Main Stage 2 document entry point ─────────────────────────────────────────

def run_ocr_stage2(
    raw_bytes: bytes,
    zone1_result: "Zone1Result",
    economy_config: "EconomyConfig",
    stage1_cer: float,
    stage1_engine: str,
    stage1_text: str = "",
    is_segment: bool = False,
) -> FetchedDocument:
    """
    Full Stage 2 pipeline for a document that failed the CER gate.

    Converts raw_bytes to per-page images, runs the Azure DI → Mistral
    provider cascade on each page, assembles a FetchedDocument.
    """
    start = time.monotonic()

    is_pdf = b"%PDF" in raw_bytes[:512]
    images = pdf_to_images(raw_bytes) if is_pdf else [raw_bytes]
    page_count = len(images)

    page_texts: list[str] = []
    page_cers: list[float] = []
    engine_final = stage1_engine
    all_providers_failed = True

    for i, img_bytes in enumerate(images):
        try:
            text, cer, engine = _route_stage2(img_bytes)
            page_texts.append(text)
            page_cers.append(cer)
            engine_final = engine
            all_providers_failed = False
        except RuntimeError as exc:
            logger.warning({
                "event": "ocr_stage2_page_failed",
                "page": i + 1,
                "error": str(exc),
                "url": zone1_result.url,
                "economy": zone1_result.economy,
            })
            page_texts.append("")
            page_cers.append(1.0)

    mean_cer = sum(page_cers) / len(page_cers) if page_cers else stage1_cer

    has_content = any(t.strip() for t in page_texts)
    if has_content:
        full_text = assemble_pages(page_texts)
    else:
        full_text = stage1_text if stage1_text.strip() else " "

    flag_for_review = mean_cer >= _CER_THRESHOLD or all_providers_failed
    flag_reason: str | None = None
    if all_providers_failed:
        flag_reason = "Stage 2 OCR: all providers failed — using Stage 1 fallback text"
    elif mean_cer >= _CER_THRESHOLD:
        flag_reason = f"Stage 2 CER still above threshold: {mean_cer:.3f}"

    elapsed_ms = (time.monotonic() - start) * 1000
    cost_log = CostLogEntry(
        engine=engine_final,
        pages=page_count,
        cost_usd=0.0,
        processing_time_ms=elapsed_ms,
        cer_score=mean_cer,
    )

    doc = FetchedDocument(
        source_url=zone1_result.url,
        resolved_url=zone1_result.url,
        economy=zone1_result.economy,
        act_title=zone1_result.act_title,
        discovery_tag=zone1_result.discovery_tag,
        archive_url=zone1_result.archive_url,
        doc_type="SCANNED_PDF" if is_pdf else "IMAGE",
        extraction_method=engine_final,  # type: ignore[arg-type]
        page_count=page_count,
        raw_text=full_text,
        section_hierarchy=[],
        cer_score=mean_cer,
        is_segment=is_segment,
        flag_for_review=flag_for_review,
        flag_reason=flag_reason,
        cost_log_entry=cost_log,
    )

    logger.info({
        "event": "ocr_stage2_document_completed",
        "url": zone1_result.url,
        "engine": engine_final,
        "mean_cer": round(mean_cer, 4),
        "flag_for_review": flag_for_review,
        "economy": zone1_result.economy,
    })
    return doc


# ── Cloud-first OCR cascade ─────────────────────────────────────────────────────
# Mistral (whole-doc → per-page retry) → Azure (if configured) → LLM-vision.
# Returns a FetchedDocument, or None when no cloud tier could produce text — the
# router then falls to the local Tesseract/Paddle floor.

def _mistral_whole_pdf(pdf_bytes: bytes, timeout: int = 300) -> tuple[str, int]:
    """OCR a whole PDF in ONE call via Mistral's Files API (upload → signed URL →
    OCR). Returns (markdown_text, page_count). Avoids rasterizing + 100× round-trips."""
    api_key = os.environ["MISTRAL_API_KEY"]
    auth = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(timeout=timeout) as client:
        up = client.post(
            "https://api.mistral.ai/v1/files",
            headers=auth,
            files={"file": ("document.pdf", pdf_bytes, "application/pdf")},
            data={"purpose": "ocr"},
        )
        up.raise_for_status()
        file_id = up.json()["id"]

        signed = client.get(
            f"https://api.mistral.ai/v1/files/{file_id}/url",
            headers=auth, params={"expiry": 1},
        )
        signed.raise_for_status()

        resp = client.post(
            "https://api.mistral.ai/v1/ocr",
            headers={**auth, "Content-Type": "application/json"},
            json={
                "model": "mistral-ocr-latest",
                "document": {"type": "document_url", "document_url": signed.json()["url"]},
            },
        )
        resp.raise_for_status()

    pages = resp.json().get("pages", [])
    text = "\n\n".join(p.get("markdown", "") for p in pages)
    return text, len(pages)


def _build_cloud_doc(
    zone1_result: "Zone1Result", text: str, pages: int, engine: str,
    cer: float, cost_usd: float, is_pdf: bool, is_segment: bool,
    flag_for_review: bool | None = None, flag_reason: str | None = None,
) -> FetchedDocument:
    """Assemble a validated FetchedDocument for a cloud OCR result (shared builder)."""
    if flag_for_review is None:
        flag_for_review = cer >= _CER_THRESHOLD
        flag_reason = f"OCR CER above threshold: {cer:.3f}" if flag_for_review else None
    doc = FetchedDocument(
        source_url=zone1_result.url,
        resolved_url=zone1_result.url,
        economy=zone1_result.economy,
        act_title=zone1_result.act_title,
        discovery_tag=zone1_result.discovery_tag,
        archive_url=zone1_result.archive_url,
        doc_type="SCANNED_PDF" if is_pdf else "IMAGE",
        extraction_method=engine,  # type: ignore[arg-type]
        page_count=pages,
        raw_text=text,
        section_hierarchy=[],
        cer_score=cer,
        is_segment=is_segment,
        flag_for_review=flag_for_review,
        flag_reason=flag_reason,
        cost_log_entry=CostLogEntry(
            engine=engine, pages=pages, cost_usd=cost_usd, processing_time_ms=0.0, cer_score=cer,
        ),
    )
    doc.validate()
    logger.info({
        "event": "ocr_cloud_completed", "engine": engine, "pages": pages,
        "cer": round(cer, 4), "cost_usd": round(cost_usd, 4),
        "flag_for_review": flag_for_review, "url": zone1_result.url,
        "economy": zone1_result.economy,
    })
    return doc


def _cloud_perpage(raw_bytes: bytes, zone1_result: "Zone1Result", is_pdf: bool, is_segment: bool):
    """Per-page Mistral → Azure cascade (whole-doc retry / image input). None if all fail."""
    images = pdf_to_images(raw_bytes) if is_pdf else [raw_bytes]
    texts: list[str] = []
    engine = "mistral_ocr"
    paid_pages = 0
    for img in images:
        try:
            text, _cer, engine = _route_stage2(img)
            texts.append(text)
            paid_pages += 1
        except RuntimeError:
            texts.append("")
    if not any(t.strip() for t in texts):
        return None
    full = assemble_pages(texts)
    rate = _MISTRAL_OCR_PRICE_PER_PAGE if engine == "mistral_ocr" else _AZURE_OCR_PRICE_PER_PAGE
    return _build_cloud_doc(
        zone1_result, full, len(images), engine,
        _estimate_cer_from_text(full), paid_pages * rate, is_pdf, is_segment,
    )


def _llm_vision_perpage(raw_bytes: bytes, zone1_result: "Zone1Result", is_pdf: bool, is_segment: bool):
    """LLM-vision OCR on the configured provider, page by page. None if unavailable/empty."""
    from src.fetcher.extractors.llm_ocr import LLMOCRUnavailableError, run_llm_ocr
    from src.output.cost_logger import compute_llm_cost

    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    images = pdf_to_images(raw_bytes) if is_pdf else [raw_bytes]
    texts: list[str] = []
    in_tok = out_tok = 0
    for img in images:
        try:
            text, _cer, i_tok, o_tok = run_llm_ocr(img)
            texts.append(text)
            in_tok += i_tok
            out_tok += o_tok
        except LLMOCRUnavailableError:
            return None  # provider can't do vision at all → skip the whole tier
        except Exception as exc:
            logger.warning({"event": "ocr_llm_vision_page_failed", "error": str(exc)[:200]})
            texts.append("")
    if not any(t.strip() for t in texts):
        return None
    full = assemble_pages(texts)
    return _build_cloud_doc(
        zone1_result, full, len(images), "llm_ocr",
        _estimate_cer_from_text(full), compute_llm_cost(provider, in_tok, out_tok),
        is_pdf, is_segment,
        flag_for_review=True,  # LLMs can hallucinate — always verify verbatim
        flag_reason="LLM-vision OCR — verify verbatim (hallucination risk)",
    )


def run_ocr_cloud(
    raw_bytes: bytes,
    zone1_result: "Zone1Result",
    economy_config: "EconomyConfig",
    is_segment: bool = False,
) -> FetchedDocument | None:
    """Cloud-first OCR cascade. Returns a FetchedDocument, or None when no cloud
    tier is configured/succeeds (caller falls to the local Tesseract/Paddle floor)."""
    is_pdf = b"%PDF" in raw_bytes[:512]

    # Tier 1 — Mistral: whole-doc single call, then per-page retry.
    if os.getenv("MISTRAL_API_KEY"):
        if is_pdf:
            try:
                text, pages = _mistral_whole_pdf(raw_bytes)
                if text.strip():
                    return _build_cloud_doc(
                        zone1_result, text, pages, "mistral_ocr",
                        _estimate_cer_from_text(text),
                        pages * _MISTRAL_OCR_PRICE_PER_PAGE, is_pdf, is_segment,
                    )
            except Exception as exc:
                logger.warning({
                    "event": "ocr_mistral_wholedoc_failed_retry_perpage",
                    "error": str(exc)[:200], "url": zone1_result.url,
                })
        doc = _cloud_perpage(raw_bytes, zone1_result, is_pdf, is_segment)
        if doc is not None:
            return doc

    # Tier 2 — Azure DI (only if truly configured).
    if _azure_configured():
        doc = _cloud_perpage(raw_bytes, zone1_result, is_pdf, is_segment)
        if doc is not None:
            return doc

    # Tier 3 — LLM-vision on the configured provider.
    from src.fetcher.extractors.llm_ocr import llm_vision_available
    if llm_vision_available():
        doc = _llm_vision_perpage(raw_bytes, zone1_result, is_pdf, is_segment)
        if doc is not None:
            return doc

    return None
