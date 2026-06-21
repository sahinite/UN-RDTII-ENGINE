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

### ✅ Completed: [Z1-3] Crawl4AI Crawler Integration

**Files changed:**
- `src/crawler/crawl4ai_runner.py` — dedicated Crawl4AI/Playwright wrapper: `SSO_DOMAIN`, `SSO_WAIT_FOR`, `SSO_TIMEOUT_MS`, `is_js_portal()`, `fetch_with_playwright()`. Extracted from crawler.py per CLAUDE.md architecture.
- `src/crawler/crawler.py` — full implementation: `CandidateAct`, `run_crawler()`, `load_known_urls()`, BFS engine, logging. Now imports Crawl4AI layer from `crawl4ai_runner` instead of inline.
- `src/crawler/exceptions.py` — added `CrawlerError`
- `tests/test_crawler.py` — 28 tests, all passing
- `tests/fixtures/sso_listing_page.html` — mock SSO HTML with pagination
- `tests/fixtures/legislation_au_listing.html` — mock AU listing HTML
- `tests/fixtures/round1_db_sg.xlsx` — minimal Round 1 DB (3 SG acts + 1 MY act)
- `tests/fixtures/sg_economy.yaml` — SG economy fixture for tests
- `requirements.txt` — added `tldextract`, `openpyxl`
- `.env.example` — added `CRAWL_TIMEOUT_MS`, `CRAWL_MAX_DEPTH`, `CRAWL_MAX_PAGES`, `CRAWL_JITTER_MS`, `MAX_CONCURRENT_CRAWLS`

**What was built:**
- `CandidateAct` dataclass (9 fields: act_title, act_url, description_snippet, document_type, discovery_tag, portal_source, economy, pillar, pass_number)
- Two-pass discovery: Pass 1 seeds BFS from known Round 1 URLs (KNOWN tag), Pass 2 seeds from indicator keyword search URLs (KNOWN or NEW tag)
- BFS crawler with `asyncio.Semaphore` concurrency, manual depth tracking (max 2 tiers), domain-locking via tldextract
- JS portal detection: `_is_js_portal` → dispatches to `_fetch_with_playwright` (Crawl4AI); static → `_fetch_with_httpx`
- SSO-specific: `css:a[href*='/Act/']` wait selector, 20s timeout, pagination following (detects "Next" link)
- Anti-bot: randomised jitter (200–600ms), 3-UA rotation, 429 → exponential backoff (1/2/4s), 403 → immediate skip
- Document type: HEAD request (Content-Type) + `.pdf` extension check
- URL dedup: `seen_urls: set[str]` with `_normalise_url` (lowercase, strip trailing `/`, strip cosmetic query params)
- Quality filter: titles < 5 chars discarded; snippets truncated at 500 chars; skip extensions/off-domain silently
- `load_known_urls(xlsx_path, economy_name)` — reads Round 1 XLSX, row-level economy filter
- Structured logging: per-page JSONL + summary JSON + error log; INFO console output for both passes
- `CrawlerError` raised on empty result (prevents Zone 2 from running with no input)
- `_sleep` module alias for testable asyncio.sleep patching; `_jitter` patchable no-op in tests

**ADR-010 — `_sleep` alias for testable backoff**
`_sleep = asyncio.sleep` at module level. Tests patch `src.crawler.crawler._sleep` to record/suppress delays without affecting pytest-asyncio internals.

**ADR-011 — Crawl4AI layer extracted to `crawl4ai_runner.py`**
The Crawl4AI/Playwright fetch code (`fetch_with_playwright`, `is_js_portal`, SSO constants) lives exclusively in `src/crawler/crawl4ai_runner.py`. `crawler.py` imports them using aliased names (`fetch_with_playwright as _fetch_with_playwright` etc.) so existing tests that `monkeypatch.setattr(crawler_mod, "_fetch_with_playwright", ...)` continue to work — the alias is a name in `crawler_mod`'s `__dict__`, not a closure. This matches the CLAUDE.md architecture and allows swapping the Playwright backend without touching BFS logic.

**Test coverage:** domain locking, depth limit, 429 backoff, 403 skip, KNOWN/NEW tagging, deduplication, pass ordering, Playwright routing, SSO pagination, timeout grace, PDF/HTML detection, title filter, snippet cap, CrawlerError, field completeness, sort order, XLSX loader.

### ✅ Completed: [Z1-4] Currency Check + Wayback Archiving

**Files changed:**
- `src/crawler/currency.py` — full implementation: `CurrencyResult`, `run_currency_check()`, `_validate_url()`, `_detect_currency_status()`, `_handle_cancelled_act()`, `_extract_last_amended()`, `_archive_act_url()`
- `tests/test_currency.py` — 27 tests, all passing
- `tests/fixtures/sso_current_page.html` — mock SSO page with "Current" status tag
- `tests/fixtures/legislation_au_repealed.html` — mock AU page with "Repealed" legislation-status div
- `tests/fixtures/cancellation_notice.html` — mock cancellation notice with replacement URL
- `.env.example` — added `CURRENCY_FETCH_TIMEOUT_SEC`, `CURRENCY_RETRY_WAIT_SEC`, `WAYBACK_RATE_LIMIT_SEC`

**What was built:**
- `CurrencyResult` dataclass (15 fields = 9 from CandidateAct + 6 new: `http_status`, `currency_status`, `flag_for_review`, `currency_note`, `last_amended`, `archive_url`)
- **ST1 URL validation**: HTTP GET gate — 404 → broken; 301/302 same-domain → follow and update `act_url`; cross-domain redirect → SUSPICIOUS_REDIRECT (broken); 403 → retry alt UA; 429 → backoff retry; 5xx/timeout → uncertain + flag_for_review
- **ST2 in-force detection**: portal-specific status tags first (SSO `<span class="status-tag">`, legislation.gov.au `<div class="legislation-status">`), then keyword scan (`_CANCELLED_PATTERNS`, `_IN_FORCE_PATTERNS`), Bahasa "Akta ini telah dibatalkan" included; unknown → "uncertain"
- **ST3 auto-replacement (4 scenarios)**: S1=pass unchanged; S2=parse notice for URL/name → validate → use; S3=re-search portal → if still not found → flag_for_review=True, still passed downstream; S4=sectoral law detection by title keyword → currency_note annotation
- **ST4 last_amended extraction**: regex patterns for "as amended in YYYY", "consolidated as at YYYY", "[as at DD Mon YYYY]" etc.; most-recent-year wins; sanity range 1950–now; empty string on failure (not null)
- **ST5 Wayback archiving**: `https://web.archive.org/save/{url}`; Content-Location header → snapshot URL; 429 → 60s wait retry; 523/timeout → archive_url="", pipeline continues; sequential (1 req/sec, `_sleep` alias for testability); only live URLs archived (not 404s)
- `run_currency_check()` returns ALL acts (broken, cancelled, uncertain, in-force) — ranker.py filters; summary JSON written on completion
- `_sleep = asyncio.sleep` module alias for testable rate-limit patching

**ADR-011 — CurrencyResult is a standalone dataclass, not an extension of CandidateAct**
Avoids modifying the tested Z1-3 dataclass. All 9 CandidateAct fields are forwarded into CurrencyResult as flat fields. This keeps both dataclasses independent and fully testable without cross-module coupling.

### ✅ Completed: [Z1-5] Two-Pass Discovery Tagging (KNOWN/NEW) + Ranker

**Files changed:**
- `src/crawler/seed_loader.py` — NEW: `SeedData`, `load_seed_data()`, `normalise_title()`, Round 1 DB xlsx loader, Sample CSV loader
- `src/crawler/ranker.py` — full implementation replacing stub: `RankedAct`, `RankerError`, `ActIndicatorScore`, `run_ranker()`, all ST2–ST7 pipeline
- `taxonomy.json` — extended: added `exclude_keywords` and `exclude_act_titles` arrays for all 10 indicators
- `tests/test_ranker.py` — 40 tests (7 groups), all passing; replaces 2-line stub
- `tests/fixtures/sample_portals_p6.csv` — NEW: Sample CSV fixture with semicolon-separated reference URLs
- `tests/fixtures/taxonomy_ranker_test.json` — NEW: 2-indicator test taxonomy with exclusion rules

**What was built:**

**ST1 — Seed Data Loader (`crawler/seed_loader.py`)**
- `SeedData` dataclass: `known_urls: set[str]`, `known_titles: set[str]`, `economy`, `pillar`
- Loads Round 1 DB (xlsx): filters by economy + pillar, normalises URLs and titles
- Loads Sample CSV: splits semicolon-separated `References` URLs, merges into seed sets
- `normalise_title()`: strips trailing year suffix, lowercases, collapses whitespace
- Imports `_normalise_url` from `crawler.py` — zero URL normalisation duplication
- Logs `[SEED] {economy} {pillar}: N known URLs loaded…`; WARN (no crash) if empty

**ST2 — Discovery Tag Finalisation (`resolve_discovery_tag`)**
- URL match → KNOWN (exact, highest priority)
- Single title prefix match → KNOWN + note ("URL changed since Round 1")
- Ambiguous title match (≥2 known titles share prefix) → NEW + `flag_for_review=True`
- No match → NEW

**ST3 — Layer 2 Translation (`_apply_translation`)**
- Triggered by `economy_config.translation_provider is not None`
- DeepL primary (with `DEEPL_API_KEY`); googletrans fallback on DeepL failure
- In-memory + disk cache (`cache/l2_title_translations_{lang}.json`)
- `act_title_original` preserved; translated title used for scoring only
- English economies (SG, AU): zero API calls

**ST4 — Semantic + BM25 Scorer (`_score_acts`)**
- `SentenceTransformer("all-MiniLM-L6-v2")` — lazy singleton, loaded once per process
- Batch encode all act texts; cosine similarity per (act, indicator) pair
- `BM25Okapi` corpus built once per run; normalised by max (negative scores clipped to 0)
- Returns `list[ActIndicatorScore]` — all scores in [0.0, 1.0]

**ST5 — Exclusion Filter (`is_excluded`)**
- Checks `exclude_act_titles` (normalised title substring) and `exclude_keywords` (full text)
- Excluded acts logged to `logs/ranker_excluded_{economy}_{ts}.jsonl`; never sent to LLM gate
- Adding new rules requires only editing `taxonomy.json` — zero code changes
- Populated for all 10 indicators: banking/tax/immigration acts excluded from P6, criminal procedure excluded from P7

**ST6 — Score Fusion + DeepSeek LLM Gate (`_call_llm_gate`, `_run_gate_for_indicator`)**
- Fused score = `RANKER_SEMANTIC_WEIGHT * semantic + RANKER_BM25_WEIGHT * bm25` (configurable via `.env`)
- Top 20 by fused score sent to LLM gate sequentially (jitter: `GATE_LLM_JITTER_MS=200ms`)
- LLM cascade: DeepSeek R1 via Groq → Qwen 2.5 via Groq → Ollama qwen2.5:7b (offline)
- Binary PASS/FAIL prompt; ambiguous responses → FAIL + `GATE_AMBIGUOUS_RESPONSE` log
- Zero PASS → `RankerError`; fewer than 3 PASS → include UNCERTAIN acts with `flag_for_review=True`
- `RANKER_TOP_N` env var (default 5, min 3) controls shortlist size

**ST7 — Output Contract + Logs**
- `RankedAct` dataclass: 20 fields (identity, discovery, currency, ranking, quality)
- `run_ranker(currency_results, seed_data, taxonomy, economy_config, output_dir)` — full pipeline
- `logs/ranker_summary_{economy}_{ts}.json` — always written (even on partial failure)
- `logs/ranker_scores_{economy}_{ts}.jsonl` — one row per (act × indicator) pair
- `logs/ranker_excluded_{economy}_{ts}.jsonl` — all excluded acts with reason
- `logs/ranker_cost_{economy}_{ts}.jsonl` — per-gate-call token counts and cost

**ADR-012 — Negative BM25 scores clipped to 0 before normalisation**
`rank_bm25`'s BM25Okapi can return negative IDF for terms present in all corpus documents. Negative scores are clipped to 0 (semantically: too-common terms carry no discriminative signal) before dividing by max to get [0, 1] range.

**ADR-013 — `normalise_url` shared via direct import from `crawler.py`**
`seed_loader.py` and `ranker.py` import `_normalise_url` from `src.crawler.crawler` directly. No utility module needed; the test `test_url_normalisation_consistent_with_crawler` enforces identity.

### ✅ Completed: [Z2-1] Fetch + Route + OCR Stage 1 (language-based)

**Files created/changed:**
- `src/fetcher/models.py` — `Zone1Result`, `CostLogEntry`, `FetchedDocument`, `ActSegment`, `to_dict()` — shared contracts for all Zone 2 modules
- `src/fetcher/logger.py` — JSON-line structured logger (stdout INFO+ / rotating file DEBUG+); every record includes `timestamp`, `level`, `module`, `event`, `economy`, `url`
- `src/fetcher/extractors/__init__.py` — package init
- `src/fetcher/extractors/pdf_text.py` — pdfplumber extraction with section hierarchy parser (L1/L2/L3 heading detection), table→TSV flattening, mixed-page reclassification trigger, password-protection handling
- `src/fetcher/extractors/html_extractor.py` — BeautifulSoup extraction with URL anchor extraction (`location_reference_map`), charset detection (Content-Type → meta charset → chardet → utf-8), JS-rendered detection, boilerplate removal
- `src/fetcher/extractors/ocr_stage1.py` — OCR Stage 1: engine read from `economy_config.ocr_engine`, OpenCV preprocessing (grayscale, adaptive threshold, deskew), Tesseract (DICT output, no pandas), PaddleOCR singleton, CER gate (raises `OCRQualityError` at ≥5% for Stage-2 handoff), page-marker assembly
- `src/fetcher/segmenter.py` — consolidated volume splitter: PyMuPDF font-size + pattern boundary detection, PDF slicing, title extraction, short-segment merge (<3 pages), fixed-50-page fallback, extensible `volume_header_patterns` from YAML
- `src/fetcher/router.py` — Zone 2 entry point: `download()` with 2-attempt retry, content-type priority → byte-sniff type detection, `classify_pdf()`, `is_consolidated_volume()` (200+ pages OR ≥2 act headers in first 10 pages), full `route()` dispatch, `DownloadError`, `UnsupportedDocTypeError`
- `requirements.txt` — added `lxml`, `pymupdf`, `opencv-python`, `Pillow`, `chardet`, `fpdf2`
- `tests/test_z2_1_router.py` — 94 unit tests; 91.76% line coverage (≥90% target met); zero real HTTP calls
- `tests/fixtures/z2_1/sg.yaml`, `th.yaml` — economy config fixtures
- `tests/fixtures/z2_1/sso_agc_sample.html` — SSO portal page with `#anchor` ids
- `tests/fixtures/z2_1/pdpa_sg_sample.pdf` — minimal text-native PDF (3 pages, pdfplumber-readable)
- `tests/fixtures/z2_1/scanned_sample.pdf` — minimal scanned PDF (2 empty pages)

**What was built:**
- Full `FetchedDocument` output contract: identity, doc metadata, raw text, section hierarchy, `location_reference_map` (HTML), `cer_score` (OCR), segmentation flags, `flag_for_review`, `cost_log_entry`
- `FetchedDocument.validate()` — enforces non-empty text, valid URL, valid discovery tag, no UNKNOWN doc_type, required cost log
- Routing: TEXT_PDF → pdfplumber; SCANNED_PDF/IMAGE → OCR Stage 1; HTML → BeautifulSoup; consolidated volume → segment then route each segment
- Reclassification: TEXT_PDF with >30% empty pages → silently hands off to OCR Stage 1 via `ReclassifyToScannedError`
- OCR engine is always read from `economy_config.ocr_engine` (derived from `script_type` in YAML) — zero runtime override
- URL anchors (`location_reference_map`) populated for HTML extraction — judge differentiator
- All 13 structured log events from ClickUp spec implemented
- Hackathon cost logging: `cost_usd=0.0` for all local engines (pdfplumber, Tesseract, PaddleOCR, BeautifulSoup)

**ADR-014 — `FetchedDocument` is the single Zone 2 contract**
All extractors return `FetchedDocument`. Router, segmenter, and all downstream modules (chunker, mapper, writer) consume this type. Validation (`validate()`) is called at the end of every extractor before returning — silent extraction failures are impossible.

**ADR-015 — Existing `EconomyConfig` from `src/config/economy_config.py` reused**
The ClickUp spec described a `fetcher/models.py` EconomyConfig but the project already has a well-formed Pydantic one. `Zone1Result`, `FetchedDocument`, `CostLogEntry`, and `ActSegment` are defined in `src/fetcher/models.py`; `EconomyConfig` is imported from `src.config.economy_config`.

**ADR-016 — Tesseract uses `Output.DICT` (no pandas dependency)**
`pytesseract.image_to_data(output_type=Output.DICT)` returns a plain dict. Confidence scores computed with a list comprehension — no pandas import, no pandas dependency in requirements.

### 🔲 Pending: [Z2-2] Segment + Translate
`src/fetcher/segmenter.py` already handles volume splitting (Z2-1 ST5). Z2-2 needs to add: 3-layer DeepL translation, Google Translate fallback, `verbatim_original` + `translation` persistence, Thai Buddhist Era conversion.

### 🔲 Pending: [Z2-3] RAG Pipeline
`src/retrieval/chunker.py`, `embedder.py`, `rag.py` — article-level chunking, hybrid BM25 + dense retrieval, cross-encoder rerank, top-5 chunks with location_reference.

### 🔲 Pending: [Z2-4] LLM Mapping
`src/llm/client.py`, `src/mapping/mapper.py` — 5-tier cascade client; per-indicator mapping to `{indicator_id, article, verbatim_snippet, mapping_rationale, confidence}`.

### 🔲 Pending: [Z2-5] Validation + Confidence Flagging
`src/output/validator.py` — HTTP GET each source_url; Wayback snapshot; confidence < 0.80 → auto-note.

### 🔲 Pending: [Z2-6] Output Writer + Cost Logger
`src/output/writer.py`, `tools/cost_logger.py` — 13-column CSV (exact OUTPUT_TEMPLATE_31MAY.xlsx schema) + JSON envelope; actual per-component cost report.
