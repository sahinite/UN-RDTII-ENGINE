"""
Two-pass discovery tagging (KNOWN/NEW) + ranking. [Z1-5]

Pipeline:
  ST2 — Discovery tag finalisation (KNOWN/NEW) after currency check
  ST3 — Layer 2 title/snippet translation for non-English economies
  ST4 — Semantic similarity + BM25 scoring (local, zero API cost)
  ST5 — Per-indicator exclusion filter (pre-LLM gate)
  ST6 — Score fusion + DeepSeek LLM gate + top 3-5 shortlisting
  ST7 — RankedAct output contract + ranking summary logs

Top 3-5 RankedAct objects per indicator hand off to Zone 2 (fetcher/router).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from src.crawler.crawler import _normalise_url as normalise_url
from src.crawler.currency import CurrencyResult
from src.crawler.seed_loader import SeedData, normalise_title
from src.fetcher.translator import translate_text as _shared_translate_text
from src.mapping.providers.groq_provider import GROQ_MODEL_DEFAULT as GROQ_MODEL, GROQ_MODEL_FALLBACK
from src.mapping.providers.ollama_provider import OLLAMA_MODELS

logger = logging.getLogger(__name__)

# ── Environment config ─────────────────────────────────────────────────────────

_SEMANTIC_WEIGHT = float(os.getenv("RANKER_SEMANTIC_WEIGHT", "0.6"))
_BM25_WEIGHT     = float(os.getenv("RANKER_BM25_WEIGHT",     "0.4"))
_RANKER_TOP_N    = int(os.getenv("RANKER_TOP_N",    "5"))
_GATE_TOP_N      = 20   # candidates sent to LLM gate
_GATE_JITTER_MS  = int(os.getenv("GATE_LLM_JITTER_MS", "200"))

# ── Sentence-transformer model (lazy singleton) ────────────────────────────────

_model: Any | None = None


def _get_model() -> Any:
    """Lazy-load sentence-transformers model — called once per process."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer  # type: ignore
        _model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _model


def _reset_model_cache() -> None:
    """Reset model singleton — used in tests."""
    global _model
    _model = None




# ── Exceptions ─────────────────────────────────────────────────────────────────

class RankerError(Exception):
    """Raised when no acts pass the LLM gate for an indicator."""


# ── Output contract ─────────────────────────────────────────────────────────────

@dataclass
class RankedAct:
    # Identity
    act_title: str
    act_title_original: str   # untranslated (same as act_title for English economies)
    act_url: str              # validated canonical URL
    document_type: str        # "pdf" | "html"
    economy: str
    pillar: str

    # Discovery
    discovery_tag: str        # "KNOWN" | "NEW"

    # Currency (forwarded from CurrencyResult)
    currency_status: str      # "in_force" | "uncertain"
    last_amended: str         # 4-digit year or ""
    archive_url: str          # Wayback snapshot URL or ""
    currency_note: str

    # Ranking
    indicator_id: str         # indicator this act is shortlisted for
    semantic_score: float     # 0.0–1.0
    bm25_score: float         # 0.0–1.0 (normalised)
    fused_score: float        # weighted sum
    llm_gate_verdict: str     # "PASS" | "FAIL" | "UNCERTAIN"
    ranker_rank: int          # 1 = top ranked for this indicator

    # Quality
    flag_for_review: bool
    ranker_notes: str


# ── ST2: Discovery Tag Finalisation ───────────────────────────────────────────

def resolve_discovery_tag(
    result: CurrencyResult,
    seed: SeedData,
) -> tuple[str, bool, str]:
    """
    Returns (discovery_tag, flag_for_review, note).

    KNOWN requires an exact match — never guess. Ambiguous title matches
    default to NEW to avoid false KNOWN tags (scoring penalty).
    """
    canonical_url   = normalise_url(result.act_url)
    canonical_title = normalise_title(result.act_title)

    if canonical_url in seed.known_urls:
        return "KNOWN", False, ""

    matching_known = [
        t for t in seed.known_titles
        if canonical_title.startswith(t) or t.startswith(canonical_title[:20])
    ]

    if len(matching_known) > 1:
        return "NEW", True, "Ambiguous title match — defaulting to NEW for safety"

    if len(matching_known) == 1:
        return "KNOWN", False, "URL changed since Round 1; title match used for KNOWN tag"

    return "NEW", False, ""


def _needs_translation(economy_config: Any) -> tuple[bool, str]:
    """
    Returns (should_translate, source_lang).
    Translation is needed when translation_provider is set on the economy config.
    """
    if hasattr(economy_config, "translation_provider"):
        provider = economy_config.translation_provider
        if not provider:
            return False, ""
        # First non-English language in languages list
        langs = getattr(economy_config, "languages", [])
        src_lang = next((l for l in langs if l != "en"), langs[0] if langs else "")
        return True, src_lang

    # dict-style economy_config
    if isinstance(economy_config, dict):
        provider = economy_config.get("translation_provider")
        if not provider:
            return False, ""
        langs = economy_config.get("languages", [])
        src_lang = next((l for l in langs if l != "en"), langs[0] if langs else "")
        return True, src_lang

    return False, ""


def _apply_translation(
    results: list[CurrencyResult],
    economy_config: Any,
) -> dict[str, tuple[str, str]]:
    """
    Returns {act_url: (translated_title, translated_snippet)} for scoring.
    For English economies, returns original values unchanged.
    """
    should_translate, src_lang = _needs_translation(economy_config)
    translations: dict[str, tuple[str, str]] = {}

    if not should_translate:
        for r in results:
            translations[r.act_url] = (r.act_title, r.description_snippet)
        return translations

    for r in results:
        title_en, _, _ = _shared_translate_text(r.act_title, src_lang)
        snippet_en, _, _ = _shared_translate_text(r.description_snippet[:500], src_lang)
        translations[r.act_url] = (title_en, snippet_en)

    return translations


# ── ST4: Semantic Similarity + BM25 Scorer ────────────────────────────────────

@dataclass
class ActIndicatorScore:
    act_url: str
    indicator_id: str
    semantic_score: float   # 0.0–1.0
    bm25_score: float       # 0.0–1.0 normalised


def _normalise_bm25(scores: np.ndarray) -> np.ndarray:
    # Clip negative BM25 scores to 0 (negative IDF means term too common — no discriminative value)
    scores = np.clip(scores, 0.0, None)
    max_score = float(scores.max())
    if max_score == 0.0:
        return np.zeros_like(scores)
    return scores / max_score


def _score_acts(
    results: list[CurrencyResult],
    translations: dict[str, tuple[str, str]],
    taxonomy: list[dict],
) -> list[ActIndicatorScore]:
    """
    Compute semantic + BM25 scores for every (act, indicator) pair.
    Model is loaded once. BM25 corpus is built once per run.
    """
    model = _get_model()
    from rank_bm25 import BM25Okapi  # type: ignore

    # Build act texts (translated)
    act_urls   = [r.act_url for r in results]
    act_texts  = [
        f"{translations[r.act_url][0]}. {translations[r.act_url][1][:500]}"
        for r in results
    ]

    # Batch-encode act texts once
    act_embeddings = model.encode(act_texts)

    # BM25 corpus (once per run)
    corpus_tokens = [t.lower().split() for t in act_texts]
    bm25 = BM25Okapi(corpus_tokens)

    all_scores: list[ActIndicatorScore] = []

    for indicator in taxonomy:
        iid = indicator["indicator_id"]
        legal_q = indicator.get("legal_question", "")
        probe_kws = indicator.get("probe_keywords", [])

        # Semantic scores
        q_embedding = model.encode([legal_q])[0]
        sem_scores = np.array([
            float(np.dot(act_embeddings[i], q_embedding) / (
                np.linalg.norm(act_embeddings[i]) * np.linalg.norm(q_embedding) + 1e-9
            ))
            for i in range(len(results))
        ])
        sem_scores = np.clip(sem_scores, 0.0, 1.0)

        # BM25 scores
        query_tokens = " ".join(probe_kws).lower().split()
        raw_bm25 = bm25.get_scores(query_tokens)
        bm25_norm = _normalise_bm25(np.array(raw_bm25, dtype=float))

        for i, url in enumerate(act_urls):
            all_scores.append(ActIndicatorScore(
                act_url=url,
                indicator_id=iid,
                semantic_score=float(sem_scores[i]),
                bm25_score=float(bm25_norm[i]),
            ))

    return all_scores


# ── ST5: Per-Indicator Exclusion Filter ───────────────────────────────────────

def is_excluded(act: CurrencyResult, indicator: dict) -> tuple[bool, str]:
    """
    Returns (excluded, reason). Excluded acts are never sent to the LLM gate.
    Adding a new rule requires only editing taxonomy.json — zero code changes.
    """
    act_text = f"{act.act_title} {act.description_snippet}".lower()
    norm_act_title = normalise_title(act.act_title)

    for excl_title in indicator.get("exclude_act_titles", []):
        if excl_title.lower() in norm_act_title:
            return True, f"Matched exclusion rule (title): {excl_title}"

    for excl_kw in indicator.get("exclude_keywords", []):
        if excl_kw.lower() in act_text:
            return True, f"Matched exclusion rule (keyword): {excl_kw}"

    return False, ""


def _write_exclusion_log(
    excluded: list[dict], economy: str, ts: str, output_dir: str
) -> None:
    if not excluded:
        return
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    path = Path(output_dir) / f"ranker_excluded_{economy}_{ts}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for entry in excluded:
            f.write(json.dumps(entry) + "\n")


# ── ST6: Score Fusion + LLM Gate ──────────────────────────────────────────────

def _fuse_scores(semantic: float, bm25: float) -> float:
    sem_w = float(os.getenv("RANKER_SEMANTIC_WEIGHT", str(_SEMANTIC_WEIGHT)))
    bm25_w = float(os.getenv("RANKER_BM25_WEIGHT", str(_BM25_WEIGHT)))
    return (sem_w * semantic) + (bm25_w * bm25)


def _parse_gate_response(raw: str) -> str:
    """
    Extract PASS or FAIL from an LLM response.
    Handles Qwen3's <think>...</think> chain-of-thought prefix by stripping it first.
    """
    # Strip Qwen3 chain-of-thought block before reading the verdict
    cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip().upper()
    if "PASS" in cleaned:
        return "PASS"
    if "FAIL" in cleaned:
        return "FAIL"
    return ""


def _call_llm_gate(act_title: str, description_snippet: str, indicator: dict) -> str:
    """
    Binary PASS/FAIL LLM gate using Qwen3-32b via Groq (free tier, Apache 2.0).
    Falls back to Qwen3.6-27b on Groq, then Ollama offline.
    Returns 'PASS', 'FAIL', or 'UNCERTAIN' on complete failure.
    """
    prompt = (
        "You are a legal relevance classifier for RDTII data collection.\n\n"
        f"Indicator: {indicator['indicator_id']} — {indicator.get('name', '')}\n"
        f"Legal question: {indicator.get('legal_question', '')}\n\n"
        f"Act title: {act_title}\n"
        f"Act summary: {description_snippet[:500]}\n\n"
        "Question: Does this act likely contain provisions that directly answer the legal question above?\n"
        "Answer with exactly one word: PASS or FAIL. No explanation."
    )

    # Primary + fallback: Qwen3 models via Groq (Apache 2.0, free tier)
    groq_key = os.getenv("GROQ_API_KEY", "")
    if groq_key:
        for model_id in (f"qwen/{GROQ_MODEL}", f"qwen/{GROQ_MODEL_FALLBACK}"):
            try:
                from groq import Groq  # type: ignore
                client = Groq(api_key=groq_key)
                resp = client.chat.completions.create(
                    model=model_id,
                    messages=[
                        {"role": "system", "content": "/no_think"},  # disable Qwen3 chain-of-thought
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=20,
                    temperature=0,
                )
                verdict = _parse_gate_response(resp.choices[0].message.content)
                if verdict:
                    return verdict
                logger.warning("GATE_AMBIGUOUS_RESPONSE from %s: %r — treating as FAIL",
                               model_id, resp.choices[0].message.content[:80])
                return "FAIL"
            except Exception as exc:
                logger.warning("Groq gate failed (%s): %s — trying next model", model_id, exc)

    # Offline fallback: Ollama (priority 4 model)
    try:
        import ollama  # type: ignore
        resp = ollama.chat(
            model=OLLAMA_MODELS[4],
            messages=[{"role": "user", "content": prompt}],
            options={"num_predict": 10, "temperature": 0},
        )
        raw = resp["message"]["content"]
        verdict = _parse_gate_response(raw)
        if verdict:
            return verdict
        logger.warning("GATE_AMBIGUOUS_RESPONSE (Ollama): %r — treating as FAIL", raw)
        return "FAIL"
    except Exception as exc:
        logger.error("All LLM gate providers failed: %s", exc)
        return "UNCERTAIN"


def _run_gate_for_indicator(
    candidates: list[dict],
    indicator: dict,
    output_dir: str,
    economy: str,
    ts: str,
    cost_entries: list[dict],
) -> list[dict]:
    """
    Send top _GATE_TOP_N candidates through LLM gate sequentially.
    Returns candidates with 'llm_gate_verdict' set.
    """
    top_candidates = candidates[:_GATE_TOP_N]
    jitter_s = _GATE_JITTER_MS / 1000.0

    for cand in top_candidates:
        verdict = _call_llm_gate(
            cand["act_title"], cand["description_snippet"], indicator
        )
        cand["llm_gate_verdict"] = verdict

        # Estimate tokens for cost logging
        prompt_tokens = len(cand["act_title"].split()) + len(cand["description_snippet"].split()) + 60
        cost_entries.append({
            "component": "ranker_llm_gate",
            "provider": "groq_qwen3",
            "indicator_id": indicator["indicator_id"],
            "act_title": cand["act_title"],
            "prompt_tokens": prompt_tokens,
            "completion_tokens": 1,
            "cost_usd": 0.0,
        })

        if jitter_s > 0:
            time.sleep(jitter_s)

    return top_candidates


# ── ST7: Ranking logs + run_ranker() ──────────────────────────────────────────

def _write_summary_log(
    economy: str,
    pillar: str,
    total_in: int,
    excluded_count: int,
    gate_pass: int,
    gate_fail: int,
    final_acts: list[RankedAct],
    per_indicator: dict,
    ts: str,
    output_dir: str,
) -> None:
    summary = {
        "economy": economy,
        "pillar": pillar,
        "total_candidates_in": total_in,
        "excluded_by_filter": excluded_count,
        "sent_to_llm_gate": total_in - excluded_count,
        "llm_gate_pass": gate_pass,
        "llm_gate_fail": gate_fail,
        "final_ranked_acts": len(final_acts),
        "known_count": sum(1 for a in final_acts if a.discovery_tag == "KNOWN"),
        "new_count": sum(1 for a in final_acts if a.discovery_tag == "NEW"),
        "per_indicator": per_indicator,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    path = Path(output_dir) / f"ranker_summary_{economy}_{ts}.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info("[RANKER] %s %s: %d candidates → %d final acts (%d KNOWN, %d NEW)",
                economy, pillar, total_in, len(final_acts),
                summary["known_count"], summary["new_count"])


def _write_score_detail_log(
    score_rows: list[dict], economy: str, ts: str, output_dir: str
) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    path = Path(output_dir) / f"ranker_scores_{economy}_{ts}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for row in score_rows:
            f.write(json.dumps(row) + "\n")


def run_ranker(
    currency_results: list[CurrencyResult],
    seed_data: SeedData,
    taxonomy: list[dict],
    economy_config: Any,
    output_dir: str = "logs",
) -> list[RankedAct]:
    """
    Runs ST2 → ST3 → ST4 → ST5 → ST6 → ST7.

    Returns top 3–5 RankedAct per indicator_id.
    Total output = up to 5 acts × N indicators RankedAct objects.
    Raises RankerError if zero acts pass for any indicator.
    """
    economy = currency_results[0].economy if currency_results else "UNK"
    pillar  = currency_results[0].pillar  if currency_results else ""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Filter out broken acts — only in_force and uncertain proceed
    active_results = [
        r for r in currency_results
        if r.currency_status in ("in_force", "uncertain")
    ]

    if not active_results:
        raise RankerError(f"No active (in_force/uncertain) acts for {economy} {pillar}")

    # ── ST2: Resolve discovery tags ────────────────────────────────────────────
    tag_map: dict[str, tuple[str, bool, str]] = {}  # url → (tag, flag, note)
    for result in active_results:
        tag, flag, note = resolve_discovery_tag(result, seed_data)
        tag_map[result.act_url] = (tag, flag, note)

    # ── ST3: Layer 2 translation ───────────────────────────────────────────────
    translations = _apply_translation(active_results, economy_config)

    # ── ST4: Score all (act, indicator) pairs ─────────────────────────────────
    all_scores = _score_acts(active_results, translations, taxonomy)

    # Group scores by indicator
    score_index: dict[str, dict[str, ActIndicatorScore]] = {}
    for s in all_scores:
        score_index.setdefault(s.indicator_id, {})[s.act_url] = s

    score_rows: list[dict] = []  # for detail log

    # ── ST5+ST6+ST7: Per-indicator pipeline ───────────────────────────────────
    all_ranked: list[RankedAct] = []
    exclusion_log: list[dict] = []
    per_indicator_summary: dict = {}
    total_gate_pass = 0
    total_gate_fail = 0
    total_excluded = 0
    cost_entries: list[dict] = []

    for indicator in taxonomy:
        iid = indicator["indicator_id"]
        scores_for_ind = score_index.get(iid, {})

        # ST5: exclusion filter
        candidates_for_gate: list[dict] = []
        for result in active_results:
            excluded, excl_reason = is_excluded(result, indicator)
            s = scores_for_ind.get(result.act_url, ActIndicatorScore(result.act_url, iid, 0.0, 0.0))
            fused = _fuse_scores(s.semantic_score, s.bm25_score)

            score_rows.append({
                "act_url": result.act_url,
                "act_title": result.act_title,
                "indicator_id": iid,
                "semantic_score": round(s.semantic_score, 4),
                "bm25_score": round(s.bm25_score, 4),
                "fused_score": round(fused, 4),
                "excluded": excluded,
                "exclusion_reason": excl_reason,
            })

            if excluded:
                total_excluded += 1
                exclusion_log.append({
                    "act_title": result.act_title,
                    "act_url": result.act_url,
                    "indicator_id": iid,
                    "exclusion_reason": excl_reason,
                })
                continue

            candidates_for_gate.append({
                "act_url": result.act_url,
                "act_title": result.act_title,
                "description_snippet": result.description_snippet,
                "currency_status": result.currency_status,
                "document_type": result.document_type,
                "economy": result.economy,
                "pillar": result.pillar,
                "last_amended": result.last_amended,
                "archive_url": result.archive_url,
                "currency_note": result.currency_note,
                "semantic_score": s.semantic_score,
                "bm25_score": s.bm25_score,
                "fused_score": fused,
                "llm_gate_verdict": "",
            })

        # Sort by fused score descending before gate
        candidates_for_gate.sort(key=lambda x: x["fused_score"], reverse=True)

        # ST6: LLM gate
        gated = _run_gate_for_indicator(
            candidates_for_gate, indicator, output_dir, economy, ts, cost_entries
        )

        pass_acts  = [c for c in gated if c["llm_gate_verdict"] == "PASS"]
        fail_acts  = [c for c in gated if c["llm_gate_verdict"] == "FAIL"]
        total_gate_pass += len(pass_acts)
        total_gate_fail += len(fail_acts)

        # If too few PASS, include UNCERTAIN acts with flag
        if len(pass_acts) < 3:
            uncertain_acts = [c for c in gated if c["llm_gate_verdict"] == "UNCERTAIN"]
            pass_acts.extend(uncertain_acts)
            # Flag all for review
            for c in pass_acts:
                c["_flag"] = True

        if len(pass_acts) == 0:
            # Write what we have, then raise
            _write_score_detail_log(score_rows, economy, ts, output_dir)
            _write_summary_log(economy, pillar, len(active_results), total_excluded,
                               total_gate_pass, total_gate_fail, all_ranked,
                               per_indicator_summary, ts, output_dir)
            raise RankerError(
                f"No acts passed LLM gate for {iid} in {economy}"
            )

        top_n = int(os.getenv("RANKER_TOP_N", str(_RANKER_TOP_N)))
        min_n = 3
        shortlist = pass_acts[:max(top_n, min_n)]

        per_indicator_summary[iid] = {
            "top_act": shortlist[0]["act_title"],
            "fused_score": round(shortlist[0]["fused_score"], 4),
            "discovery_tag": tag_map.get(shortlist[0]["act_url"], ("NEW", False, ""))[0],
        }

        for rank, cand in enumerate(shortlist, start=1):
            url = cand["act_url"]
            disc_tag, disc_flag, disc_note = tag_map.get(url, ("NEW", False, ""))
            flag = cand.get("_flag", disc_flag) or disc_flag
            trans_title, _ = translations.get(url, (cand["act_title"], ""))

            notes_parts: list[str] = []
            if disc_note:
                notes_parts.append(disc_note)
            if cand.get("_flag"):
                notes_parts.append("Too few PASS acts — included with flag_for_review")

            all_ranked.append(RankedAct(
                act_title=trans_title,
                act_title_original=cand["act_title"],
                act_url=url,
                document_type=cand["document_type"],
                economy=cand["economy"],
                pillar=cand["pillar"],
                discovery_tag=disc_tag,
                currency_status=cand["currency_status"],
                last_amended=cand["last_amended"],
                archive_url=cand["archive_url"],
                currency_note=cand["currency_note"],
                indicator_id=iid,
                semantic_score=round(cand["semantic_score"], 4),
                bm25_score=round(cand["bm25_score"], 4),
                fused_score=round(cand["fused_score"], 4),
                llm_gate_verdict=cand["llm_gate_verdict"],
                ranker_rank=rank,
                flag_for_review=flag,
                ranker_notes="; ".join(notes_parts),
            ))

    # ── Write logs (always, even on partial failure) ───────────────────────────
    _write_exclusion_log(exclusion_log, economy, ts, output_dir)
    _write_score_detail_log(score_rows, economy, ts, output_dir)
    _write_summary_log(
        economy, pillar,
        len(active_results), total_excluded,
        total_gate_pass, total_gate_fail,
        all_ranked, per_indicator_summary, ts, output_dir,
    )

    # Write cost log
    if cost_entries:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        cost_path = Path(output_dir) / f"ranker_cost_{economy}_{ts}.jsonl"
        with open(cost_path, "w", encoding="utf-8") as f:
            for entry in cost_entries:
                f.write(json.dumps(entry) + "\n")

    return all_ranked
