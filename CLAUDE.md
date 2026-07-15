# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**RDTII Extraction Engine** — AI pipeline for digital trade regulatory analysis, built for the UN Global Hackathon. It crawls government legal portals, extracts and OCRs documents, then uses LLM-based RAG to map regulatory provisions to RDTII indicators.

The codebase is organized into two zones (see Architecture below): Zone 1 evidence
discovery and Zone 2 intelligent mapping.

**Build gate:** Phase 1 must achieve a fully-verified Singapore PDPA (Pillar 7) end-to-end run before expanding to other economies or acts.

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

### Zone 1 — Evidence Discovery (strategy-driven)
- `src/config/economy_config.py` — Pydantic `EconomyConfig` + `Portal` loader; reads `economies/{name}.yaml`
- `src/crawler/discover.py` — **primary entry point**: single `discover()` step driven by per-portal strategy fields in YAML (replaces old probe→crawl→currency→rank pipeline)
- `src/crawler/transport.py` — transport ladder: plain httpx → httpx+browser headers → Playwright stealth
- `src/crawler/seed_loader.py` — Round 1 DB xlsx + Sample CSV parser; builds KNOWN URL/title/provision sets
- `src/crawler/crawl4ai_runner.py` — Crawl4AI + Playwright stealth browser (shared singleton)
- `src/crawler/probe.py` — taxonomy loader + keyword probe (legacy; `discover.py` is the active path)
- `src/crawler/crawler.py` — BFS crawler (legacy; used only if discover() falls back)
- `src/crawler/currency.py` — currency/freshness check (legacy)
- `src/crawler/ranker.py` — ranking + LLM gate (legacy)

### Zone 2 — Intelligent Mapping
- `src/fetcher/router.py` — routes documents to PDF/HTML path; `segmenter.py` splits long docs; `pdf_endpoint` rewrite for portals with `fetch: pdf_endpoint`
- `src/fetcher/extractors/pdf_text.py` — pdfplumber text extraction + `legislation_meta.py` (law_number_ref/last_amended)
- `src/fetcher/extractors/ocr_stage1.py` — Tesseract/PaddleOCR with CER gate
- `src/fetcher/extractors/html_extractor.py` — BeautifulSoup + anchor map extraction
- `src/fetcher/extractors/llm_ocr.py` — LLM vision OCR fallback
- `src/ocr/processor.py` — two-stage OCR cascade (Stage 1 local → Stage 2 Azure DI/Mistral on CER≥5%)
- `src/retrieval/chunker.py`, `embedder.py`, `rag.py` — chunk → embed → BM25+dense hybrid RAG with cross-encoder rerank. Chunker has a hierarchy-coverage guard + raw-text furniture stripping that handles AU legislation.gov.au compilation PDFs (space-separated `6A` headings, per-page running headers/footers, multi-page Contents TOC)
- `src/mapping/mapper.py` — maps retrieved passages to RDTII indicators; provision-level KNOWN/NEW tagging
- `src/mapping/llm_client.py` — 7-tier provider cascade; one provider pinned per run via `LLM_PROVIDER` env var
- `src/mapping/parser.py` — LLM response parser with verbatim assertion + provision tag resolution
- `src/mapping/prompts.py` — system/user prompt builder with Rules 1–9
- `src/output/writer.py` — CSV (13-col UTF-8-BOM) + JSON (document-level + `provisions[]` envelope)
- `src/output/validator.py` — URL validation + Wayback/local archiving (deduped per URL)
- `src/output/cost_logger.py` — per-component cost accumulation → `logs/cost_report.json`
- `src/cli/progress.py` — single-line ANSI spinner with substep reporting

### Economy Configs
`economies/*.yaml` files declare per-economy: `economy_name`, `iso_code`, `un_name`, `script_type`, `languages`, `portals` list (unlimited), and optional `llm_override`, `translation_provider`, `ocr_engine_override`, `be_year_conversion`. Each `Portal` has strategy fields: `anti_bot`, `discovery`, `fetch`, `index_urls`, `pdf_view_suffix`, `api_base`, `api_collection`, `pdf_path_suffix`, `transport_fallback`. Singapore (`singapore.yaml`) is the reference; Australia (`australia.yaml`) is the API-driven reference. 10 economy files exist (SG, AU, MY, TH, VN, PH, KH, MM, LA, BN) — SG/AU/MY/TH have configured portals; the other six are scaffolds.

### LLM Cascade (pinned order, no mid-run switching)
1. `anthropic` / `claude-sonnet-4-20250514` (primary)
2. `openai` / `gpt-4o`
3. `deepseek` / `deepseek-chat` V3 (`DEEPSEEK_API_KEY`; OpenAI-compatible)
4. `groq` / `qwen/qwen3-32b` (fallback: `qwen/qwen3.6-27b`; free tier)
5. `qwen` / `qwen-plus` via DashScope intl (`DASHSCOPE_API_KEY`)
6. `ollama` / `qwen2.5:7b` (offline, Apache 2.0)
7. `ollama` / `granite3-8b` (offline, Apache 2.0)

> Llama 3.3 is explicitly excluded — non-Apache 2.0 license.

### Zone 1 Discovery Strategy (per-portal YAML-driven)
Each portal in `economies/*.yaml` declares `discovery` and `fetch` strategies (defined as Pydantic `Literal`s in `economy_config.py`):
- `discovery: auto` — **default**: best-effort adapter that probes the portal (SPA-vs-SSR) and picks a discovery path
- `discovery: index` — fetch browse index pages (`index_urls`), BM25-rank titles against pillar-scoped keywords, merge KNOWN seeds, apply taxonomy exclusions (Singapore SSO)
- `discovery: api` — query a portal's public OData/JSON API (`api_base`, `api_collection`) instead of scraping HTML (Australia FRL)
- `discovery: seed_only` — use only Round 1 KNOWN URLs
- `discovery: TBD` — portal skipped (not yet implemented)
- `fetch: pdf_endpoint` — rewrite act URL with `pdf_view_suffix` (e.g. `?ViewType=Pdf`) for text-layer PDF (Singapore)
- `fetch: api_versioned_pdf` — resolve the act's latest in-force version date via the API, then fetch the dated text-layer PDF using `pdf_path_suffix` (Australia)
- `anti_bot: header_spoof` — browser-like headers bypass 403 bot blocks
- `transport_fallback: playwright_stealth` — escalate to stealth Playwright if headers fail

### Cost Logging
`src/output/cost_logger.py` accumulates **actual** per-component costs (OCR / embedding / LLM / crawling) and writes `logs/cost_report.json`. `tools/cost_logger.py` is a standalone CLI for benchmarking. Hackathon judges verify these numbers against the code.

## Key Data Paths
- `data/sample_kit/` — Round 1 ground truth for evaluation
- `data/benchmark/` — benchmark PDF for cost logging
- `outputs/` — CSV/JSON run outputs (gitignored)
- `logs/` — run logs and cost reports (gitignored)

### IMPORTANT RULES:
- always update CONTEXT.md and remove any deprecated details from the file.

## For Evidence and requirement confirmation:
- always refer docs/README_template.md

## For output format and RULES
- for output values guides and rules always refer to docs/How_to_Complete_the_Output_Template.md 
- for json output always refer data/output_schema_sample.json
- for csv output always refer data/output_template/OUTPUT_TEMPLATE_31MAY.xlsx (Make sure to read it correctly)

## For RDTII Complete pillar definition/methodology and score critiera
- always refer to docs/RDTII_methodology_and_scoring_criteria.csv

## For Round 1 and 2 databases:
- for round 1 always throughly read the complete file from data/database/ESCAP-RDTII-2.1_ Round 1 Database.xlsx
- for round 2 always throughly read the complete file from data/database/ESCAP-RDTII-2.1_ Round 2 Database.xlsx

## VALIDATION
- Always check if seed data loaded correctly from Round 1 database of the asked economy.
- Always check if taxonomy.json has correct keywords and mapping.

## NOTE:
- dont provide long summary.
- your response should be easiy to understand in a way such that a 20 years can easily understand.
- try to give short/consice bullets in response.