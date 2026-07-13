# RDTII Extraction Engine

**UN Global Hackathon on AI for Digital Trade Regulatory Analysis**
Team: Galaxefi | Round: 1 | Submission deadline: 20 July 2026 | Demo: 3 August 2026

An end-to-end AI pipeline that crawls government legal portals, extracts regulatory text via OCR/NLP, and maps provisions to RDTII indicators (Pillars 6 & 7) using a 7-tier LLM cascade with hybrid RAG retrieval.

---

## Prerequisites

Before running Quick Start, install system dependencies:

```bash
python setup.py
```

This script will:
- Install **Tesseract** (required — default OCR engine; language packs like Malay `msa` auto-install per economy at run start)
- Optionally pre-download the **sentence-transformers** embedding models (English `all-MiniLM-L6-v2` + multilingual `paraphrase-multilingual-MiniLM-L12-v2`, ~90–120MB each — see [retrieval strategy](#retrieval-strategy))
- Optionally install **Ollama** with offline models (~9GB) — only needed if you have no cloud API key

> **Translation** uses **Argos Translate** (offline neural MT) as the primary provider, with DeepL → Google as fallbacks. Argos models auto-install per economy at run start (no manual step, no API key). Non-English economies retrieve over the original text and skip full-document translation; the LLM reads the retrieved source-language passages directly.

> **If you have any one of `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GROQ_API_KEY`, you can skip Ollama.**

### Manual install (if you prefer not to use setup.py)

| Dependency | Required | Install |
|---|---|---|
| Tesseract 5.3+ | **Yes** | macOS: `brew install tesseract` / Ubuntu: `sudo apt-get install -y tesseract-ocr` / Windows: [installer](https://github.com/UB-Mannheim/tesseract/wiki) |
| Ollama + models | No (offline only) | macOS: `brew install ollama` / Ubuntu: `curl -fsSL https://ollama.com/install.sh \| sh` |
| sentence-transformers models | No (auto-download on first run) | `python -c "from sentence_transformers import SentenceTransformer as S; S('all-MiniLM-L6-v2'); S('paraphrase-multilingual-MiniLM-L12-v2')"` |
| Argos Translate | Yes for non-English economies (auto-installs models per run) | `pip install argostranslate` (in requirements.txt) |

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

# Batch run — each economy runs concurrently in its own isolated subprocess (auto)
RUN_PROFILE=submit python batch_run.py --economies Singapore Australia Malaysia --pillar 6 7
#   --max-parallel N   cap concurrent economy lanes (avoid LLM rate limits with many economies)
#   --max-parallel 1   fully sequential (in-process)
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
economies/       per-economy YAML configs (3 files)          [Z1-1]
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

Three economy YAML files exist and are configured. Singapore is the HTML-index
reference, while Australia is the API-driven reference.

| Economy | YAML | Primary portal | Discovery | Script | Languages | Status |
|---------|------|----------------|-----------|--------|-----------|--------|
| Singapore | `singapore.yaml` | sso.agc.gov.sg | index | latin | en | Reference (PDPA-first gate) |
| Australia | `australia.yaml` | legislation.gov.au | api | latin | en | Live |
| Malaysia | `malaysia.yaml` | agc.gov.my | — | latin | ms, en | Configured |
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
| 7 | Ollama | `granite3-8b` | Offline, Apache 2.0 |

**Note:** Llama 3.3 is explicitly excluded (non-Apache 2.0 license).
Pin any provider with `LLM_PROVIDER`: `anthropic | openai | deepseek | groq | qwen | ollama`.

The LLM cascade implementation lives in `src/mapping/llm_client.py`.
`src/llm/client.py` re-exports the same public API for backwards compatibility.

### Swapping the LLM

Set `LLM_PROVIDER` in `.env`:
```bash
LLM_PROVIDER=anthropic   # use Anthropic as primary (recommended)
LLM_PROVIDER=openai      # use OpenAI gpt-4o as primary
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

## Retrieval strategy

Retrieval models and translation are selected **per economy** from `economy_config.languages`
(see CONTEXT.md ADR-060/061):

| | English economies (SG, AU) | Non-English economies (MY) |
|---|---|---|
| Embedder | `all-MiniLM-L6-v2` | `paraphrase-multilingual-MiniLM-L12-v2` |
| Reranker | `ms-marco-MiniLM-L-6-v2` | `mmarco-mMiniLMv2-L12-H384-v1` |
| Translation | none (already English) | RAG on **original** text; only retrieved passages reach the LLM |
| Full-doc translation | n/a | **skipped** (`translate_body=False`) |

- **Why per-economy, not global multilingual?** A global multilingual embedder *regressed the Singapore build gate* (P7: 18→10 records). English economies keep the English-specialised model; the multilingual one is only used where it's needed. Default is the English model, so the gate is safe.
- **Why translate-less?** Non-English documents (e.g. Malaysia's ~1.5M-char acts) used to be translated in full just to feed retrieval. Retrieval now runs on the original text and only the small set of retrieved passages is sent to the LLM, cutting **Malaysia P7 from ~50 min to ~7 min** (translation → ~1% of runtime).
- **Run profiles** (`RUN_PROFILE`): `gate` (KNOWN-only, build gate, default) · `submit` (KNOWN + 3 NEW — use this for submissions) · `explore` (aggressive NEW). The active profile is printed at run start.

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
| LLM (tier 7, offline) | `granite3-8b` (Ollama, Apache 2.0) |
| OCR (Latin scripts) | Tesseract 5.x |
| OCR (Asian scripts) | PaddleOCR 2.x |

Python package dependencies are declared in `requirements.txt`; model names are fixed in code/config so runs do not depend on provider `latest` aliases.

---

## Open-source fallback (without commercial API keys)

To run the engine fully offline without any API keys:

```bash
# 1. Pull the offline models
ollama pull qwen2.5:7b
ollama pull granite3-8b

# 2. Pin the engine to Ollama
export LLM_PROVIDER=ollama

# 3. Run as normal — no ANTHROPIC_API_KEY or OPENAI_API_KEY required
python main.py --economy Singapore --pillar 7
```

The Ollama cascade uses `qwen2.5:7b` (tier 6) first, then `granite3-8b` (tier 7) on failure. Both models are Apache 2.0 licensed. OCR (Tesseract / PaddleOCR) and embeddings are always local and require no API key.


## Limitations

**Fetch & discovery**
- Anti-bot portals (e.g. Singapore SSO) require JS rendering that intermittently fails under rate-limiting; retry + backoff mitigates but fetch is not fully deterministic run-to-run.
- KNOWN seeds are capped at `ZONE2_MAX_KNOWN_ACTS` (default 12) per run — economies with more known acts lose the excess.
- Round 1 seeds given as an act *name* only (no URL) rely on the browse index surfacing them by title and may be missed.
- The set of incidental NEW/index acts surfaced varies between runs.

**Mapping & tagging**
- KNOWN vs NEW is decided by act-title + section identity; a garbled or empty LLM `law_name` falls back to NEW (fuzzy title matching helps, but is not perfect).
- Round 1 rows without a parseable section number aren't tracked by the recall audit.
- Secondary/guidance sources are flagged for review, not auto-verified against primary legislation.
- Weak Round 1 mappings the LLM reasonably declines can appear as recall "misses".

**LLM & runtime**
- One provider is pinned per run — a sustained outage/429 aborts extraction (no mid-run provider switch).
- LLM calls dominate runtime and cost; large consolidated acts increase both.

**Coverage**
- Only Pillars 6 & 7 and economies SG / AU / MY are validated/configured.
- OCR Stage-2 (Azure DI / Mistral) needs API keys; without them, poor-quality scans may degrade extraction.
- SSO whole-doc HTML yields a flat section hierarchy (raw-text chunking); `location_reference` carries no deep-link anchor (the URL is in the `source_url` column).

---

## License

Apache License 2.0 — see `LICENSE`.
All offline models in the cascade (`qwen2.5:7b`, `granite3-8b`) are Apache 2.0 licensed.
