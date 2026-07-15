"""
Per-indicator prompt builder for LLM extraction.

SYSTEM_PROMPT is shared across all indicators and providers.
build_user_prompt constructs the full user-facing prompt for one
(indicator × document) LLM call.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.retrieval.models import RetrievedChunk

logger = logging.getLogger("mapping.prompts")

SYSTEM_PROMPT = """You are a legal provision extraction assistant for the UN ESCAP Regional Digital Trade Integration Index (RDTII) database.

Your ONLY job is to extract verbatim text from the provided legal document chunks that directly answers a specific RDTII indicator question.

CRITICAL RULES — violations cause immediate disqualification of the row:
1. VERBATIM ONLY: Copy the exact text from the source. Do NOT paraphrase, summarise, or rephrase any part of the provision.
2. NO INVENTION: If no relevant provision exists in the provided chunks, return null for verbatim_snippet. Never fabricate text.
3. EXACT ARTICLE: The article field must cite the exact section/article/regulation number as it appears in the text, INCLUDING the sub-paragraph level (e.g. "Section 26(2)", "Regulation 4(1)(b)"). Never use bare article numbers like "Section 26" if sub-paragraphs exist.
4. RATIONALE CAP: mapping_rationale must be 300 characters or fewer.
5. CONFIDENCE: Score 0.00–1.00 reflecting how clearly the provision answers the indicator question. Below 0.80 means the mapping is uncertain.
6. JSON OUTPUT ONLY: Return a valid JSON object. No prose, no markdown fences, no explanation outside the JSON.
7. LAW NAME: If the act_title is abbreviated (all-caps acronym, under 20 characters, or ends without a year), expand it to the full official name including the enactment year. Example: expand "PDPA" to "Personal Data Protection Act 2012".
8. RATIONALE FORMAT: Use exactly this structure: "This [Art./s./Reg. X(Y)] [prohibits / requires / permits / establishes] [what]. Maps to [indicator_id] because [one-sentence legal logic]." Do NOT describe what the article is about. Do NOT restate the verbatim_snippet. Name the legal mechanism. You may add the indicator's short name in parentheses after the id, e.g. "Maps to P6-I1 (general restriction) because...".
9. RATIONALE BLANK IF UNCERTAIN: If you cannot give confident one-sentence legal logic for why this provision maps to the indicator, set mapping_rationale to "" (empty string). A blank rationale is neutral; a wrong or vague one misleads reviewers. Never pad with filler.

Output schema (return exactly this structure):
{
  "found": true | false,
  "provisions": [
    {
      "article": "Section 26(2)",
      "verbatim_snippet": "Exact copied text from source...",
      "mapping_rationale": "This s.26 requires data to be processed only on local systems. Maps to P6-I1 because it imposes a local-processing requirement on personal data.",
      "confidence": 0.95,
      "non_consecutive": false
    }
  ]
}

If found is false, provisions must be an empty array [].
If a provision spans non-consecutive sections, set non_consecutive: true and include BOTH article numbers in the article field (e.g. "Section 26(2) and Section 31(1)").
"""

MAX_PROMPT_TOKENS = 6000  # safe limit leaving room for response


def build_user_prompt(
    indicator_id: str,
    taxonomy: dict,
    top_chunks: list[RetrievedChunk],
    act_title: str,
    economy: str,
    source_url: str = "",
) -> str:
    """
    Builds the full user-facing prompt for one (indicator × document) call.
    taxonomy is a dict keyed by indicator_id with in_scope/out_of_scope/negative_examples.
    """
    if not act_title.strip():
        act_title = f"[Untitled document from {source_url}]" if source_url else "[Untitled document]"
        logger.debug({"event": "act_title_empty_substitution", "source_url": source_url})

    entry = taxonomy[indicator_id]

    indicator_block = _build_indicator_block(indicator_id, entry)
    chunks_block = _build_chunks_block(top_chunks)
    instruction = (
        f"Extract from the chunks above any provision from '{act_title}' ({economy}) "
        f"that directly answers the indicator question. "
        f"Return the JSON output schema defined in your system instructions."
    )

    return f"{indicator_block}\n\n{chunks_block}\n\n{instruction}"


def _build_indicator_block(indicator_id: str, entry: dict) -> str:
    negative_str = ""
    if entry.get("negative_examples"):
        neg_list = "\n".join(f"  - {ex}" for ex in entry["negative_examples"])
        negative_str = f"\nNEGATIVE EXAMPLES (these are NOT in scope — do not extract):\n{neg_list}"

    in_scope_items = entry.get("in_scope", [entry.get("legal_question", "")])
    in_scope = "\n".join(f"  - {s}" for s in in_scope_items)

    out_of_scope_items = entry.get("out_of_scope", [])
    out_scope_block = ""
    if out_of_scope_items:
        out_scope = "\n".join(f"  - {s}" for s in out_of_scope_items)
        out_scope_block = f"\nOUT OF SCOPE:\n{out_scope}"

    # Optional per-indicator decision rule, injected at runtime from taxonomy.json
    # (entry["extraction_guidance"]). Used where the in_scope/out_of_scope lists
    # alone are not applied strictly enough — a forceful, imperative boundary the
    # model reads last. Any indicator may define one; absent ones add nothing.
    guidance = (entry.get("extraction_guidance") or "").strip()
    guidance_block = f"\n\nDECISION RULE (apply strictly):\n{guidance}" if guidance else ""

    return (
        f"INDICATOR: {indicator_id} — {entry.get('name', '')}\n"
        f"LEGAL QUESTION: {entry.get('legal_question', '')}\n"
        f"IN SCOPE:\n{in_scope}"
        f"{out_scope_block}"
        f"{negative_str}"
        f"{guidance_block}"
    )


def _build_chunks_block(chunks: list[RetrievedChunk]) -> str:
    lines = ["SOURCE CHUNKS (top-5 most relevant, ranked by retrieval score):"]
    for i, rc in enumerate(chunks, start=1):
        c = rc.chunk
        loc = c.location_reference
        # Convert LocationReference object to string
        loc_str = _loc_to_str(loc)
        article_str = loc.article_number if loc.article_number else str(i)
        lines.append(
            f"\n[CHUNK {i} | Article {article_str} | {loc_str}]\n"
            f"{c.text.strip()}"
        )
    return "\n".join(lines)


def _loc_to_str(loc) -> str:
    """Convert LocationReference to a readable string."""
    parts = []
    if loc.part:
        parts.append(loc.part)
    if loc.article_number:
        parts.append(f"Art. {loc.article_number}")
    if loc.page is not None:
        parts.append(f"Page {loc.page + 1}")
    return " | ".join(parts) if parts else "unknown location"


def estimate_tokens(text: str) -> int:
    """Rough estimate: 1 token ≈ 4 chars for English legal text."""
    return len(text) // 4


def trim_chunks_to_budget(
    top_chunks: list[RetrievedChunk],
    system: str,
    max_tokens: int = MAX_PROMPT_TOKENS,
) -> list[RetrievedChunk]:
    """
    Progressively removes lowest-ranked chunks until prompt fits within budget.
    Always keeps top-1 chunk even if it alone exceeds budget.
    """
    chunks = list(top_chunks)
    while len(chunks) > 1:
        user_est = _build_chunks_block(chunks)
        if estimate_tokens(system) + estimate_tokens(user_est) <= max_tokens:
            break
        chunks.pop()
    return chunks


def load_taxonomy_dict() -> dict:
    """
    Loads taxonomy.json and returns as {indicator_id: entry_dict} for prompt building.
    Separate from retrieval.config.load_taxonomy which returns list[TaxonomyEntry].
    """
    import json
    path = Path(__file__).parent.parent.parent / "taxonomy.json"
    with open(path, encoding="utf-8") as fh:
        raw: list[dict] = json.load(fh)
    return {entry["indicator_id"]: entry for entry in raw}
