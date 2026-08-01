# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

**RDTII Extraction Engine** — AI pipeline for digital trade regulatory analysis, built for the UN Global Hackathon. It crawls government legal portals, extracts/OCRs documents, translates when needed, retrieves relevant passages with hybrid RAG, then maps legal provisions to RDTII indicators and writes reviewer-ready CSV/JSON outputs.

The codebase is organized into two zones wired by `main.py`:
- **Zone 1:** evidence discovery (`src/crawler/`)
- **Zone 2:** fetch, OCR/translate, retrieve, map, validate, and write (`src/fetcher/`, `src/ocr/`, `src/retrieval/`, `src/mapping/`, `src/output/`)

**Build gate:** Phase 1 remains a fully verified Singapore PDPA Pillar 7 end-to-end run. Use `RUN_PROFILE=gate` for KNOWN-only regression/gate work, and `RUN_PROFILE=submit` when a submission should include high-confidence NEW discoveries.

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python setup.py
cp .env.example .env   # fill in API keys
```

The runtime loads `.env` in `main.py`, `batch_run.py`, and `evaluate.py`. Prefer `LLM_PROVIDER` + `LLM_API_KEY`; provider-specific keys still work and override the generic key.

## Common Commands

```bash
# Run the engine for one economy/pillar
python main.py --economy Singapore --pillar 7

# Skip Zone 1 and run Zone 2 directly on a local PDF
python main.py --economy Malaysia --pillar 6 --pdf path/to/law.pdf

# Write only one output format
python main.py --economy Australia --pillar 7 --format json

# Evaluate against Round 1 ground truth
python evaluate.py --sample-kit data/sample_kit/ --economy Singapore
python evaluate.py --sample-kit data/sample_kit/ --economy Singapore --pillar 7 --csv outputs/file.csv

# Run multiple economies/pillars; default is one isolated subprocess lane per economy
python batch_run.py --economies Singapore Australia Malaysia --pillar 6 7
python batch_run.py --economies Singapore Australia Malaysia --pillar 6 7 --max-parallel 2

# Measure actual per-document cost
python tools/cost_logger.py --pdf data/benchmark/benchmark_50pages.pdf --economy Singapore --pillar 6

# Run all tests / one test file
pytest
pytest tests/test_economy_config.py
```

## Architecture

### Zone 1 — Evidence Discovery

- `src/config/economy_config.py` — Pydantic `EconomyConfig` + `Portal`; strict YAML loader for `economies/{name}.yaml`, ISO fallback, fuzzy suggestions, `extra="forbid"`.
- `src/crawler/discover.py` — active discovery entry point. Per-portal adapters emit raw `(title, url)` candidates, then a shared tail ranks with BM25, applies taxonomy exclusions, merges Round 1 seeds, tags KNOWN/NEW, canonicalizes act identity, and caps speculative NEW acts by run profile.
- `src/crawler/transport.py` — transport ladder: httpx/browser headers/Playwright stealth, gated by `anti_bot` and `transport_fallback`.
- `src/crawler/seed_loader.py` — loads Round 1 DB + sample portal CSV into `SeedData` (`known_urls`, `known_titles`, `known_provisions`, `known_sections`, `known_sections_by_indicator`).
- `src/crawler/crawl4ai_runner.py` — Playwright/Crawl4AI helpers; includes isolated fetches for JS rendering to avoid event-loop reuse hangs.
- `src/crawler/spa_probe.py` — SPA-vs-SSR classifier used by auto discovery and fetch routing.
- `src/crawler/probe.py` — taxonomy loader/validator (`load_taxonomy`, `validate_taxonomy`).
- `src/crawler/crawler.py` and `src/crawler/domains.py` — shared URL/link/domain helpers. The old probe -> BFS crawl -> currency -> rank pipeline is retired; do not reintroduce it.

Discovery strategies declared in YAML: `index`, `api`, `sitemap`, `auto`, `search`, `search_js`, `seed_only`, `TBD`.

### Zone 2 — Intelligent Mapping

- `src/fetcher/router.py` — routes `Zone1Result` to complete document text. Fetch strategies include `pdf_endpoint`, `api_versioned_pdf`, `html`, `html_wholedoc`, `html_js`, `pdf_link`, `auto`, `TBD`.
- `src/fetcher/extractors/` — text PDF (`pdf_text.py` + `legislation_meta.py`), scanned/local OCR (`ocr_stage1.py`), HTML (`html_extractor.py`), DOCX (`docx_text.py`), and LLM vision OCR fallback (`llm_ocr.py`).
- `src/ocr/processor.py` — cloud-first OCR cascade: Mistral whole-PDF, Mistral/Azure per-page, LLM vision, then local Tesseract/PaddleOCR floor.
- `src/fetcher/segmenter.py` — long-volume segmentation.
- `src/fetcher/translator.py` — translation pipeline: Argos offline primary in a persistent subprocess, then DeepL/Google fallbacks. `verbatim_original` stays source-language raw text.
- `src/retrieval/` — chunking, BM25, dense embeddings, RRF fusion, cross-encoder rerank, and batch retrieval. English-only economies use English models; non-English economies use multilingual models and usually skip full-document translation.
- `src/mapping/` — provision extraction and mapping. `llm_client.py` pins one provider per run, `parser.py` enforces JSON/verbatim checks, `provision_tag.py` resolves anchors and KNOWN/NEW tags, `prompts.py` contains Rules 1-9.
- `src/output/` — CSV/JSON writer, URL validation/archiving, output models, and cost logging. Archiving is Wayback best-effort with local snapshot fallback; JS-rendered pages should archive exact in-memory bytes via `archive_bytes`/`archive_ext`.
- `src/cli/progress.py` — single-line progress UI.

## Economy Configs

`economies/*.yaml` declares `economy_name`, `iso_code`, `un_name`, `script_type`, `languages`, `portals`, and optional overrides (`llm_override`, `translation_provider`, `ocr_engine_override`, `be_year_conversion`, `seed_url_remap`).

Configured economies:
- `economies/singapore.yaml` — SSO `index` + `html_wholedoc` + `header_spoof`; PDPC `sitemap` + `html_js`; Gazette `TBD`.
- `economies/australia.yaml` — legislation.gov.au `api` + `api_versioned_pdf`; OAIC portal support.
- `economies/malaysia.yaml` — multiple website/PDF portals.

Adding an economy should normally be YAML-only. Add Python only for a genuinely new portal category or extractor behavior, and keep the adapter surface declarative.

## LLM Cascade

Provider order is pinned in `src/mapping/llm_client.py`:
1. `anthropic` / `claude-sonnet-4-20250514`
2. `openai` / `gpt-4o` (gpt-5 and o-series request-shape support exists)
3. `gemini` / `gemini-2.5-flash`
4. `deepseek` / `deepseek-chat`
5. `groq` / `qwen/qwen3-32b` with `qwen/qwen3.6-27b` fallback
6. `qwen` / `qwen-plus` via DashScope
7. `ollama` / `qwen2.5:7b`
8. `ollama` / `granite3-8b`

One provider is pinned at startup via `LLM_PROVIDER` or auto-detection. `smoke_check_llm()` runs before extraction to catch dead keys, thinking-only empty responses, or unparseable output. Llama 3.3 is explicitly excluded because of license constraints.

## Important Environment Variables

- `LLM_PROVIDER` / `LLM_MODEL` / `LLM_API_KEY` — provider selection and model/key override.
- `RUN_PROFILE` — `gate` (KNOWN-only, default), `submit` (KNOWN + 3 NEW), `explore` (KNOWN + 8 NEW).
- `ZONE2_MAX_KNOWN_ACTS` / `ZONE2_MAX_NEW_ACTS` — explicit discovery caps.
- `ALLOW_UNVERIFIED_SNIPPETS` — defaults false; failed verbatim assertion discards the provision.
- `MISTRAL_API_KEY`, `AZURE_DI_KEY`, `AZURE_DI_ENDPOINT` — cloud OCR.
- `ARGOS_TIMEOUT`, `ARGOS_POOL_SIZE` — offline translation subprocess behavior.
- `WAYBACK_BEST_EFFORT`, `LOCAL_ARCHIVE_FALLBACK`, `LOCAL_ARCHIVE_DIR` — archiving behavior.
- `OLLAMA_NUM_CTX` — local-model context window; too small can produce empty outputs.
- `RDTII_LOG_DIR` — per-run log directory, set by `batch_run.py` for isolated subprocesses.

## Key Data Paths

- `data/database/ESCAP-RDTII-2.1_ Round 1 Database.xlsx` — primary Round 1 seed/ground-truth DB.
- `data/database/ESCAP-RDTII-2.1_ Round 2 Database.xlsx` — Round 2 DB; read thoroughly when relevant.
- `data/sample_kit/` — sample legislation kit and legacy fallback location.
- `data/output_template/OUTPUT_TEMPLATE_31MAY.xlsx` — authoritative CSV column/order template.
- `data/output_schema_sample.json` — JSON output reference.
- `taxonomy.json` — RDTII taxonomy consumed by discovery/retrieval/mapping.
- `outputs/` — generated CSV/JSON and local archive snapshots.
- `logs/` — run logs, diagnostics, and `cost_report.json`.

## Validation Rules For Agents

- Always update `CONTEXT.md` when architectural or process guidance changes, and remove deprecated details.
- For evidence and requirement confirmation, refer to `docs/README_template.md`.
- For output values and rules, refer to `docs/How_to_Complete_the_Output_Template.md`.
- For JSON output, refer to `data/output_schema_sample.json`.
- For CSV output, read `data/output_template/OUTPUT_TEMPLATE_31MAY.xlsx` correctly instead of guessing column order.
- For RDTII pillar definitions, methodology, and scoring criteria, refer to `docs/RDTII_methodology_and_scoring_criteria.csv`.
- For Round 1 and Round 2 database work, thoroughly read the relevant XLSX under `data/database/`.
- Before changing extraction/mapping behavior, check that seed data loads correctly for the requested economy and that `taxonomy.json` has the expected keywords/mapping.
- Keep responses short and easy to understand unless the user asks for deep detail.
