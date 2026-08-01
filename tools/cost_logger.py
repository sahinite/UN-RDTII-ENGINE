"""
Standalone cost measurement tool.

Usage:
    python tools/cost_logger.py --pdf data/benchmark/benchmark_50pages.pdf \
        --economy Singapore --pillar 6

Runs the full OCR + embedding + LLM pipeline on a single document and
reports measured (not estimated) per-component costs. Writes logs/cost_report.json.
Required by the hackathon rubric — judges verify these against the code.
"""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import argparse
import sys
import time
from pathlib import Path


def _run_ocr_stage(pdf_path: Path, economy_name: str, cost_logger) -> tuple[str, float | None]:
    """Run OCR on the PDF and record cost. Returns (extracted_text, cer).

    Cascade: Tesseract/PaddleOCR → pdfplumber (text-layer PDF) → LLM vision OCR.
    Mirrors the production fallback in extract_ocr_stage1 so cost figures are accurate
    regardless of whether setup.py was run.
    """
    import os

    from src.config.economy_config import load_economy
    from src.fetcher.extractors.ocr_stage1 import (
        DependencyError,
        assemble_pages,
        pdf_to_images,
        run_paddleocr,
        run_tesseract,
    )

    economy = load_economy(economy_name)
    raw_bytes = pdf_path.read_bytes()

    t0 = time.monotonic()
    images = pdf_to_images(raw_bytes)
    pages = len(images)

    page_texts: list[str] = []
    last_cer: float | None = None
    engine_used = economy.ocr_engine

    non_en = [l for l in (economy.languages or ["en"]) if l != "en"]
    paddle_lang = non_en[0] if non_en else "en"

    ocr_failed_with: Exception | None = None

    for img_bytes in images:
        try:
            if economy.ocr_engine == "tesseract":
                page_text, page_cer = run_tesseract(img_bytes)
            else:
                page_text, page_cer = run_paddleocr(img_bytes, lang=paddle_lang)
            page_texts.append(page_text)
            last_cer = page_cer
        except DependencyError as exc:
            ocr_failed_with = exc
            break

    # Fallback 1: pdfplumber (text-layer PDFs, no system install needed)
    if ocr_failed_with is not None:
        print(f"  [{economy.ocr_engine} not installed] falling back to pdfplumber...", flush=True)
        try:
            import io
            import pdfplumber
            with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                page_texts = [p.extract_text() or "" for p in pdf.pages]
                last_cer = 0.0
            engine_used = "pdfplumber"
            ocr_failed_with = None
        except Exception:
            pass  # pdfplumber also failed — continue to LLM fallback

    # Fallback 2: LLM vision OCR (uses provider from .env)
    if ocr_failed_with is not None:
        from src.fetcher.extractors.llm_ocr import LLMOCRUnavailableError, run_llm_ocr
        provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
        model = os.environ.get("LLM_MODEL", "").strip() or "unknown"
        print(f"  [pdfplumber failed] falling back to LLM vision OCR ({provider}/{model})...", flush=True)
        page_texts = []
        total_in_tok = 0
        total_out_tok = 0
        try:
            for img_bytes in images:
                page_text, page_cer, in_tok, out_tok = run_llm_ocr(img_bytes)
                page_texts.append(page_text)
                last_cer = page_cer
                total_in_tok += in_tok
                total_out_tok += out_tok
            engine_used = "llm_ocr"
        except LLMOCRUnavailableError as llm_err:
            print(f"  LLM vision OCR unavailable: {llm_err}", file=sys.stderr)
            print("  Run setup.py to install the OCR engine.", file=sys.stderr)

        elapsed_ms = (time.monotonic() - t0) * 1000
        if engine_used == "llm_ocr":
            cost_logger.record_llm_ocr_page(
                provider=provider,
                model=model,
                input_tokens=total_in_tok,
                output_tokens=total_out_tok,
                pages=pages,
                latency_ms=elapsed_ms,
            )
        text = assemble_pages(page_texts)
        return text, last_cer

    text = assemble_pages(page_texts)
    elapsed_ms = (time.monotonic() - t0) * 1000
    cost_logger.record_ocr_page(
        engine=engine_used,
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

    load_economy(economy_name)  # validate the economy exists (raises on unknown)
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

    for call_data in llm_cost_entry.per_indicator.values():
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
    text = ""
    try:
        text, _ = _run_ocr_stage(pdf_path, args.economy, cost_logger)
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
    ocr_note = f"{c['ocr']['pages_processed']} pages"
    if c['ocr'].get('input_tokens', 0) > 0:
        ocr_note += f", llm_ocr {c['ocr']['input_tokens']}+{c['ocr']['output_tokens']} tokens"
    print(f"  OCR cost         : ${c['ocr']['cost_usd']:.6f}  ({ocr_note})")
    print(f"  Embedding cost   : ${c['embedding']['cost_usd']:.6f}  (local model)")
    print(f"  LLM cost         : ${c['llm']['cost_usd']:.6f}  ({c['llm']['input_tokens']}+{c['llm']['output_tokens']} tokens)")
    print(f"  Crawling cost    : ${c['crawling']['cost_usd']:.6f}  (Crawl4AI, free)")
    print(f"  {'─'*46}")
    print(f"  TOTAL            : ${report['total_cost_usd']:.6f}")
    print(f"{'='*50}")
    print(f"\n  Full report: {report_path}")


if __name__ == "__main__":
    main()
