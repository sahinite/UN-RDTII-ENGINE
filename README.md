# RDTII Extraction Engine

**UN Global Hackathon on AI for Digital Trade Regulatory Analysis**
Team: UN ESCAP | Round: 1 | Submission deadline: 20 July 2026 | Demo: 3 August 2026

An end-to-end AI pipeline that crawls government legal portals, extracts regulatory text via OCR/NLP, and maps provisions to RDTII indicators (Pillars 6 & 7) using a 7-tier LLM cascade with hybrid RAG retrieval.

---

## Prerequisites

Before running Quick Start, install system dependencies:

```bash
python setup.py
```

This script will:
- Install **Tesseract** (required — default OCR engine)
- Optionally pre-download the **sentence-transformers** embedding model (~90MB)
- Optionally install **Ollama** with offline models (~9GB) — only needed if you have no cloud API key

> **If you have any one of `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GROQ_API_KEY`, you can skip Ollama.**

### Manual install (if you prefer not to use setup.py)

| Dependency | Required | Install |
|---|---|---|
| Tesseract 5.3+ | **Yes** | macOS: `brew install tesseract` / Ubuntu: `sudo apt-get install -y tesseract-ocr` / Windows: [installer](https://github.com/UB-Mannheim/tesseract/wiki) |
| Ollama + models | No (offline only) | macOS: `brew install ollama` / Ubuntu: `curl -fsSL https://ollama.com/install.sh \| sh` |
| sentence-transformers model | No (auto-downloads on first run) | `python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"` |

---

## Quick start

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in API keys (see Configuration below)

# Run for Singapore PDPA (Pillar 7) — PDPA-first gate
python main.py --economy Singapore --pillar 7

# Run on a local PDF (skip crawler)
python main.py --economy Singapore --pillar 7 --pdf data/benchmark/benchmark_50pages.pdf

# Evaluate against Round 1 ground truth
python evaluate.py --sample-kit data/sample_kit/ --economy Singapore

# Measure per-document costs (required by rubric)
python tools/cost_logger.py --pdf data/benchmark/benchmark_50pages.pdf \
    --economy Singapore --pillar 7

# Batch run across economies and pillars
python batch_run.py --economies Singapore Australia Malaysia --pillar 6 7
```

---

## Project layout

```
src/config/      economy YAML schema + loader                [Z1-1]
src/crawler/     discover() strategy engine (index/api/auto) [Z1-2..Z1-5]
src/fetcher/     fetch + route + segment + translate         [Z2-1, Z2-2]
src/ocr/         OCR two-stage cascade (Tesseract/PaddleOCR + Azure DI/Mistral)
src/retrieval/   chunking, embedding, BM25+dense hybrid RAG  [Z2-3]
src/llm/         LLM cascade re-export (see src/mapping/)    [Z2-4]
src/mapping/     7-tier LLM cascade + indicator mapping      [Z2-4]
src/output/      CSV/JSON writer, URL validator, cost logger [Z2-5, Z2-6]
tools/           cost_logger.py — standalone cost benchmark  [Z2-6]
economies/       per-economy YAML configs (10 files)         [Z1-1]
tests/           pytest suite (mirrors src/ modules)
data/
  sample_kit/    Round 1 ground truth (evaluation input)
  benchmark/     benchmark_50pages.pdf (cost logger input)
  output_schema_sample.json — example output JSON envelope
outputs/         CSV/JSON run outputs (gitignored)
logs/            run logs + cost_report.json (gitignored)
```

---

## Supported economies

Ten economy YAML files exist. Four have configured portals (Singapore is the
HTML-index reference; Australia is the API-driven reference); the remaining six
are scaffolds carrying only economy metadata (portals `TBD`).

| Economy | YAML | Primary portal | Discovery | Script | Languages | Status |
|---------|------|----------------|-----------|--------|-----------|--------|
| Singapore | `singapore.yaml` | sso.agc.gov.sg | index | latin | en | Reference (PDPA-first gate) |
| Australia | `australia.yaml` | legislation.gov.au | api | latin | en | Live |
| Malaysia | `malaysia.yaml` | agc.gov.my | — | latin | ms, en | Configured |
| Thailand | `thailand.yaml` | ratchakitcha.soc.go.th | — | asian | th, en | Configured |
| Viet Nam | `vietnam.yaml` | — | — | latin | vi, en | Scaffold |
| Philippines | `philippines.yaml` | — | — | latin | fil, en | Scaffold |
| Cambodia | `cambodia.yaml` | — | — | khmer | km, en | Scaffold |
| Myanmar | `myanmar.yaml` | — | — | myanmar | my, en | Scaffold |
| Lao PDR | `laos.yaml` | — | — | lao | lo, en | Scaffold |
| Brunei Darussalam | `brunei.yaml` | — | — | latin | ms, en | Scaffold |

---

## Output format

Outputs are written to `outputs/{Economy}_P{pillar}_{timestamp}.csv` and `.json`.

**13-column CSV schema** (exact order from `OUTPUT_TEMPLATE_31MAY.xlsx`):

| Column | Required | Notes |
|--------|----------|-------|
| economy | Yes | UN official economy name |
| law_name | Yes | Full act title |
| law_number_ref | No | Act number (blank if none) |
| last_amended | No | Year of most recent amendment; blank if never amended |
| indicator_id | Yes | P6-I1 to P7-I5 |
| article | Yes | Section/Article reference |
| discovery_tag | Yes | KNOWN or NEW |
| location_reference | No | `(act, part, article)` citation |
| verbatim_snippet | Yes | Exact text from the act |
| mapping_rationale | No | Max 300 chars |
| source_url | Yes | Government portal URL |
| confidence | No | 0.00–1.00 |
| notes | No | Human review flags |

The JSON envelope groups records by `source_url` and adds 7 extended fields.
See `data/output_schema_sample.json` for a complete example.

---

## LLM cascade

Provider order is fixed (ADR-021, pinned once per run via `LLM_PROVIDER` env var):

| Tier | Provider | Model | Notes |
|------|----------|-------|-------|
| 1 | Anthropic | `claude-sonnet-4-20250514` | Recommended primary |
| 2 | OpenAI | `gpt-4o` | Fallback on API error |
| 3 | DeepSeek | `deepseek-chat` (V3) | OpenAI-compatible; `DEEPSEEK_API_KEY` |
| 4 | Groq | `qwen/qwen3-32b` (fallback: `qwen/qwen3.6-27b`) | Free tier |
| 5 | Qwen (DashScope) | `qwen-plus` | `DASHSCOPE_API_KEY` |
| 6 | Ollama | `qwen2.5:7b` | Offline, Apache 2.0 |
| 7 | Ollama | `granite3-dense:8b` | Offline, Apache 2.0 |

**Note:** Llama 3.3 is explicitly excluded (non-Apache 2.0 license).
Pin any provider with `LLM_PROVIDER`: `anthropic | openai | deepseek | groq | qwen | ollama`.

The LLM cascade implementation lives in `src/mapping/llm_client.py`.
`src/llm/client.py` re-exports the same public API for backwards compatibility.

### Swapping the LLM

Set `LLM_PROVIDER` in `.env`:
```bash
LLM_PROVIDER=anthropic   # use Anthropic as primary (recommended)
LLM_PROVIDER=deepseek    # use DeepSeek V3 as primary
LLM_PROVIDER=groq        # use Groq free tier as primary
LLM_PROVIDER=qwen        # use Qwen via DashScope as primary
LLM_PROVIDER=ollama      # use local Ollama as primary (offline mode)
```

### Adding a new LLM provider

1. Subclass `src/mapping/base_provider.py:BaseLLMProvider`
2. Implement `provider_name`, `model`, `is_available()`, `complete()`
3. Insert the new provider at the desired position in `PROVIDER_CASCADE` in `src/mapping/llm_client.py`
4. Add pricing constants to `src/output/cost_logger.py:_PROVIDER_PRICING`

---

## Configuration

Copy `.env.example` to `.env` and fill in your keys:

```bash
# Tier 1 — Anthropic (recommended)
ANTHROPIC_API_KEY=sk-ant-...

# Tier 2 — OpenAI
OPENAI_API_KEY=sk-...

# Tier 3 — DeepSeek (OpenAI-compatible)
DEEPSEEK_API_KEY=sk-...

# Tier 4 — Groq (free)
GROQ_API_KEY=gsk_...

# Tier 5 — Qwen via DashScope International
DASHSCOPE_API_KEY=sk-...

# Tier 6/7 — Ollama (offline)
# Run: ollama serve && ollama pull qwen2.5:7b

# OCR Stage 2 (optional, triggers when CER >= 5%)
AZURE_DI_KEY=...
AZURE_DI_ENDPOINT=https://...cognitiveservices.azure.com/
MISTRAL_API_KEY=...

# Translation (for non-English economies)
DEEPL_API_KEY=...

# Provider pin (optional — auto-detects if not set)
LLM_PROVIDER=anthropic
```

---

## Cost measurement

The hackathon rubric requires **measured** (not estimated) per-document costs.

```bash
python tools/cost_logger.py \
    --pdf data/benchmark/benchmark_50pages.pdf \
    --economy Singapore --pillar 7
```

Output: `logs/cost_report.json` — includes per-component costs for OCR, embedding, LLM, and crawling. Judges verify this file against the code.

---

## PDPA-first gate

Singapore PDPA (Pillar 7) **must pass end-to-end before testing any other economy**. This is enforced architecturally: `main.py` runs `check_pdpa_gate()` after Singapore Pillar 7 extraction. Gate requires at least one P7 provision with confidence ≥ 0.80.

---

## Running tests

```bash
pytest                              # all tests
pytest tests/test_economy_config.py # specific module
pytest -v --tb=short                # verbose output
```

---

## Adding a new economy

Only a YAML file is required — zero Python code changes:

1. Create `economies/{code}.yaml` with these fields:
   ```yaml
   economy_name: Vietnam
   iso_code: VN
   un_name: Viet Nam
   script_type: latin       # or "asian" for PaddleOCR
   languages: [vi, en]
   portals:
     - name: Ministry of Justice
       url: https://vbpl.vn
       type: primary
   ```
2. Run: `python main.py --economy Vietnam --pillar 7`

The pipeline will auto-derive the OCR engine from `script_type`, pick up the UN name for CSV output, and include the economy in `_get_economy_names()` lookups. The `--pillar` argument accepts any integer — no code change needed for new pillars either.

---

## Pinned versions

No `latest` tags are used anywhere in this project. All versions are pinned:

| Component | Pinned version |
|-----------|---------------|
| LLM (tier 1) | `claude-sonnet-4-20250514` |
| LLM (tier 2) | `gpt-4o` |
| LLM (tier 3) | `deepseek-chat` (V3) |
| LLM (tier 4) | `qwen/qwen3-32b` via Groq |
| LLM (tier 5) | `qwen-plus` via DashScope |
| LLM (tier 6, offline) | `qwen2.5:7b` (Ollama, Apache 2.0) |
| LLM (tier 7, offline) | `granite3-dense:8b` (Ollama, Apache 2.0) |
| OCR (Latin scripts) | Tesseract 5.x |
| OCR (Asian scripts) | PaddleOCR 2.x |

Key library versions are pinned in `requirements.txt`. Run `pip install -r requirements.txt` to reproduce the exact environment.

---

## Open-source fallback (if commercial API)

To run the engine fully offline without any API keys:

```bash
# 1. Pull the offline models
ollama pull qwen2.5:7b
ollama pull granite3-dense:8b

# 2. Pin the engine to Ollama
export LLM_PROVIDER=ollama

# 3. Run as normal — no ANTHROPIC_API_KEY or OPENAI_API_KEY required
python main.py --economy Singapore --pillar 7
```

The Ollama cascade uses `qwen2.5:7b` (tier 4) first, then `granite3-dense:8b` (tier 5) on failure. Both models are Apache 2.0 licensed. OCR (Tesseract / PaddleOCR) and embeddings are always local and require no API key.

---

## Build order (ClickUp story sequence)

All stories completed: `[Z1-1]` → `[Z1-5]` → `[Z2-1]` → `[Z2-6]` → `[Z2-86ey0q56f]` (audit/integration fixes).

---

## License

Apache License 2.0 — see `LICENSE`.
All offline models in the cascade (`qwen2.5:7b`, `granite3-8b`) are Apache 2.0 licensed.
