"""
OCR two-stage cascade.

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


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _estimate_cer_from_text(text: str) -> float:
    """Heuristic CER from non-printable character ratio (no ground truth needed)."""
    if not text or not text.strip():
        return 1.0
    control_character_count = sum(
        1 for ch in text
        if unicodedata.category(ch) in ("Cc", "Cs", "Co", "Cn")
        and ch not in ("\n", "\t", "\r")
    )
    return min(control_character_count / len(text), 1.0)


# ── Azure Document Intelligence ──────────────────────────────────────────

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

    encoded_image = base64.b64encode(image_bytes).decode()
    analyze_url = (
        f"{endpoint}/documentintelligence/documentModels/prebuilt-read:analyze"
        "?api-version=2024-11-30"
    )
    headers = {
        "Ocp-Apim-Subscription-Key": api_key,
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=timeout) as client:
        response = client.post(analyze_url, headers=headers, json={"base64Source": encoded_image})
        if response.status_code not in (200, 202):
            raise RuntimeError(
                f"Azure DI analyze POST failed: HTTP {response.status_code} — {response.text[:200]}"
            )

        operation_url = (
            response.headers.get("Operation-Location")
            or response.headers.get("operation-location", "")
        )
        if not operation_url:
            raise RuntimeError("Azure DI: no Operation-Location header in response")

        # Poll for completion (max 12 × 5 s = 60 s)
        analysis_result: dict = {}
        for _ in range(12):
            _sleep(5)
            poll = client.get(
                operation_url, headers={"Ocp-Apim-Subscription-Key": api_key}
            )
            if poll.status_code != 200:
                raise RuntimeError(f"Azure DI polling failed: HTTP {poll.status_code}")
            analysis_result = poll.json()
            status = analysis_result.get("status", "")
            if status == "succeeded":
                break
            if status == "failed":
                raise RuntimeError(
                    f"Azure DI analysis failed: {analysis_result.get('error', {})}"
                )
        else:
            raise RuntimeError("Azure DI: timed out waiting for analysis result")

    pages = analysis_result.get("analyzeResult", {}).get("pages", [])
    extracted_words: list[str] = []
    confidences: list[float] = []
    for page in pages:
        for word in page.get("words", []):
            extracted_words.append(word.get("content", ""))
            confidences.append(float(word.get("confidence", 1.0)))

    text = " ".join(extracted_words)
    cer = (
        1.0 - (sum(confidences) / len(confidences))
        if confidences
        else _estimate_cer_from_text(text)
    )

    logger.info({
        "event": "ocr_stage2_azure_di_completed",
        "words_extracted": len(extracted_words),
        "cer": round(cer, 4),
        "url": "",
        "economy": "",
    })
    return text, cer


# ── Mistral OCR ───────────────────────────────────────────────────────────

def run_mistral_ocr(image_bytes: bytes, timeout: int = 60) -> tuple[str, float]:
    """
    Call Mistral OCR API (vision-based extraction).
    Returns (text, cer_estimate).
    Raises RuntimeError when MISTRAL_API_KEY missing or API call fails.
    """
    api_key = os.getenv("MISTRAL_API_KEY", "")
    if not api_key:
        raise RuntimeError("MISTRAL_API_KEY must be set for Stage 2 Mistral OCR")

    encoded_image = base64.b64encode(image_bytes).decode()
    image_data_url = f"data:image/png;base64,{encoded_image}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "mistral-ocr-latest",
        "document": {"type": "image_url", "image_url": image_data_url},
    }

    with httpx.Client(timeout=timeout) as client:
        response = client.post(
            "https://api.mistral.ai/v1/ocr",
            headers=headers,
            json=payload,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Mistral OCR failed: HTTP {response.status_code} — {response.text[:200]}"
            )

    result = response.json()
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


# ── Stage 2 Controller ────────────────────────────────────────────────────

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
    providers = []
    if os.getenv("MISTRAL_API_KEY"):
        providers.append(("mistral_ocr", run_mistral_ocr))
    if _azure_configured():
        providers.append(("azure_di", run_azure_di))

    errors: list[str] = []
    for engine_name, ocr_provider in providers:
        try:
            text, cer = ocr_provider(image_bytes)
            return text, cer, engine_name
        except Exception as exc:
            errors.append(f"{engine_name}: {exc}")
            logger.warning({
                "event": f"ocr_stage2_{engine_name}_failed",
                "error": str(exc),
                "url": "",
                "economy": "",
            })

    raise RuntimeError(f"All Stage 2 OCR providers failed: {'; '.join(errors)}")


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
        upload_response = client.post(
            "https://api.mistral.ai/v1/files",
            headers=auth,
            files={"file": ("document.pdf", pdf_bytes, "application/pdf")},
            data={"purpose": "ocr"},
        )
        upload_response.raise_for_status()
        file_id = upload_response.json()["id"]

        signed_url_response = client.get(
            f"https://api.mistral.ai/v1/files/{file_id}/url",
            headers=auth, params={"expiry": 1},
        )
        signed_url_response.raise_for_status()

        ocr_response = client.post(
            "https://api.mistral.ai/v1/ocr",
            headers={**auth, "Content-Type": "application/json"},
            json={
                "model": "mistral-ocr-latest",
                "document": {
                    "type": "document_url",
                    "document_url": signed_url_response.json()["url"],
                },
            },
        )
        ocr_response.raise_for_status()

    ocr_pages = ocr_response.json().get("pages", [])
    text = "\n\n".join(page.get("markdown", "") for page in ocr_pages)
    return text, len(ocr_pages)


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


def _run_cloud_ocr_per_page(
    raw_bytes: bytes,
    zone1_result: "Zone1Result",
    is_pdf: bool,
    is_segment: bool,
):
    """Per-page Mistral → Azure cascade (whole-doc retry / image input). None if all fail."""
    page_images = pdf_to_images(raw_bytes) if is_pdf else [raw_bytes]
    page_texts: list[str] = []
    selected_engine = "mistral_ocr"
    processed_pages = 0
    for page_image in page_images:
        try:
            text, _page_cer, selected_engine = _route_stage2(page_image)
            page_texts.append(text)
            processed_pages += 1
        except RuntimeError:
            page_texts.append("")
    if not any(text.strip() for text in page_texts):
        return None
    full_text = assemble_pages(page_texts)
    cost_per_page = (
        _MISTRAL_OCR_PRICE_PER_PAGE
        if selected_engine == "mistral_ocr"
        else _AZURE_OCR_PRICE_PER_PAGE
    )
    return _build_cloud_doc(
        zone1_result, full_text, len(page_images), selected_engine,
        _estimate_cer_from_text(full_text), processed_pages * cost_per_page,
        is_pdf, is_segment,
    )


def _run_llm_vision_per_page(
    raw_bytes: bytes,
    zone1_result: "Zone1Result",
    is_pdf: bool,
    is_segment: bool,
):
    """LLM-vision OCR on the configured provider, page by page. None if unavailable/empty."""
    from src.fetcher.extractors.llm_ocr import LLMOCRUnavailableError, run_llm_ocr
    from src.output.cost_logger import compute_llm_cost

    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    page_images = pdf_to_images(raw_bytes) if is_pdf else [raw_bytes]
    page_texts: list[str] = []
    input_tokens = output_tokens = 0
    for page_image in page_images:
        try:
            text, _page_cer, page_input_tokens, page_output_tokens = run_llm_ocr(page_image)
            page_texts.append(text)
            input_tokens += page_input_tokens
            output_tokens += page_output_tokens
        except LLMOCRUnavailableError:
            return None  # provider can't do vision at all → skip the whole tier
        except Exception as exc:
            logger.warning({"event": "ocr_llm_vision_page_failed", "error": str(exc)[:200]})
            page_texts.append("")
    if not any(text.strip() for text in page_texts):
        return None
    full_text = assemble_pages(page_texts)
    return _build_cloud_doc(
        zone1_result, full_text, len(page_images), "llm_ocr",
        _estimate_cer_from_text(full_text),
        compute_llm_cost(provider, input_tokens, output_tokens),
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
    input_is_pdf = b"%PDF" in raw_bytes[:512]

    # Tier 1 — Mistral: whole-doc single call, then per-page retry.
    if os.getenv("MISTRAL_API_KEY"):
        if input_is_pdf:
            try:
                text, page_count = _mistral_whole_pdf(raw_bytes)
                if text.strip():
                    return _build_cloud_doc(
                        zone1_result, text, page_count, "mistral_ocr",
                        _estimate_cer_from_text(text),
                        page_count * _MISTRAL_OCR_PRICE_PER_PAGE,
                        input_is_pdf, is_segment,
                    )
            except Exception as exc:
                logger.warning({
                    "event": "ocr_mistral_wholedoc_failed_retry_perpage",
                    "error": str(exc)[:200], "url": zone1_result.url,
                })
        cloud_document = _run_cloud_ocr_per_page(
            raw_bytes, zone1_result, input_is_pdf, is_segment
        )
        if cloud_document is not None:
            return cloud_document

    # Tier 2 — Azure DI (only if truly configured).
    if _azure_configured():
        cloud_document = _run_cloud_ocr_per_page(
            raw_bytes, zone1_result, input_is_pdf, is_segment
        )
        if cloud_document is not None:
            return cloud_document

    # Tier 3 — LLM-vision on the configured provider.
    from src.fetcher.extractors.llm_ocr import llm_vision_available
    if llm_vision_available():
        cloud_document = _run_llm_vision_per_page(
            raw_bytes, zone1_result, input_is_pdf, is_segment
        )
        if cloud_document is not None:
            return cloud_document

    return None
