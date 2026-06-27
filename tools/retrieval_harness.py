"""
Offline retrieval harness — diagnoses WHERE a target provision is lost.

For each known recall-failure case it runs the REAL Zone 2 retrieval pipeline
(chunk → BM25 + dense → RRF fusion → cross-encoder rerank) against the cached
act PDF, locates the target provision's chunk(s), and reports the stage at which
recall breaks:

  1. CHUNKED?      target text survives extraction + chunking at all
  2. Recall@Fusion target chunk reaches the fused candidate pool
  3. Recall@Rerank target chunk reaches the reranked top-N (what the LLM sees)

This isolates the bottleneck: if the chunk never reaches the rerank top-N the
fault is retrieval; if it does but extraction still failed, the fault is the LLM
prompt/taxonomy. No LLM calls, no cost — runs in seconds on cached PDFs.

Usage:
    python tools/retrieval_harness.py
    python tools/retrieval_harness.py --case pdpa_dpo
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()  # load API keys from .env, as main.py does (needed for --llm)

from src.config.economy_config import load_economy
from src.fetcher.extractors.pdf_text import extract_text_pdf
from src.fetcher.models import Zone1Result
from src.retrieval.bm25_index import build_bm25
from src.retrieval.chunker import chunk_document
from src.retrieval.config import (
    BM25_TOP_K,
    DENSE_TOP_K,
    FUSION_TOP_K,
    RERANK_TOP_N,
    get_indicator,
)
from src.retrieval.embedder import build_index
from src.retrieval.fusion import rrf_fusion
from src.retrieval.reranker import rerank

_ARCHIVE = Path("outputs/archive")


@dataclass
class Case:
    name: str
    pdf: str               # cached PDF filename under outputs/archive/
    source_url: str
    act_title: str
    indicator_id: str
    target_section: str    # article_number the chunker should assign
    phrases: list[str]     # distinctive text of the target provision (any-match)


# The four confirmed recall failures (Singapore P7).
CASES = [
    Case("pdpa_dpo", "Act_PDPA2012_ViewType_Pdf.pdf",
         "https://sso.agc.gov.sg/Act/PDPA2012", "Personal Data Protection Act 2012",
         "P7-I4", "11",
         ["data protection officer", "responsible for ensuring", "designate an individual",
          "designate one or more individuals"]),
    Case("cpc_access", "Act_CPC2010_ViewType_Pdf.pdf",
         "https://sso.agc.gov.sg/Act/CPC2010", "Criminal Procedure Code 2010",
         "P7-I5", "39",
         ["access to", "decryption", "computer", "police officer may"]),
    Case("companies_retention", "Act_CoA1967_ViewType_Pdf.pdf",
         "https://sso.agc.gov.sg/Act/CoA1967", "Companies Act 1967",
         "P7-I3", "199",
         ["accounting records", "retained", "7 years", "preserved"]),
    Case("incometax_retention", "Act_ITA1947_ViewType_Pdf.pdf",
         "https://sso.agc.gov.sg/Act/ITA1947", "Income Tax Act 1947",
         "P7-I3", "67",
         ["keep records", "retain", "shall keep", "preserve"]),
    # Pillar 6: Companies Act local-storage requirement (6.2, score 0.5) — records
    # and registers must be kept at a place in Singapore.
    Case("companies_localstorage", "Act_CoA1967_ViewType_Pdf.pdf",
         "https://sso.agc.gov.sg/Act/CoA1967", "Companies Act 1967",
         "P6-I2", "199",
         ["kept in Singapore", "place in Singapore", "registered office within Singapore",
          "branch register"]),
]


def _is_target(chunk, case: Case) -> bool:
    art = (chunk.location_reference.article_number or "").lower()
    if art == case.target_section.lower():
        return True
    text = chunk.text.lower()
    return any(p.lower() in text for p in case.phrases)


def _rank_in(idx: int, ranked_indices: list[int]) -> int | None:
    """1-based rank of idx in the list, or None if absent."""
    for r, j in enumerate(ranked_indices, start=1):
        if j == idx:
            return r
    return None


def run_case(case: Case, economy_config) -> dict:
    pdf_path = _ARCHIVE / case.pdf
    if not pdf_path.exists():
        return {"name": case.name, "error": f"cached PDF missing: {pdf_path}"}

    z1 = Zone1Result(url=case.source_url, economy="SG", act_title=case.act_title,
                     discovery_tag="KNOWN", archive_url="")
    doc = extract_text_pdf(pdf_path.read_bytes(), z1, economy_config)
    chunks = chunk_document(doc)

    target_idxs = [i for i, c in enumerate(chunks) if _is_target(c, case)]

    indicator = get_indicator(case.indicator_id)
    query = indicator.legal_question + " " + " ".join(indicator.probe_keywords[:3])

    emb = build_index(chunks)
    bm25 = build_bm25(chunks)
    dense_results = emb.dense_search(query, top_k=DENSE_TOP_K)
    bm25_results = bm25.search(query, indicator, top_k=BM25_TOP_K)
    fused = rrf_fusion(bm25_results, dense_results, top_k=FUSION_TOP_K)
    fused_idxs = [idx for idx, _ in fused]

    # Full reranked order (top_n = all fused) so we can read the target's rank.
    reranked = rerank(query, fused, chunks, top_n=len(fused))
    rerank_idx_by_id = {c.chunk_id: i for i, c in enumerate(chunks)}
    reranked_idxs = [rerank_idx_by_id[rc.chunk.chunk_id] for rc in reranked]

    # Best (smallest) rank achieved by any target chunk at each stage.
    best_fusion = min((r for i in target_idxs if (r := _rank_in(i, fused_idxs))), default=None)
    best_rerank = min((r for i in target_idxs if (r := _rank_in(i, reranked_idxs))), default=None)

    return {
        "name": case.name,
        "indicator": case.indicator_id,
        "chunks": len(chunks),
        "target_chunks": len(target_idxs),
        "fused_size": len(fused),
        "rank_fusion": best_fusion,
        "rank_rerank": best_rerank,
        "in_top_n": (best_rerank is not None and best_rerank <= RERANK_TOP_N),
        "top_n_articles": [rc.chunk.location_reference.article_number or "?" for rc in reranked[:RERANK_TOP_N]],
    }


def probe_llm(case: Case, economy_config) -> None:
    """
    Run the REAL extraction path (retrieve → prompt → LLM → parse) for one case
    and show: the chunks the LLM sees (with article numbers), the raw LLM output,
    and what survives parsing. Isolates whether a miss is chunk-structure, the
    LLM declining, or the parser discarding. One real LLM call (~cents).
    """
    from src.retrieval.rag import retrieve
    from src.mapping.prompts import (SYSTEM_PROMPT, build_user_prompt,
                                     load_taxonomy_dict, trim_chunks_to_budget)
    from src.mapping.mapper import _build_doc_metadata
    from src.mapping.llm_client import call_llm_with_cascade, pin_active_provider
    from src.mapping.parser import parse_llm_response

    pin_active_provider()  # cascade requires a pinned provider (as main.py does)

    pdf_path = _ARCHIVE / case.pdf
    z1 = Zone1Result(url=case.source_url, economy="SG", act_title=case.act_title,
                     discovery_tag="KNOWN", archive_url="")
    doc = extract_text_pdf(pdf_path.read_bytes(), z1, economy_config)
    retrieved = retrieve(case.indicator_id, doc, top_n=RERANK_TOP_N)

    print(f"\n=== LLM PROBE: {case.name} [{case.indicator_id}] — {case.act_title} ===")
    print(f"\nChunks sent to LLM (article# | target? | text):")
    for i, rc in enumerate(retrieved, 1):
        art = rc.chunk.location_reference.article_number or "?"
        tgt = "TARGET" if _is_target(rc.chunk, case) else "      "
        snippet = " ".join(rc.chunk.text.split())[:80]
        print(f"  {i:2}. [{art:>8}] {tgt} {snippet}")

    taxonomy = load_taxonomy_dict()
    chunks_for_prompt = trim_chunks_to_budget(retrieved, SYSTEM_PROMPT)
    user_prompt = build_user_prompt(
        indicator_id=case.indicator_id, taxonomy=taxonomy,
        top_chunks=chunks_for_prompt, act_title=doc.act_title,
        economy=doc.economy, source_url=doc.source_url,
    )
    response = call_llm_with_cascade(system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt,
                                     max_tokens=1000, temperature=0.0)
    print(f"\nRAW LLM RESPONSE ({response.provider}/{response.model}):")
    print("  " + response.text.strip().replace("\n", "\n  ")[:1200])

    doc_metadata = _build_doc_metadata(doc)
    provisions = parse_llm_response(response, case.indicator_id, retrieved,
                                    doc_metadata, known_provisions=set())
    print(f"\nPARSED PROVISIONS ({len(provisions)} survived):")
    for p in provisions:
        print(f"  - {p.article} | law_name={p.law_name!r} | {(p.verbatim_snippet or '')[:60]}")
    if not provisions:
        print("  (none — LLM declined OR parser discarded all)")
    print()


def _verdict(r: dict) -> str:
    if r.get("error"):
        return "ERROR"
    if r["target_chunks"] == 0:
        return "LOST IN CHUNKING (target text not found in any chunk)"
    if r["rank_fusion"] is None:
        return "RETRIEVAL FAIL (target not in fused pool — widen candidate pool)"
    if not r["in_top_n"]:
        return f"RERANK FAIL (in pool @#{r['rank_fusion']} but reranked @#{r['rank_rerank']} > {RERANK_TOP_N})"
    return f"RETRIEVAL OK (rerank @#{r['rank_rerank']}) → LLM/mapping is the fault"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="run a single case by name")
    ap.add_argument("--llm", action="store_true",
                    help="run the real LLM extraction path (one call/case) instead of retrieval-only")
    args = ap.parse_args()

    economy_config = load_economy("Singapore")
    cases = [c for c in CASES if not args.case or c.name == args.case]

    if args.llm:
        for case in cases:
            probe_llm(case, economy_config)
        return

    print(f"\nRetrieval harness — BM25={BM25_TOP_K} DENSE={DENSE_TOP_K} "
          f"FUSION={FUSION_TOP_K} RERANK_TOP_N={RERANK_TOP_N}\n")
    for case in cases:
        r = run_case(case, economy_config)
        if r.get("error"):
            print(f"● {r['name']:22} {r['error']}")
            continue
        print(f"● {r['name']:22} [{r['indicator']}]  chunks={r['chunks']} "
              f"target_chunks={r['target_chunks']} fused={r['fused_size']}")
        print(f"    rank@fusion={r['rank_fusion']}  rank@rerank={r['rank_rerank']}  "
              f"in_top_{RERANK_TOP_N}={r['in_top_n']}")
        print(f"    top-{RERANK_TOP_N} articles seen by LLM: {r['top_n_articles']}")
        print(f"    VERDICT: {_verdict(r)}\n")


if __name__ == "__main__":
    main()
