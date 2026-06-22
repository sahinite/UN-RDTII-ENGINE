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

def _route_stage2(image_bytes: bytes) -> tuple[str, float, str]:
    """
    Azure DI → Mistral OCR cascade for a single image.
    Returns (text, cer, engine_used).
    Raises RuntimeError if all providers fail.
    """
    errors: list[str] = []

    if os.getenv("AZURE_DI_KEY"):
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
