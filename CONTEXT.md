# CONTEXT.md

Domain glossary and implementation state for the RDTII Extraction Engine.
Updated as stories are completed — future agents should read this before touching any module.

---

## Domain Glossary

| Term | Definition |
|------|-----------|
| **Economy** | A single Asia-Pacific jurisdiction (e.g. Singapore, Thailand). Described by one YAML file in `economies/`. |
| **EconomyConfig** | The Pydantic model that is the single source of truth for everything a downstream module needs to know about an economy: script type, OCR engine, portals, and optional overrides. |
| **Portal** | A government web portal listed in an economy's YAML. Unlimited per economy. No pillar-tagging field — relevance is determined at runtime by the auto-probe. |
| **script_type** | Either `"latin"` or `"asian"`. Determines the default OCR engine automatically. |
| **ocr_engine** | A computed property of `EconomyConfig` derived from `script_type` (`latin → tesseract`, `asian → paddleocr`). Never set directly in YAML; use `ocr_engine_override` only for Stage-2 engines. |
| **ocr_engine_override** | Optional YAML field to force a specific OCR engine (`tesseract`, `paddleocr`, `azure`, `mistral_ocr`). Overrides the derived default. |
| **be_year_conversion** | Boolean flag (default `false`) that signals Buddhist Era → Gregorian year conversion is needed (Thailand, Cambodia, etc.). |
| **UnknownEconomyError** | Raised by `load_economy()` when `economies/{name}.yaml` does not exist. |
| **InvalidEconomyConfigError** | Raised by `load_economy()` when the YAML exists but fails Pydantic schema validation. Wraps `ValidationError` with the economy name. |
| **load_economy(name)** | Thin I/O loader: reads `economies/{name.lower()}.yaml`, calls `EconomyConfig.model_validate()`, raises named domain exceptions. Case-insensitive on the name argument. |
| **KNOWN** | Discovery tag for an act found in the Round 1 ground-truth database (set by the ranker, Z1-5). |
| **NEW** | Discovery tag for an act independently discovered by the engine beyond the Round 1 database (worth 20/40 accuracy points). |
| **Zone 1** | Evidence Discovery pipeline: config → probe → crawl → currency check → tag/rank. |
| **Zone 2** | Intelligent Mapping pipeline: fetch/route → OCR → segment/translate → chunk → embed → RAG → map → validate → write. |
| **Pillar** | One of the RDTII regulatory dimensions being assessed (e.g. Pillar 6, Pillar 7 / PDPA). |
| **PDPA** | Singapore Personal Data Protection Act — the primary target for the Phase 1 gate (Pillar 7). |
| **5-tier LLM cascade** | Provider order: Anthropic → OpenAI → Groq → Ollama (qwen2.5:7b) → Ollama (granite3-8b). Pinned per run via `LLM_PROVIDER` env var; fallback only on runtime failure. Llama 3.3 excluded (non-Apache 2.0). |
| **CER** | Character Error Rate — OCR quality metric. Stage-2 OCR triggers automatically when CER ≥ 5%. |
| **RAG pipeline** | Hybrid BM25 + dense retrieval with cross-encoder reranking; returns top-5 chunks per indicator query, each with a `location_reference`. |
| **location_reference** | `(act_name, part, article_number)` tuple carried by every chunk so citations in the output are verifiable. |

---

## Architectural Decisions

### ADR-001 — `ocr_engine` is derived, not configured
`ocr_engine` is a computed `@property` on `EconomyConfig` derived from `script_type`. It is never a YAML field. The mapping `latin → tesseract`, `asian → paddleocr` lives in one place (`_SCRIPT_TO_OCR` dict in `economy_config.py`). Overrides use the separate `ocr_engine_override` field.
**Reason:** eliminates silent mismatch between `script_type` and `ocr_engine`; single source of truth.

### ADR-002 — I/O separated from validation at the `load_economy` seam
`EconomyConfig.model_validate(data)` is pure (no filesystem). `load_economy(name)` is the thin I/O shell. Tests for schema validation pass inline dicts directly to `model_validate` — no YAML files, no filesystem mocking needed.

### ADR-003 — `extra='forbid'` on all config models
Both `Portal` and `EconomyConfig` use `ConfigDict(extra="forbid")`. Unknown YAML keys raise `InvalidEconomyConfigError` immediately. Prevents silent field drops (e.g. a future economy YAML using an unrecognised field).

### ADR-004 — Economy YAML filenames use the full lowercase economy name
`load_economy("Singapore")` resolves to `economies/singapore.yaml`. Short codes (`sg.yaml`, `th.yaml`) are not used — they cannot be resolved by the loader and are not present in the repo.

### ADR-005 — Economies run sequentially, never in parallel
`batch_run.py` is a thin sequential wrapper around `main.py`. No parallel orchestration. Reason: cost telemetry per economy must be clean and the hackathon rubric verifies actual costs.

---

## ADR-006 — Portal `type` field is optional with default "primary"
`type: Literal["primary", "secondary"] = "primary"` on the `Portal` model. Existing YAML files without `type` remain valid; `type` is used only for tie-breaking in probe ranking. An optional `search_url_pattern` field allows per-portal search URL templates (e.g. `https://sso.agc.gov.sg/Search?SearchAct={keyword}`).

## ADR-007 — Translation trigger inferred from `translation_provider`
`translation_provider is not None` means Layer 1 keyword translation is active for that economy. No separate boolean flag added. Primary non-English language = first entry in `languages` that is not `"en"`.

## ADR-008 — JS portal detection uses httpx-first probe with Playwright fallback
The probe does not hardcode which portals need Playwright. It sends httpx first; if the response body is JS-rendered (< 200 chars body text, or SPA root markers), it automatically retries with Crawl4AI/Playwright. This keeps the logic dynamic and configurable via YAML `search_url_pattern`.

## ADR-009 — taxonomy.json lives at project root
`taxonomy.json` holds all 10 indicators (P6-I1 to P7-I5) with `probe_keywords` arrays. Validated at startup by `validate_taxonomy()`. `load_taxonomy()` in `probe.py` is the single loader.

---

## Implementation State

### ✅ Completed: [Z1-1] Economy YAML Adapter Schema

**Files changed:**
- `src/config/economy_config.py` — full implementation
- `economies/singapore.yaml` — reference Latin-script config
- `economies/thailand.yaml` — reference Asian-script config (BE year conversion)
- `tests/test_economy_config.py` — 21 tests, all passing
- `docs/economy_schema.md` — full schema reference + "add a new economy" walkthrough

**What was built:**
- `Portal` Pydantic model (`name`, `url`, `extra='forbid'`)
- `EconomyConfig` Pydantic model with all fields (`economy_name`, `script_type`, `languages`, `portals`, `ocr_engine_override`, `be_year_conversion`, `llm_override`, `translation_provider`), merged single-pass `languages_valid` validator, and `ocr_engine` computed property
- `UnknownEconomyError` and `InvalidEconomyConfigError` named domain exceptions
- `load_economy(name: str) -> EconomyConfig` — thin I/O shell, case-insensitive, raises named exceptions only

**Test coverage:** happy path (model_validate + filesystem), derived OCR engine, override, unlimited portals, case-insensitive loading, 7 failure modes, named exception types.

---

### ✅ Completed: [Z1-2] Auto-Probe Portal Discovery

**Files changed:**
- `taxonomy.json` — 10 indicators (P6-I1 to P7-I5) with `probe_keywords` arrays (3–6 terms each)
- `src/crawler/probe.py` — full implementation: `run_probe()`, `validate_taxonomy()`, `load_taxonomy()`, `translate_keywords()`, `ProbeRawResult`, `ProbeResult`
- `src/crawler/exceptions.py` — `ProbeError`, `ConfigError`
- `src/config/economy_config.py` — `Portal` extended: `type: Literal["primary","secondary"] = "primary"`, `search_url_pattern: str | None = None`
- `economies/singapore.yaml` — added `type` + `search_url_pattern` to portals
- `economies/thailand.yaml` — added `type` to portals; `translation_provider: deepl`
- `economies/malaysia.yaml` — new Round 1 economy (Latin/Bahasa, DeepL translation)
- `main.py` — startup `load_taxonomy()` + `validate_taxonomy()` call
- `requirements.txt` — added `httpx`, `googletrans`, `pytest-asyncio`, `pytest-mock`, `respx`
- `.env.example` — added `PROBE_JITTER_MS`, `PROBE_TIMEOUT_SEC`
- `pytest.ini` — `asyncio_mode = auto`
- `tests/conftest.py` — shared fixtures: `sg_economy`, `malaysia_economy`, `full_taxonomy`, `clear_translation_memory` (autouse)
- `tests/test_probe.py` — 34 tests, all passing
- `tests/test_economy_config.py` — 5 new Portal type/pattern tests (26 total, all passing)
- `tests/fixtures/search_result_page.html` — mock search result page for tests
- `cache/.gitkeep` — cache directory for keyword translation persistence

**What was built:**
- `ProbeRawResult` dataclass (portal_url, keyword, hit_count, status, result_urls)
- `ProbeResult` dataclass (url, portal_name, portal_type, total_hit_count, is_active, language, probe_status)
- httpx-first probe with automatic Playwright fallback on JS-rendered responses (`_is_js_rendered`)
- Result URL deduplication across keyword queries per portal
- Ranking: total_hit_count descending, primary portal wins ties
- `probe_status`: "ok" / "partial" / "failed" based on keyword probe success rate
- Zero-result filtering: portals with `is_active=False` never reach crawler.py
- `ProbeError` raised if all portals are inactive
- Structured logging: `probe_skip_{economy}_{ts}.jsonl` + `probe_summary_{economy}_{ts}.json`
- Layer 1 translation: DeepL primary → Google Translate fallback; in-session + disk cache
- `ConfigError` raised at startup if any indicator missing `probe_keywords`

**Test coverage:** taxonomy validation, JS detection, search URL building, HTML parsing, httpx routing, Playwright fallback, HTTP 403/429/timeout handling, hit aggregation, deduplication, ranking, zero-result filtering, ProbeError, log files, translation skip/translate/fallback/cache, output contract.

### 🔲 Pending: [Z1-3] Crawl4AI Document Retrieval
`src/crawler/crawl4ai_runner.py` — `crawl(portal_url, depth=2) -> list[CandidateDocument]`
Domain-locked, Playwright-rendered, depth-2 crawl.

### 🔲 Pending: [Z1-4] Currency Check + Wayback Archiving
`src/crawler/currency.py` — verify in-force status, auto-fetch successor, archive to Wayback Machine.

### 🔲 Pending: [Z1-5] KNOWN/NEW Tagging + Ranking
`src/crawler/ranker.py` — two-pass discovery tagging; top 3–5 ranked acts handed to Zone 2.

### 🔲 Pending: [Z2-1] Fetch + Route + OCR
`src/fetcher/router.py`, `src/ocr/processor.py` — route by MIME type; two-stage OCR cascade (CER ≥ 5% → Stage 2).

### 🔲 Pending: [Z2-2] Segment + Translate
`src/fetcher/segmenter.py` — split multi-act volumes; DeepL + Google Translate fallback; persist verbatim original + translation.

### 🔲 Pending: [Z2-3] RAG Pipeline
`src/retrieval/chunker.py`, `embedder.py`, `rag.py` — article-level chunking, hybrid BM25 + dense retrieval, cross-encoder rerank, top-5 chunks with location_reference.

### 🔲 Pending: [Z2-4] LLM Mapping
`src/llm/client.py`, `src/mapping/mapper.py` — 5-tier cascade client; per-indicator mapping to `{indicator_id, article, verbatim_snippet, mapping_rationale, confidence}`.

### 🔲 Pending: [Z2-5] Validation + Confidence Flagging
`src/output/validator.py` — HTTP GET each source_url; Wayback snapshot; confidence < 0.80 → auto-note.

### 🔲 Pending: [Z2-6] Output Writer + Cost Logger
`src/output/writer.py`, `tools/cost_logger.py` — 13-column CSV (exact OUTPUT_TEMPLATE_31MAY.xlsx schema) + JSON envelope; actual per-component cost report.
