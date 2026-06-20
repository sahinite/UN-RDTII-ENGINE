# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**RDTII Extraction Engine** — AI pipeline for digital trade regulatory analysis, built for the UN Global Hackathon. It crawls government legal portals, extracts and OCRs documents, then uses LLM-based RAG to map regulatory provisions to RDTII indicators.

The codebase is largely scaffolded. Story IDs in comments (e.g. `[Z1-3]`, `[Z2-4]`) map 1:1 to ClickUp stories in the "UN ESCAP" board under epics ZONE 1 and ZONE 2.

**Build gate:** Phase 1 must achieve a fully-verified Singapore PDPA (Pillar 7) end-to-end run before expanding to other economies or acts. Follow story order: `[Z1-1]` → `[Z1-5]` → `[Z2-1]` → `[Z2-6]`.

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in API keys
```

## Common Commands

```bash
# Run the engine for one economy/pillar
python main.py --economy Singapore --pillar 7

# Evaluate against Round 1 ground truth
python evaluate.py --sample-kit data/sample_kit/ --economy Singapore

# Run across multiple economies (sequential)
python batch_run.py --economies Singapore Australia --pillar 6 7

# Measure actual per-document cost (required by hackathon rubric)
python tools/cost_logger.py --pdf data/benchmark/benchmark_50pages.pdf --economy Singapore --pillar 6

# Run all tests
pytest

# Run a single test file
pytest tests/test_economy_config.py
```

## Architecture

Two zones wired together in `main.py`:

### Zone 1 — Evidence Discovery
- `src/config/economy_config.py` — Pydantic `EconomyConfig` loader; reads `economies/{name}.yaml`
- `src/crawler/probe.py` — auto-probes government portals for document links
- `src/crawler/crawl4ai_runner.py` — Crawl4AI + Playwright-based document fetcher
- `src/crawler/currency.py` — checks document freshness/currency
- `src/crawler/ranker.py` — ranks fetched documents by relevance

### Zone 2 — Intelligent Mapping
- `src/fetcher/router.py` — routes documents to PDF/HTML path; `segmenter.py` splits long docs
- `src/ocr/processor.py` — two-stage OCR cascade (Tesseract for Latin, PaddleOCR for Asian scripts)
- `src/retrieval/chunker.py`, `embedder.py`, `rag.py` — chunk → embed → BM25+dense hybrid RAG
- `src/llm/client.py` — 5-tier provider cascade (Anthropic → OpenAI → Groq → Ollama ×2); one provider pinned per run via `LLM_PROVIDER` env var, fallback only on runtime failure
- `src/mapping/mapper.py` — maps retrieved passages to RDTII indicators
- `src/output/writer.py` — CSV/JSON output; `validator.py` checks URLs/citations

### Economy Configs
`economies/*.yaml` files declare per-economy: `script_type`, `languages`, `ocr_engine`, `portals` list (unlimited, no manual pillar tagging), and optional `llm_override`. Singapore (`sg.yaml`) is the reference. Thailand (`th.yaml`) is the second.

### LLM Cascade (pinned order, no mid-run switching)
1. `anthropic` / `claude-sonnet-4-20250514` (primary)
2. `openai` / `gpt-4o`
3. `groq` / `qwen3-32b` (DeepSeek no longer on Groq as of June 2026; fallback: `qwen3.6-27b`)
4. `ollama` / `qwen2.5:7b` (offline, Apache 2.0)
5. `ollama` / `granite3-8b` (offline, Apache 2.0)

> Llama 3.3 is explicitly excluded — non-Apache 2.0 license.

### Cost Logging
`tools/cost_logger.py` measures **actual** (not estimated) per-component costs (OCR / embedding / LLM / crawling) and writes `logs/cost_report.json`. Hackathon judges verify these numbers against the code.

## Key Data Paths
- `data/sample_kit/` — Round 1 ground truth for evaluation
- `data/benchmark/` — benchmark PDF for cost logging
- `outputs/` — CSV/JSON run outputs (gitignored)
- `logs/` — run logs and cost reports (gitignored)

### NOTE:
- always update CONTEXT.md once you done implementation.