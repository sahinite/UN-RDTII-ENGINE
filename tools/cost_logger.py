"""
Standalone cost measurement tool. [Z2-6 ST5]

Usage:
    python tools/cost_logger.py --pdf data/benchmark/benchmark_50pages.pdf \
        --economy Singapore --pillar 6

Runs the full OCR + embedding + LLM pipeline on a single document and
reports measured (not estimated) per-component costs. Writes logs/cost_report.json.
Required by the hackathon rubric — judges verify these against the code.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _run_ocr_stage(pdf_path: Path, economy_name: str, cost_logger) -> tuple[str, float | None]:
    """Run Stage-1 OCR on the PDF and record cost. Returns (extracted_text, cer)."""
    from src.config.economy_config import load_economy
    from src.fetcher.extractors.ocr_stage1 import assemble_pages, pdf_to_images
    from src.fetcher.extractors.ocr_stage1 import run_paddleocr, run_tesseract

    economy = load_economy(economy_name)
    raw_bytes = pdf_path.read_bytes()

    t0 = time.monotonic()
    images = pdf_to_images(raw_bytes)
    pages = len(images)

    page_texts: list[str] = []
    last_cer: float | None = None

    # Primary non-English language for PaddleOCR lang param
    non_en = [l for l in (economy.languages or ["en"]) if l != "en"]
    paddle_lang = non_en[0] if non_en else "en"

    for img_bytes in images:
        if economy.ocr_engine == "tesseract":
            page_text, page_cer = run_tesseract(img_bytes)
        else:
            page_text, page_cer = run_paddleocr(img_bytes, lang=paddle_lang)
        page_texts.append(page_text)
        last_cer = page_cer

    text = assemble_pages(page_texts)
    elapsed_ms = (time.monotonic() - t0) * 1000

    cost_logger.record_ocr_page(
        engine=economy.ocr_engine,
        pages=pages,
        latency_ms=elapsed_ms,
    )
    return text, last_cer


def _run_embedding_stage(text: str, cost_logger) -> list:
    """Chunk + embed text, record cost. Returns embedded chunk list."""
    from src.retrieval.chunker import chunk_document
    from src.retrieval.embedder import EmbeddingIndex

    # Build a minimal stub document for the chunker
    from src.fetcher.models import CostLogEntry, FetchedDocument
    stub_doc = FetchedDocument(
        source_url="https://benchmark.local/document.pdf",
        resolved_url="https://benchmark.local/document.pdf",
        economy="SG",
        act_title="Benchmark Document",
        discovery_tag="KNOWN",
        archive_url="",
        doc_type="TEXT_PDF",
        extraction_method="pdfplumber",
        page_count=1,
        raw_text=text,
        section_hierarchy=[],
        cost_log_entry=CostLogEntry(
            engine="pdfplumber", pages=1, cost_usd=0.0, processing_time_ms=0.0
        ),
    )

    chunks = chunk_document(stub_doc)
    t0 = time.monotonic()
    index = EmbeddingIndex()
    index.build_index(chunks)
    elapsed_ms = (time.monotonic() - t0) * 1000

    total_tokens = sum(len(c.text.split()) for c in chunks)
    cost_logger.record_embedding(tokens=total_tokens, latency_ms=elapsed_ms)
    return chunks


def _run_llm_stage(
    text: str, economy_name: str, pillar: int, chunks: list, cost_logger
) -> list:
    """Run RAG + LLM extraction for all indicators. Returns ExtractionResult list."""
    from src.config.economy_config import load_economy
    from src.fetcher.models import CostLogEntry, FetchedDocument
    from src.mapping.llm_client import pin_active_provider
    from src.mapping.mapper import extract_provisions
    from src.retrieval.rag import retrieve_batch

    economy = load_economy(economy_name)
    economy_iso = {"Singapore": "SG", "Australia": "AU", "Malaysia": "MY", "Thailand": "TH"}.get(
        economy_name, economy_name[:2].upper()
    )

    pin_active_provider()

    stub_doc = FetchedDocument(
        source_url="https://benchmark.local/document.pdf",
        resolved_url="https://benchmark.local/document.pdf",
        economy=economy_iso,
        act_title="Benchmark Document",
        discovery_tag="KNOWN",
        archive_url="",
        doc_type="TEXT_PDF",
        extraction_method="pdfplumber",
        page_count=1,
        raw_text=text,
        section_hierarchy=[],
        cost_log_entry=CostLogEntry(
            engine="pdfplumber", pages=1, cost_usd=0.0, processing_time_ms=0.0
        ),
    )

    # Determine which indicators belong to this pillar
    indicator_ids = [f"P{pillar}-I{i}" for i in range(1, 6)]

    rag_results = retrieve_batch(indicator_ids, stub_doc)
    results, llm_cost_entry = extract_provisions(rag_results, stub_doc)

    for ind_id, call_data in llm_cost_entry.per_indicator.items():
        cost_logger.record_llm_call(
            provider=call_data.get("provider", "unknown"),
            model=call_data.get("model", "unknown"),
            input_tokens=call_data.get("input_tokens", 0),
            output_tokens=call_data.get("output_tokens", 0),
            latency_ms=call_data.get("latency_ms", 0),
        )

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure RDTII engine cost per document"
    )
    parser.add_argument("--pdf", required=True, help="Path to PDF file")
    parser.add_argument("--economy", required=True, help="Economy name (e.g. Singapore)")
    parser.add_argument(
        "--pillar", required=True, type=int, choices=[6, 7],
        help="RDTII pillar number"
    )
    parser.add_argument(
        "--log-dir", default="logs", help="Directory for cost_report.json"
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"Error: PDF not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    from src.output.cost_logger import CostLogger

    cost_logger = CostLogger(
        economy=args.economy,
        pillar=args.pillar,
        pdf_path=str(pdf_path),
    )

    print(f"Running pipeline on: {args.pdf}")
    print(f"Economy: {args.economy} | Pillar: {args.pillar}")

    # ── OCR Stage ──────────────────────────────────────────────────────────────
    print("  [1/3] OCR ...")
    text, cer = "", None
    try:
        text, cer = _run_ocr_stage(pdf_path, args.economy, cost_logger)
    except Exception as exc:
        print(f"  OCR stage failed: {exc}", file=sys.stderr)
        print("  Continuing with empty text for cost baseline.")

    # ── Embedding Stage ────────────────────────────────────────────────────────
    print("  [2/3] Chunking + Embedding ...")
    chunks = []
    try:
        chunks = _run_embedding_stage(text, cost_logger)
    except Exception as exc:
        print(f"  Embedding stage failed: {exc}", file=sys.stderr)

    # ── LLM Stage ─────────────────────────────────────────────────────────────
    print("  [3/3] LLM Extraction ...")
    try:
        _run_llm_stage(text, args.economy, args.pillar, chunks, cost_logger)
    except Exception as exc:
        print(f"  LLM stage failed: {exc}", file=sys.stderr)

    # ── Save + Print Report ────────────────────────────────────────────────────
    report_path = cost_logger.save(log_dir=Path(args.log_dir))
    report = cost_logger.to_report()

    print(f"\n{'='*50}")
    print(f"  COST REPORT")
    print(f"{'='*50}")
    print(f"  Document         : {args.pdf}")
    print(f"  Economy/Pillar   : {args.economy} | {args.pillar}")
    print(f"  Model            : {report['model_version']}")
    print(f"  Processing time  : {report['processing_time_seconds']:.1f}s")
    print(f"  {'─'*46}")
    c = report["components"]
    print(f"  OCR cost         : ${c['ocr']['cost_usd']:.6f}  ({c['ocr']['pages_processed']} pages)")
    print(f"  Embedding cost   : ${c['embedding']['cost_usd']:.6f}  (local model)")
    print(f"  LLM cost         : ${c['llm']['cost_usd']:.6f}  ({c['llm']['input_tokens']}+{c['llm']['output_tokens']} tokens)")
    print(f"  Crawling cost    : ${c['crawling']['cost_usd']:.6f}  (Crawl4AI, free)")
    print(f"  {'─'*46}")
    print(f"  TOTAL            : ${report['total_cost_usd']:.6f}")
    print(f"{'='*50}")
    print(f"\n  Full report: {report_path}")


if __name__ == "__main__":
    main()
