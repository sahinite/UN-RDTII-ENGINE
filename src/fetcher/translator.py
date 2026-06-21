"""
3-layer translation pipeline. [Z2-2 ST3, ST4, ST5]

Layer 1: Portal search keywords  → English  (translate_keywords)
Layer 2: Act titles              → English  (translate_act_title)
Layer 3: Full document text      → English  (translate_document)

Provider order: DeepL (DEEPL_API_KEY) → Google Translate fallback.
verbatim_original is always preserved alongside the translated text.
Buddhist Era (BE) years are converted to Gregorian before Layer 3 when
economy_config.be_year_conversion is True.
"""

from __future__ import annotations

import os
import re
import time
from typing import TYPE_CHECKING, Optional

from src.fetcher.logger import get_logger
from src.fetcher.models import FetchedDocument, TranslatedDocument, TranslationCostEntry

if TYPE_CHECKING:
    from src.config.economy_config import EconomyConfig

logger = get_logger("translator")

# DeepL Pro pricing: $20 per 1M characters
_DEEPL_CHAR_RATE_USD: float = 20.0 / 1_000_000

# Buddhist Era years fall in the 2400–2599 range (CE 1857–2056)
_BE_YEAR_RE = re.compile(r"\b(2[45]\d{2})\b")

# Max chars per translation call (DeepL hard limit is 128 KB; we stay safe at 100 K)
_CHUNK_SIZE = 100_000


# ── ST5: Buddhist Era conversion ───────────────────────────────────────────────

def convert_be_years(text: str) -> tuple[str, list[tuple[str, int]]]:
    """
    Replace Buddhist Era years in *text* with Gregorian equivalents.

    BE = CE + 543, so CE = BE − 543.
    Returns (converted_text, [(original_be_string, gregorian_year)]).
    """
    conversions: list[tuple[str, int]] = []

    def _replace(m: re.Match) -> str:
        be = int(m.group(1))
        ce = be - 543
        conversions.append((m.group(1), ce))
        return str(ce)

    return _BE_YEAR_RE.sub(_replace, text), conversions


# ── ST5: Law title normalisation ───────────────────────────────────────────────

def normalise_law_reference(title: str) -> str:
    """
    Strip Buddhist Era suffixes and normalise "No." formatting in *title*.

    Examples:
      "Act No.  123/2024 B.E."   → "Act No. 123/2024"
      "พระราชบัญญัติ พ.ศ."         → "พระราชบัญญัติ"
    """
    title = re.sub(r"\s*B\.E\.?\s*$", "", title, flags=re.IGNORECASE).strip()
    title = re.sub(r"\s*พ\.ศ\.\s*$", "", title).strip()
    title = re.sub(r"\bNo\.?\s*(\d)", r"No. \1", title)
    title = re.sub(r"\s+", " ", title)
    return title


# ── Provider helpers ───────────────────────────────────────────────────────────

def _is_english(lang: str) -> bool:
    return lang.lower().strip() == "en"


def _deepl_translate(text: str, source_lang: str) -> Optional[str]:
    """Attempt DeepL translation; return None on any failure."""
    api_key = os.environ.get("DEEPL_API_KEY", "")
    if not api_key:
        return None
    try:
        import deepl  # type: ignore[import-untyped]
        translator = deepl.Translator(api_key)
        result = translator.translate_text(
            text,
            source_lang=source_lang.upper(),
            target_lang="EN-US",
        )
        return result.text  # type: ignore[union-attr]
    except Exception as exc:
        logger.warning({
            "event": "deepl_failed",
            "source_lang": source_lang,
            "error": str(exc),
        })
        return None


def _google_translate(text: str, source_lang: str) -> Optional[str]:
    """Attempt Google Translate; return None on any failure."""
    try:
        from googletrans import Translator as _GT  # type: ignore[import-untyped]
        result = _GT().translate(text, src=source_lang, dest="en")
        return result.text  # type: ignore[union-attr]
    except Exception as exc:
        logger.warning({
            "event": "google_translate_failed",
            "source_lang": source_lang,
            "error": str(exc),
        })
        return None


# ── ST3: Core translation function ────────────────────────────────────────────

def translate_text(
    text: str,
    source_lang: str,
    provider: Optional[str] = None,
) -> tuple[str, str, float]:
    """
    Translate *text* from *source_lang* to English.

    Returns (translated_text, provider_used, cost_usd).
    If source is English or translation fails, returns the original text unchanged.
    provider: "deepl" | "google" | None  — None means try DeepL first.
    """
    if not text.strip() or _is_english(source_lang):
        return text, "none", 0.0

    t0 = time.monotonic()

    if provider != "google":
        translated = _deepl_translate(text, source_lang)
        if translated is not None:
            cost = len(text) * _DEEPL_CHAR_RATE_USD
            logger.info({
                "event": "translation_completed",
                "provider": "deepl",
                "source_lang": source_lang,
                "chars": len(text),
                "cost_usd": round(cost, 6),
                "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
            })
            return translated, "deepl", cost

    translated = _google_translate(text, source_lang)
    if translated is not None:
        logger.info({
            "event": "translation_completed",
            "provider": "google",
            "source_lang": source_lang,
            "chars": len(text),
            "cost_usd": 0.0,
            "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
        })
        return translated, "google", 0.0

    logger.error({
        "event": "translation_failed",
        "source_lang": source_lang,
        "chars": len(text),
        "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
    })
    return text, "failed", 0.0


# ── ST3: Layer 1 — Keywords ────────────────────────────────────────────────────

def translate_keywords(
    keywords: list[str],
    source_lang: str,
    provider: Optional[str] = None,
) -> tuple[list[str], float]:
    """
    Layer 1: translate *keywords* to English.

    Returns (translated_keywords, total_cost_usd).
    English source → passthrough, zero cost.
    """
    if _is_english(source_lang):
        return keywords, 0.0

    translated: list[str] = []
    total_cost = 0.0
    for kw in keywords:
        t, _, cost = translate_text(kw, source_lang, provider)
        translated.append(t)
        total_cost += cost
    return translated, total_cost


# ── ST3: Layer 2 — Act title ───────────────────────────────────────────────────

def translate_act_title(
    title: str,
    source_lang: str,
    provider: Optional[str] = None,
) -> tuple[str, float]:
    """
    Layer 2: translate act *title* to English.

    Returns (translated_title, cost_usd).
    """
    if _is_english(source_lang):
        return title, 0.0
    t, _, cost = translate_text(title, source_lang, provider)
    return t, cost


# ── ST4: Layer 3 — Full document ──────────────────────────────────────────────

def translate_document(
    doc: FetchedDocument,
    economy_config: "EconomyConfig",
    taxonomy_keywords: Optional[list[str]] = None,
) -> TranslatedDocument:
    """
    Run the full Z2-2 translation pipeline on *doc*.

    Execution order:
      1. Detect source language from economy_config.languages
      2. Layer 1 — translate taxonomy keywords (optional, for probe use)
      3. Layer 2 — translate act title + normalise
      4. Buddhist Era conversion (if economy_config.be_year_conversion)
      5. Layer 3 — translate full document text (chunked at 100 K chars)
      6. Build TranslatedDocument with verbatim_original preserved

    English economies (e.g. Singapore) skip all translation calls.
    """
    # Derive the primary non-English language for this economy
    source_lang = next(
        (lang for lang in economy_config.languages if not _is_english(lang)),
        economy_config.languages[0],
    )
    provider = economy_config.translation_provider  # "deepl" | "google" | None
    provider_used = "none"
    total_cost = 0.0
    be_conversions: list[tuple[str, int]] = []

    kw_list: list[str] = taxonomy_keywords or []

    # ── Layer 1: Keywords ─────────────────────────────────────────────────────
    if kw_list and not _is_english(source_lang):
        kw_translated, kw_cost = translate_keywords(kw_list, source_lang, provider)
        total_cost += kw_cost
        if kw_cost > 0 or provider == "google":
            provider_used = provider or "deepl"
    else:
        kw_translated = kw_list

    # ── Layer 2: Act title ────────────────────────────────────────────────────
    if not _is_english(source_lang):
        title_en, title_cost = translate_act_title(doc.act_title, source_lang, provider)
        total_cost += title_cost
        if title_en != doc.act_title:
            provider_used = provider or "deepl"
    else:
        title_en = doc.act_title
    title_en = normalise_law_reference(title_en)

    # ── ST5: Buddhist Era conversion ──────────────────────────────────────────
    text_for_l3 = doc.raw_text
    if economy_config.be_year_conversion and doc.raw_text:
        text_for_l3, be_conversions = convert_be_years(doc.raw_text)
        if be_conversions:
            logger.info({
                "event": "be_year_converted",
                "economy": doc.economy,
                "count": len(be_conversions),
                "sample": be_conversions[:3],
            })

    # ── Layer 3: Full document text ───────────────────────────────────────────
    verbatim_original = doc.raw_text  # always preserve the source-language text

    if not _is_english(source_lang) and text_for_l3.strip():
        if len(text_for_l3) > _CHUNK_SIZE:
            chunks = [
                text_for_l3[i: i + _CHUNK_SIZE]
                for i in range(0, len(text_for_l3), _CHUNK_SIZE)
            ]
            translated_parts: list[str] = []
            for chunk in chunks:
                t, prov, cost = translate_text(chunk, source_lang, provider)
                translated_parts.append(t)
                total_cost += cost
                if prov not in ("none", "failed"):
                    provider_used = prov
            translated_text = "".join(translated_parts)
        else:
            translated_text, prov, cost = translate_text(text_for_l3, source_lang, provider)
            total_cost += cost
            if prov not in ("none", "failed"):
                provider_used = prov
    else:
        translated_text = text_for_l3  # English or empty — no call needed

    cost_entry = TranslationCostEntry(
        source_language=source_lang,
        provider=provider_used,
        chars_translated=len(doc.raw_text) if not _is_english(source_lang) else 0,
        cost_usd=round(total_cost, 6),
    )

    logger.info({
        "event": "document_translated",
        "economy": doc.economy,
        "source_lang": source_lang,
        "provider": provider_used,
        "chars": cost_entry.chars_translated,
        "cost_usd": cost_entry.cost_usd,
        "be_conversions": len(be_conversions),
    })

    return TranslatedDocument(
        fetched=doc,
        source_language=source_lang,
        translated_text=translated_text,
        act_title_translated=title_en,
        keywords_translated=kw_translated,
        verbatim_original=verbatim_original,
        translation_provider=provider_used,
        translation_cost_entry=cost_entry,
        be_year_conversions=be_conversions,
    )
