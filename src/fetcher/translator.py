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

import asyncio
import inspect
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

# Max chars per translation call. Kept well below the old 100 K: on Argos (CPU
# neural MT ~1-2 ms/char) a 100 K chunk took ~180 s and hit the worker timeout;
# ~25 K chunks finish in ~30-50 s and are translated in parallel across the pool.
# Still far under DeepL's 128 KB hard limit.
_CHUNK_SIZE = 25_000


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


def _lang_root(lang: str) -> str:
    """'ms', 'ms-MY', 'ms_MY' → 'ms' (Argos/DeepL use bare ISO-639-1 codes)."""
    return lang.lower().strip().replace("_", "-").split("-")[0]


# Argos Translate — offline neural MT, no API key or quota. Primary translator.
# Runs in SUBPROCESS workers (src.fetcher.argos_worker): Argos's ctranslate2/
# onnxruntime native runtime segfaults when co-resident with the RAG torch/faiss
# stack, so it must never be imported into the main pipeline process.
#
# A pool of PERSISTENT workers keeps the model loaded (no ~3.5s reload per call)
# and translates chunks in parallel (measured ~1.7-2x on multi-core). Pool size
# defaults to a fraction of the cores — each argos call already uses several
# cores, so a handful of workers saturates the machine.
_ARGOS_TIMEOUT = int(os.environ.get("ARGOS_TIMEOUT", "300"))
# ctranslate2 already uses all cores for a single translation, so running several
# workers in parallel oversubscribes the CPU and thrashes (measured 4x slower than
# one worker). Default to ONE persistent worker: model loaded once (no per-call
# reload), all cores per translation. Override with ARGOS_POOL_SIZE if a future
# GPU/host changes the tradeoff.
_ARGOS_POOL_SIZE = int(os.environ.get("ARGOS_POOL_SIZE", "0")) or 1


class _ArgosPool:
    """Pool of long-lived argos_worker subprocesses (model loaded once each)."""

    def __init__(self, size: int):
        self._size = size
        self._procs = [self._spawn() for _ in range(size)]

    @staticmethod
    def _spawn():
        import subprocess
        import sys
        return subprocess.Popen(
            [sys.executable, "-m", "src.fetcher.argos_worker", "--serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, bufsize=1,
        )

    def translate_many(self, texts: list[str], src: str) -> list[Optional[str]]:
        """Translate texts src→en across the worker pool in parallel. Each result is
        the English text, or None if that item failed (caller falls back per item).

        A hung worker must never freeze the pipeline: each read is bounded by
        _ARGOS_TIMEOUT. On timeout/EOF the worker is killed and its remaining items
        flow to other workers or to the DeepL/Google fallback. Dead workers are
        respawned at the start of the next batch."""
        import json
        import queue
        import select
        import threading

        # Respawn any worker that died in a previous batch.
        self._procs = [p if p.poll() is None else self._spawn() for p in self._procs]

        results: list[Optional[str]] = [None] * len(texts)
        work: "queue.Queue[tuple[int, str]]" = queue.Queue()
        for i, t in enumerate(texts):
            work.put((i, t))

        def run(proc) -> None:
            while True:
                try:
                    i, text = work.get_nowait()
                except queue.Empty:
                    return
                try:
                    proc.stdin.write(json.dumps({"text": text, "source_lang": src}) + "\n")
                    proc.stdin.flush()
                    # Bounded wait — a stalled worker must not block th.join() forever.
                    ready, _, _ = select.select([proc.stdout], [], [], _ARGOS_TIMEOUT)
                    if not ready:
                        raise TimeoutError(f"argos worker no response in {_ARGOS_TIMEOUT}s")
                    line = proc.stdout.readline()
                    if not line:
                        raise EOFError("argos worker closed stdout")
                    r = json.loads(line)
                    results[i] = r["text"] if r.get("ok") and r.get("text", "").strip() else None
                except Exception as exc:
                    logger.warning({"event": "argos_translate_failed", "source_lang": src,
                                    "error": str(exc)[:200]})
                    results[i] = None
                    # Worker is hung or desynced — kill it and stop; its remaining
                    # queue items are handled by other workers or the caller's fallback.
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    return
        threads = [threading.Thread(target=run, args=(p,), daemon=True) for p in self._procs]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        return results

    def close(self) -> None:
        for p in self._procs:
            try:
                p.stdin.close()
                p.terminate()
            except Exception:
                pass


_argos_pool: "Optional[_ArgosPool]" = None


def _get_argos_pool() -> "_ArgosPool":
    global _argos_pool
    if _argos_pool is None:
        _argos_pool = _ArgosPool(_ARGOS_POOL_SIZE)
        import atexit
        atexit.register(_argos_pool.close)
    return _argos_pool


def ensure_argos_langs(economy_config) -> None:
    """Pre-download the Argos <lang>→en models this economy needs (from its declared
    languages), so the first Malay document doesn't stall on a mid-run download.
    Config-driven, best-effort, runs in the subprocess worker — never imports Argos
    into the main process. Safe no-op when everything is already installed."""
    langs = sorted({_lang_root(x) for x in getattr(economy_config, "languages", [])
                    if not _is_english(x)})
    if not langs:
        return
    try:
        import json
        import subprocess
        import sys
        subprocess.run(
            [sys.executable, "-m", "src.fetcher.argos_worker"],
            input=json.dumps({"langs": langs}),
            capture_output=True, text=True, timeout=max(_ARGOS_TIMEOUT, 600),
        )
    except Exception as exc:
        logger.warning({"event": "argos_bootstrap_failed", "error": str(exc)[:200]})


def _argos_translate_many(texts: list[str], source_lang: str) -> list[Optional[str]]:
    """Translate several texts source→en in parallel via the persistent pool."""
    try:
        return _get_argos_pool().translate_many(texts, _lang_root(source_lang))
    except Exception as exc:
        logger.warning({"event": "argos_translate_failed", "source_lang": source_lang,
                        "error": str(exc)[:200]})
        return [None] * len(texts)


def _argos_translate(text: str, source_lang: str) -> Optional[str]:
    """Offline neural translation (source → English) via the persistent Argos worker
    pool. Returns None on any failure so the caller falls back to DeepL."""
    return _argos_translate_many([text], source_lang)[0]


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
        # googletrans >= 4.0 made translate() async — it returns a coroutine that
        # must be awaited; 3.x returns the result directly. Support both.
        if inspect.iscoroutine(result):
            result = asyncio.run(result)
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

    # 1. Argos Translate — offline, free, no quota. Primary translator; DeepL is the
    #    fallback when Argos has no model for the language or errors.
    translated = _argos_translate(text, source_lang)
    if translated is not None:
        logger.info({
            "event": "translation_completed",
            "provider": "argos",
            "source_lang": source_lang,
            "chars": len(text),
            "cost_usd": 0.0,
            "elapsed_ms": round((time.monotonic() - t0) * 1000, 1),
        })
        return translated, "argos", 0.0

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
) -> tuple[str, str, float]:
    """
    Layer 2: translate act *title* to English.

    Returns (translated_title, provider, cost_usd). The provider is the one that
    actually did the translation (argos/deepl/google) so document provenance is
    accurate — the title is often the only thing translated on the multilingual
    (translate_body=False) path.
    """
    if _is_english(source_lang):
        return title, "none", 0.0
    return translate_text(title, source_lang, provider)


# ── ST4: Layer 3 — Full document ──────────────────────────────────────────────

def translate_document(
    doc: FetchedDocument,
    economy_config: "EconomyConfig",
    taxonomy_keywords: Optional[list[str]] = None,
    translate_body: bool = True,
) -> TranslatedDocument:
    """
    Run the full Z2-2 translation pipeline on *doc*.

    Execution order:
      1. Detect source language from economy_config.languages
      2. Layer 1 — translate taxonomy keywords (optional, for probe use)
      3. Layer 2 — translate act title + normalise
      4. Buddhist Era conversion (if economy_config.be_year_conversion)
      5. Layer 3 — translate full document text (chunked)
      6. Build TranslatedDocument with verbatim_original preserved

    English economies (e.g. Singapore) skip all translation calls.

    translate_body=False skips Layer 3 (the expensive full-document translation):
    the body stays in the source language. Used when RAG runs on the original text
    via a multilingual embedder and only retrieved passages need the source LLM —
    avoids translating ~1.5M chars to use ~10 retrieved passages.
    """
    # Derive the primary non-English language for this economy
    source_lang = next(
        (lang for lang in economy_config.languages if not _is_english(lang)),
        economy_config.languages[0],
    )
    provider = economy_config.translation_provider  # "deepl" | "google" | None
    provider_used = "none"
    total_cost = 0.0
    chars_translated = 0  # ACTUAL chars sent to translation (not the full doc size)
    be_conversions: list[tuple[str, int]] = []

    kw_list: list[str] = taxonomy_keywords or []

    # ── Layer 1: Keywords ─────────────────────────────────────────────────────
    if kw_list and not _is_english(source_lang):
        kw_translated, kw_cost = translate_keywords(kw_list, source_lang, provider)
        total_cost += kw_cost
        chars_translated += sum(len(k) for k in kw_list)
        if kw_cost > 0 or provider == "google":
            provider_used = provider or "deepl"
    else:
        kw_translated = kw_list

    # ── Layer 2: Act title ────────────────────────────────────────────────────
    if not _is_english(source_lang):
        title_en, title_prov, title_cost = translate_act_title(doc.act_title, source_lang, provider)
        total_cost += title_cost
        chars_translated += len(doc.act_title)
        if title_prov not in ("none", "failed"):
            provider_used = title_prov  # the provider that ACTUALLY translated
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

    if not translate_body:
        # RAG runs on the original text (multilingual retrieval) — skip the expensive
        # full-document translation. Use doc.raw_text (the EXACT source), NOT
        # text_for_l3: for a Buddhist-Era economy text_for_l3 has years converted
        # (2567→2024), which would corrupt verbatim snippets extracted from these
        # chunks. BE conversions are still recorded for metadata below.
        translated_text = doc.raw_text
    elif not _is_english(source_lang) and text_for_l3.strip():
        chars_translated += len(text_for_l3)  # body IS translated here
        if len(text_for_l3) > _CHUNK_SIZE:
            chunks = [
                text_for_l3[i: i + _CHUNK_SIZE]
                for i in range(0, len(text_for_l3), _CHUNK_SIZE)
            ]
            # Translate all chunks through the Argos pool in parallel (one round-trip
            # for the whole document); fall back per-chunk to DeepL/Google only for
            # the chunks Argos couldn't handle.
            argos_parts = _argos_translate_many(chunks, source_lang)
            translated_parts: list[str] = []
            argos_used = False
            for chunk, argos_out in zip(chunks, argos_parts):
                if argos_out is not None:
                    translated_parts.append(argos_out)
                    argos_used = True
                else:
                    t, prov, cost = translate_text(chunk, source_lang, provider)
                    translated_parts.append(t)
                    total_cost += cost
                    if prov not in ("none", "failed"):
                        provider_used = prov
            if argos_used and provider_used == "none":
                provider_used = "argos"
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
        chars_translated=chars_translated,  # actual translated volume (title+kw+body)
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
