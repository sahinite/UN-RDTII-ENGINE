# CONTEXT.md

Domain glossary, architectural decisions, and implementation state for the RDTII Extraction Engine.
Updated as stories are completed — future agents should read this before touching any module.

---

## Domain Glossary

| Term | Definition |
|------|-----------|
| **Economy** | A single Asia-Pacific jurisdiction (e.g. Singapore, Thailand). Described by one YAML file in `economies/`. |
| **EconomyConfig** | Pydantic model — single source of truth for an economy: `economy_name`, `iso_code`, `un_name`, `script_type`, `languages`, `portals`, and optional overrides. `extra='forbid'` on all config models. |
| **Portal** | A government web portal in an economy's YAML. Each portal declares strategy fields: `anti_bot`, `discovery`, `fetch`, `index_urls`, `pdf_view_suffix`, `transport_fallback`. |
| **script_type** | `"latin"` or `"asian"`. Determines default OCR engine via computed `@property` (`latin → tesseract`, `asian → paddleocr`). |
| **ocr_engine_override** | Optional YAML field to force a specific OCR engine. Overrides the derived default. |
| **be_year_conversion** | Boolean flag for Buddhist Era → Gregorian year conversion (Thailand, Cambodia). |
| **KNOWN** | Discovery tag for an act/provision found in Round 1 ground-truth database. |
| **NEW** | Discovery tag for an act independently discovered by the engine (worth 20/40 accuracy points). |
| **Zone 1** | Evidence Discovery: `discover()` in `src/crawler/discover.py` — strategy-driven per-portal discovery. |
| **Zone 2** | Intelligent Mapping: fetch/route → OCR → translate → chunk → embed → RAG → map → validate → write. |
| **Pillar** | One of the RDTII regulatory dimensions (e.g. P6 Cross-border Data, P7 Domestic Data Protection). |
| **PDPA** | Singapore Personal Data Protection Act — primary target for Phase 1 gate (Pillar 7). |
| **5-tier LLM cascade** | Anthropic → OpenAI → Groq → Ollama (qwen2.5:7b) → Ollama (granite3-8b). Pinned per run via `LLM_PROVIDER`. Llama 3.3 excluded (non-Apache 2.0). |
| **CER** | Character Error Rate — OCR quality metric. Stage-2 OCR triggers at CER ≥ 5%. |
| **RAG pipeline** | Hybrid BM25 + dense retrieval with cross-encoder reranking; top-5 chunks per indicator, each with `location_reference`. |
| **location_reference** | `(act_name, part, article_number)` tuple for verifiable citations. |
| **SeedData** | `known_urls`, `known_titles`, `known_provisions` — loaded from Round 1 DB xlsx + Sample CSV. Compound titles split on `;`/newline. |

---

## Architectural Decisions

### ADR-001 — `ocr_engine` is derived, not configured
Computed `@property` on `EconomyConfig` from `script_type`. `_SCRIPT_TO_OCR` dict is single source of truth. Overrides use `ocr_engine_override`.

### ADR-002 — I/O separated from validation at `load_economy` seam
`EconomyConfig.model_validate(data)` is pure. `load_economy(name)` is thin I/O shell with fuzzy name matching (`difflib.get_close_matches`).

### ADR-003 — `extra='forbid'` on all config models
Unknown YAML keys raise `InvalidEconomyConfigError` immediately.

### ADR-004 — Economy YAML filenames use full lowercase name
`load_economy("Singapore")` → `economies/singapore.yaml`. Case-insensitive, `.strip().title()` normalised.

### ADR-005 — Economies run sequentially, never in parallel
`batch_run.py` calls `main.run_pipeline()` per economy/pillar. Cost telemetry must be clean per economy.

### ADR-009 — taxonomy.json at project root
All indicators with `probe_keywords`, `exclude_act_titles`, `exclude_keywords`, `in_scope`, `out_of_scope`, `negative_examples`, `rdtii_ref`, `category`, `scoring`. Validated at startup.

### ADR-014 — `FetchedDocument` is the single Zone 2 contract
All extractors return `FetchedDocument`. Validation enforced at end of every extractor.

### ADR-017 — `verbatim_original` always source-language `raw_text`
Unmodified even after BE year conversion. BE conversion applied only to text sent to translation provider.

### ADR-021 — Mapping module lives in `src/mapping/`, not `src/llm/`
`src/llm/client.py` is a thin re-export of `src/mapping/llm_client.py`.

### ADR-024 — Stage 2 OCR providers are credentials-gated
`AZURE_DI_KEY` / `MISTRAL_API_KEY` env vars. Missing → silently skipped.

### ADR-026 — CSV uses UTF-8-BOM
Excel-compatible. Post-write verification checks BOM bytes.

### ADR-029 — Output filename includes timestamp
`{Economy}_P{pillar}_{YYYY-MM-DDTHHMMSS}.csv/.json` — uniqueness across runs.

### ADR-032 — `extract_provisions` only catches `AllProvidersExhaustedError`
`ProviderRateLimitError` is internal cascade signal. Mapper sees boundary contract only.

### ADR-034 — `ALLOW_UNVERIFIED_SNIPPETS` env var
Default `false`. Verbatim assertion failure → hard discard. Set `true` only for scanned-PDF OCR edge cases.

### ADR-035 — Provision-level discovery tag uses URL anchor heuristic
`infer_article_anchor("Section 26")` → `"#pr26-"` (SSO scheme). Unresolvable → doc-level fallback + `flag_for_review`.

### ADR-036 — evaluate.py NEW score is provision-level
Rows with `discovery_tag=="NEW"` whose `(law_name, article)` not in kit. Score: `min(count * 4, 20)`.

### ADR-037 — Zone 1 is a single `discover()` step
Replaces old probe→crawl→currency→rank pipeline. Strategy-driven (YAML), budget-bounded, pillar-scoped. Legacy steps remain importable but unused by `main.py`.

### ADR-038 — Transport ladder stops at first real 200
Rung 1 (plain httpx) skipped for `anti_bot != "none"`. Rung 3 (Playwright) only when `transport_fallback == "playwright_stealth"`.

### ADR-039 — KNOWN seeds never truncated by `ZONE2_MAX_ACTS`
KNOWN always kept. NEW fills remaining slots up to cap.

### ADR-040 — `pdf_endpoint` rewrites URL before download
`_find_portal_for_url()` matches by registered domain. Rewrites in `route()` before `download()`.

---

## Implementation State

All stories Z1-1 through Z2-6 are complete. Below is the current module-level summary.

### Zone 1 — Evidence Discovery

| Module | Purpose |
|--------|---------|
| `src/config/economy_config.py` | `EconomyConfig` + `Portal` Pydantic models; `load_economy(name)` with fuzzy matching; `load_economy_by_iso()`; `iso_code`/`un_name` fields |
| `src/crawler/discover.py` | **Active Zone 1 entry point.** `discover()` routes per-portal strategy (`index`/`seed_only`/`TBD`). `build_pillar_keywords()` filters by `P{pillar}-`. `build_pillar_excludes()` gathers exclude lists. `_discover_index()` fetches browse indexes, BM25-ranks, applies exclusion BEFORE KNOWN check, merges seeds. |
| `src/crawler/transport.py` | Transport ladder: plain httpx → httpx+browser headers → Playwright stealth. `_is_real_response()` detects 403/JS-shell. |
| `src/crawler/seed_loader.py` | `SeedData` with `known_urls`, `known_titles`, `known_provisions`. Splits compound titles on `;`/newline. Generic `_pillar_matches()` for any Pn. |
| `src/crawler/crawl4ai_runner.py` | Stealth Crawl4AI/Playwright. Shared browser singleton. Two-attempt fetch (with/without selector). |
| `src/crawler/probe.py` | Legacy probe — `load_taxonomy()` / `validate_taxonomy()` still used at startup. |
| `src/crawler/crawler.py` | Legacy BFS crawler — shared `_normalise_url()` imported by other modules. |
| `src/crawler/currency.py` | Legacy currency check. |
| `src/crawler/ranker.py` | Legacy ranker with LLM gate. |

### Zone 2 — Intelligent Mapping

| Module | Purpose |
|--------|---------|
| `src/fetcher/router.py` | Zone 2 entry: `route()` dispatches TEXT_PDF/SCANNED_PDF/HTML. `pdf_endpoint` URL rewrite. `single_act_fetch` skips volume detection. |
| `src/fetcher/extractors/pdf_text.py` | pdfplumber extraction + `legislation_meta.py` (law_number_ref/last_amended). Section hierarchy parser. |
| `src/fetcher/extractors/ocr_stage1.py` | Tesseract/PaddleOCR with CER gate. Raises `OCRQualityError` at ≥5% for Stage 2. |
| `src/fetcher/extractors/html_extractor.py` | BeautifulSoup + `location_reference_map` from URL anchors. |
| `src/fetcher/extractors/llm_ocr.py` | LLM vision OCR fallback. |
| `src/fetcher/extractors/legislation_meta.py` | Parses "Act N of YYYY", revised-edition year, `?DocDate=` from URL. |
| `src/fetcher/segmenter.py` | Consolidated volume splitter (PyMuPDF). Short-segment merge. |
| `src/fetcher/translator.py` | 3-layer translation (keyword/title/document). DeepL primary → Google fallback. BE year conversion. |
| `src/fetcher/models.py` | `Zone1Result`, `FetchedDocument`, `TranslatedDocument` (8 proxy accessors), `ArticleReference`, `CostLogEntry`. |
| `src/ocr/processor.py` | Stage 2 OCR: Azure DI → Mistral OCR. CER fix for whitespace-only pages. `stage2_failed` flag. |
| `src/retrieval/` | `chunker.py` (3-strategy article splitter), `embedder.py` (all-MiniLM-L6-v2 + FAISS), `bm25_index.py` (keyword boosting), `fusion.py` (RRF k=60), `reranker.py` (cross-encoder top-20→top-5), `rag.py` (orchestrator), `config.py` (`get_valid_indicator_ids()`). |
| `src/mapping/mapper.py` | `extract_provisions()` orchestrator. `check_pdpa_gate()` → `PDPAGateError`. Lazy `_get_economy_names()` from YAMLs. |
| `src/mapping/llm_client.py` | `PROVIDER_CASCADE`, `pin_active_provider()`, `call_llm_with_cascade()`, `get_active_model_version(ocr_engine=)`. |
| `src/mapping/parser.py` | `parse_llm_response()` with verbatim assertion (hard discard), provision-level `resolve_provision_tag()`, law-name abbreviation check, cross-reference auto-flagging. |
| `src/mapping/prompts.py` | `SYSTEM_PROMPT` with Rules 1–9 (incl. law name expansion, rationale format, leave-blank-if-uncertain). |
| `src/mapping/provision_tag.py` | `resolve_provision_tag()` (KNOWN/NEW per provision), `infer_article_anchor()` heuristic. |
| `src/mapping/providers/` | `AnthropicProvider`, `OpenAIProvider`, `GroqProvider` (qwen3-32b + fallback), `OllamaProvider` (qwen2.5:7b, granite3-8b). |
| `src/output/writer.py` | `write_csv()` (13-col UTF-8-BOM), `write_json()` (document-level + `provisions[]` envelope per UN slide 18). `pdf_is_scanned`, `retrieval_method`, per-provision `discovery_tag`. `validate_record()` with portal domain allowlist. |
| `src/output/validator.py` | URL validation + Wayback/local archiving (deduped per URL). Confidence flagging (<0.80 → review note). |
| `src/output/cost_logger.py` | `CostLogger` — per-component accumulation → `logs/cost_report.json`. |
| `src/output/models.py` | `OutputRecord` (13 CSV cols + JSON extended fields). `processing_time: int`. `as_provision_dict()`. |
| `src/cli/progress.py` | Single-line ANSI spinner. Global singleton `set_progress(p)` / `substep(text)`. Substeps wired in OCR, RAG, mapper, crawler. |

### Pipeline Integration

| Module | Purpose |
|--------|---------|
| `main.py` | `run_pipeline()` — full end-to-end. `_run_zone1()` calls `discover()`. PDPA gate for SG P7. |
| `batch_run.py` | Sequential multi-economy wrapper calling `run_pipeline()`. |
| `evaluate.py` | Accuracy scoring: KNOWN match rate (40pts) + provision-level NEW (4pts each, max 20pts). |
| `tools/cost_logger.py` | Standalone CLI cost benchmarking. |

### Economy Configs (11 files in `economies/`)

All declare `iso_code` and `un_name`. Adding a new economy requires only creating a YAML file — no Python changes.

| Economy | ISO | Portal Strategy | Status |
|---------|-----|----------------|--------|
| Singapore | SG | SSO: `index` + `pdf_endpoint` + `header_spoof`; Gazette: `TBD` | Reference / Phase 1 gate |
| Australia | AU | legislation.gov.au + OAIC | Minimal config |
| Malaysia | MY | — | Minimal config |
| Thailand | TH | — | Minimal config (BE conversion) |
| Vietnam | VN | — | ISO/UN name only |
| Philippines | PH | — | ISO/UN name only |
| Cambodia | KH | — | ISO/UN name only |
| Myanmar | MM | — | ISO/UN name only |
| Laos | LA | — | ISO/UN name only |
| Brunei | BN | — | ISO/UN name only |
| Indonesia | ID | — | ISO/UN name only |

### Key Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_PROVIDER` | (cascade) | Pin LLM provider for run |
| `LLM_MODEL` | (provider default) | Override model within provider |
| `ZONE2_MAX_ACTS` | 5 | Max acts sent to Zone 2 |
| `DISCOVER_BUDGET_S` | 120.0 | Wall-clock budget for discover() |
| `NEW_SCORE_THRESHOLD` | 0.2 | Min BM25 score for NEW acts |
| `ALLOW_UNVERIFIED_SNIPPETS` | false | Skip verbatim assertion (OCR edge cases) |
| `WAYBACK_BEST_EFFORT` | true | Wayback archiving is non-blocking |
| `LOCAL_ARCHIVE_FALLBACK` | true | Save local PDF snapshot if Wayback fails |
| `WAYBACK_RETRY_WAIT_S` | 2 | Wayback 429 retry wait |

### Taxonomy (taxonomy.json)

Rebuilt against `docs/RDTII_methodology_and_scoring_criteria.csv`:
- **P6 (Cross-border Data Policies):** 6.1 ban/local-processing, 6.2 local storage, 6.3 infrastructure, 6.4 conditional flow regimes, 6.5 binding data-transfer agreements.
- **P7 (Domestic Data Protection & Privacy):** 7.1 comprehensive framework, 7.2 dedicated cybersecurity framework, 7.3 minimum data retention, 7.4 DPIA/DPO, 7.5 government access to personal data.

Each indicator has `rdtii_ref`, `category`, `scoring` (0/0.5/1 bands), `probe_keywords`, `exclude_act_titles`, `exclude_keywords`, `in_scope`, `out_of_scope`, `negative_examples`.

### Output Format

- **CSV:** 13 columns in `OUTPUT_TEMPLATE_31MAY.xlsx` order, UTF-8-BOM.
- **JSON:** Document-level envelope (`economy`, `law_name`, `source_url`, `source_pdf_path`, `ocr_quality_cer`, `processing_time`, `model_version`, `discovery_tag`, `pdf_is_scanned`, `retrieval_method`) + `provisions[]` array (per UN slide 18). Each provision includes `discovery_tag`.
- **`mapping_rationale`:** Rule 8 format. Rule 9: leave blank if uncertain. 300-char cap.
- **`processing_time`:** integer seconds (slide 18 naming).

### Test Suite

~604 tests passing, 2 skipped, 4 pre-existing unrelated failures (2 `test_probe`, 2 `test_z2_4_providers` anthropic credentials).

### Critical Discovery Fix (latest)

Compound seed titles (e.g. "PDPA; Guide; Advisory...") split on `;`/newline in `seed_loader.py`. Exclude filter in `discover.py` runs BEFORE KNOWN check — Round 1 negative-example acts (banking/tax/companies) no longer bypass exclusion as KNOWN.
