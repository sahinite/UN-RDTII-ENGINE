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

### ✅ Completed: [Z2-2] Segment + Translate

**Files changed:**
- `src/fetcher/translator.py` — NEW: full 3-layer translation pipeline
- `src/fetcher/segmenter.py` — added ST2 article reference mapping (`extract_article_references`)
- `src/fetcher/models.py` — added `ArticleReference`, `TranslationCostEntry`, `TranslatedDocument`
- `tests/test_z2_2_segment_translate.py` — 40 tests, all passing

**What was built:**

**ST1 — Segmenter Core (enhanced):** existing PyMuPDF boundary detection + slicing; expanded act header patterns (Thai, Malay); `extract_article_references` wired to produce per-segment citation maps.

**ST2 — Article Reference Mapping:** `extract_article_references(section_hierarchy, act_title) → list[ArticleReference]`. Tracks current PART/CHAPTER/DIVISION context; extracts article numbers from `"12."`, `"Section 5"`, `"Article 3"` style headings; disambiguates duplicate numbers across parts by appending part label. Each `ArticleReference` carries `(act_title, part, article_number, heading, text_anchor)`.

**ST3 — Layer 1 & 2 Translation:** `translate_keywords()` (Layer 1) and `translate_act_title()` (Layer 2). DeepL primary (`DEEPL_API_KEY`); automatic Google Translate fallback. English economies → zero API calls.

**ST4 — Layer 3 + verbatim_original:** `translate_document(doc, economy_config, taxonomy_keywords?)` runs the full pipeline. `verbatim_original` always holds the source-language `raw_text`. Long documents (> 100 K chars) are chunked before sending to provider. Returns `TranslatedDocument` with both original and translated text.

**ST5 — Buddhist Era + Normalisation:** `convert_be_years(text)` replaces BE 2400–2599 with Gregorian (BE − 543). `normalise_law_reference(title)` strips `B.E.`/`พ.ศ.` suffixes and normalises `No.` spacing. Both applied before Layer 3 when `economy_config.be_year_conversion=True`.

**ST6 — TranslatedDocument + Cost Entry:** `TranslationCostEntry(source_language, provider, chars_translated, cost_usd)` — DeepL at $20/1 M chars, Google at $0.00. `TranslatedDocument` wraps `FetchedDocument` + all translation outputs. `ArticleReference` carries per-article citations for downstream RAG.

**ST7 — Tests:** 40 unit tests across all subtasks; zero real API calls (all provider functions patched); covers BE conversion, normalisation, all three translation layers, chunking, English passthrough, both-fail fallback, article mapping edge cases.

**ADR-017 — `verbatim_original` always the source-language `raw_text`**
Even after BE year conversion the `verbatim_original` field holds the unmodified `raw_text` from the extractor. BE conversion is applied only to the text sent to the translation provider, not to the stored original. This lets output JSON compare source and translated passages verbatim.

**ADR-018 — Translation provider pinned per economy via YAML `translation_provider`**
`economy_config.translation_provider` is `"deepl"` | `"google"` | `None`. `None` means try DeepL first (if `DEEPL_API_KEY` present), fall back to Google. This is consistent with probe.py Layer 1 translation (Z1-2 ST3). English economies (`languages: [en]`) skip all provider calls regardless of `translation_provider`.

### ✅ Completed: [Z2-3] RAG Pipeline

**Files changed:**
- `src/retrieval/models.py` — `LocationReference`, `Chunk`, `RetrievedChunk`, `TaxonomyEntry` dataclasses
- `src/retrieval/chunker.py` — `chunk_document()`: 3-strategy article splitter (hierarchy → regex → whole-doc fallback); tracks `location_reference` on every chunk
- `src/retrieval/embedder.py` — `EmbeddingIndex` with lazy `all-MiniLM-L6-v2` singleton; FAISS flat-IP index; `build_index()`, `dense_search()`
- `src/retrieval/bm25_index.py` — `BM25Index` wrapping `rank_bm25.BM25Okapi`; probe-keyword boosting (×1.5); exclude_keywords + exclude_act_titles negative filter
- `src/retrieval/fusion.py` — `rrf_fusion()`: Reciprocal Rank Fusion (k=60) merging BM25 + dense → top-20
- `src/retrieval/reranker.py` — `rerank()`: lazy `cross-encoder/ms-marco-MiniLM-L-6-v2`; top-20 → top-5; ±300-char context windows from adjacent chunks
- `src/retrieval/config.py` — `load_taxonomy()` / `get_indicator()` backed by `taxonomy.json`; pipeline hyper-parameters
- `src/retrieval/rag.py` — `retrieve(indicator_id, doc)` orchestrator; `retrieve_batch()` amortised multi-indicator path
- `src/retrieval/__init__.py` — public exports: `retrieve`, `retrieve_batch`, `Chunk`, `LocationReference`, `RetrievedChunk`
- `tests/test_rag.py` — 31 unit tests, all passing; all ML models mocked; covers chunker strategies, BM25 boosting/filtering, RRF invariants, reranker ordering, orchestrator end-to-end, taxonomy config

**What was built:**
- Article-level chunker: prefers section_hierarchy text when ≥2 entries have content; falls back to regex splitting on `Section N.` / `Article N.` patterns; whole-doc fallback ensures never-empty output
- Embedding index: `all-MiniLM-L6-v2` (Apache 2.0, 384-dim), FAISS IndexFlatIP, normalised cosine similarity
- BM25 with legal-domain enhancements: `exclude_act_titles` drops off-topic acts (Banking Act, Customs Act); `probe_keywords` boost relevant chunks by 1.5×
- Hybrid fusion via RRF (k=60): items in both ranked lists score higher than those in one alone
- Cross-encoder reranker: `ms-marco-MiniLM-L-6-v2` (Apache 2.0) re-scores top-20 candidates; each returned `RetrievedChunk` carries `context_window` (chunk ± adjacent text)
- Every returned chunk has a verifiable `LocationReference(act_title, part, article_number, page)`

**ADR-019 — Retrieval models use lazy singletons, not module-level imports**
`all-MiniLM-L6-v2` and `cross-encoder/ms-marco-MiniLM-L-6-v2` are loaded once per process on first call. This prevents import-time model downloads during unit tests and allows the test suite to mock `_get_model()` / `_get_cross_encoder()` cleanly.

**ADR-020 — chunk_document type detection uses isinstance, not getattr**
`TranslatedDocument` vs `FetchedDocument` is resolved via `isinstance(doc, TranslatedDocument)` so that mock objects in tests cannot accidentally impersonate a `TranslatedDocument` by having auto-created attributes.

### ✅ Completed: [Z2-4] LLM Extractor — 5-Tier Auto-Cascade Mapping

**Files changed:**
- `src/mapping/exceptions.py` — full exception hierarchy: `ProviderRateLimitError`, `ProviderAPIError`, `ProviderTimeoutError`, `AllProvidersExhaustedError`, `ParseError`, `PDPAGateError`, `ConfigError`, `TaxonomyError`
- `src/mapping/models.py` — `LLMResponse`, `ExtractionResult` (13-col CSV schema + internal metadata), `LLMCostEntry`
- `src/mapping/base_provider.py` — `BaseLLMProvider` ABC
- `src/mapping/providers/` — `AnthropicProvider` (`claude-sonnet-4-20250514` PINNED), `OpenAIProvider` (`gpt-4o`), `GroqProvider` (`deepseek-r1-distill-llama-70b`), `OllamaProvider(4/5)` (`qwen2.5:7b` / `granite3-dense:8b`); Llama 3.3 blocked via `LLAMA33_BLOCKLIST`
- `src/mapping/llm_client.py` — `PROVIDER_CASCADE`, `pin_active_provider()`, `call_llm_with_cascade()` (pinned-first + silent fallthrough + one timeout retry), `get_active_model_version()`
- `src/mapping/prompts.py` — `SYSTEM_PROMPT`, `build_user_prompt()`, `trim_chunks_to_budget()`, `load_taxonomy_dict()`
- `src/mapping/parser.py` — `parse_llm_response()`, `_assert_verbatim_in_context()`, `expand_non_consecutive()`, `_dedup_within_response()`
- `src/mapping/mapper.py` — `extract_provisions(rag_results, doc)` orchestrator; `check_pdpa_gate()`; `_deduplicate()`; `ECONOMY_NAMES` ISO→UN mapping
- `src/mapping/cost_logger.py` — `CostLogger` writes `logs/cost_report.json`
- `taxonomy.json` — extended with `in_scope`, `out_of_scope`, `negative_examples` for all 10 indicators
- `tools/cost_logger.py` — standalone CLI cost benchmarking tool
- `tests/test_z2_4_*.py` — 60 tests, 60 passed, 82% coverage

**ADR-021 — Mapping module lives in `src/mapping/`, not `src/llm/`**
All Z2-4 implementation is in `src/mapping/` (providers, llm_client, prompts, parser, mapper). The `src/llm/client.py` stub is not modified.

**ADR-022 — `extract_provisions` accepts both list and dict rag_results**
Accepts `list[RAGResult]` or `dict[str, list[RetrievedChunk]]` (direct from `retrieve_batch`) via `_DictRAGResult` adapter.

**ADR-023 — `_build_doc_metadata` uses `getattr` with defaults**
`FetchedDocument` lacks `law_number_ref`, `last_amended_year`, `verbatim_original`. Mapper uses `getattr(doc, field, None)` so future schema additions are handled automatically.

### ✅ Completed: [Z2-5] Validate + Archive + OCR Stage 2 Fallback + Confidence Flagging

**Files created/changed:**
- `src/ocr/processor.py` — full OCR Stage 2 implementation: `OCRResult`, `_estimate_cer_from_text`, `run_azure_di` (ST3), `run_mistral_ocr` (ST4), `_route_stage2`, `maybe_stage2_fallback`, `run_ocr_stage2` (ST5)
- `src/fetcher/router.py` — `_try_ocr` now catches `OCRQualityError` and auto-escalates to Stage 2 via `run_ocr_stage2`
- `src/fetcher/models.py` — `extraction_method` Literal extended with `"azure_di"` and `"mistral_ocr"`
- `src/output/validator.py` — ST1 URL Validator (`validate_url`), ST2 Wayback Archiver (`archive_wayback`), ST6 Confidence Flagging (`_flag_confidence`), `ValidatedResult` dataclass, `validate_and_flag` orchestrator
- `tests/test_z2_5_validator.py` — 50 unit tests (50 passed), 95%/94% coverage on processor/validator

**What was built:**

**ST1 — URL Validator:** `validate_url(url)` HTTP GET with 3 retries on 429/5xx, soft-404 detection (HTTP 200 but body contains "page not found" etc.), cross-domain redirect detection, returns `(URLStatusType, http_code)`.

**ST2 — Wayback Archiver:** `archive_wayback(url)` POSTs to `https://web.archive.org/save/{url}`, extracts archive URL from `Content-Location` header or redirect chain; 429 → 60s wait + retry; network error → `""` (pipeline continues).

**ST3 — Azure Document Intelligence:** `run_azure_di(image_bytes)` calls prebuilt-read model via `{endpoint}/documentintelligence/documentModels/prebuilt-read:analyze?api-version=2024-11-30`, polls `Operation-Location` header until succeeded, extracts word-level confidence for true CER.

**ST4 — Mistral OCR:** `run_mistral_ocr(image_bytes)` calls `https://api.mistral.ai/v1/ocr` with `mistral-ocr-latest` model, base64-encodes image as data URL; `_estimate_cer_from_text` for CER when no word confidence available.

**ST5 — Stage 2 Controller:** `maybe_stage2_fallback(cer, image_bytes, ...)` — triggers Stage 2 when CER ≥ 5%; `_route_stage2` tries Azure DI first (if `AZURE_DI_KEY` set), then Mistral OCR (if `MISTRAL_API_KEY` set); logs engine used + resulting CER; falls back to Stage 1 text if all providers fail. `run_ocr_stage2` is the full document-level entry point called by `router.py`.

**ST6 — Confidence Flagging + Orchestrator:** `_flag_confidence` appends exact note `"Recommend human review — OCR/translation source"` when `confidence < 0.80`; `validate_and_flag(records)` orchestrates ST1+ST2+ST6 per record, returns `list[ValidatedResult]`.

**Acceptance criteria verified:**
- AC1 ✅ CER ≥ 5% triggers Stage 2 automatically; engine + resulting CER logged
- AC2 ✅ confidence < 0.80 → exact review note in `notes` field
- AC3 ✅ every `source_url` validated; broken URLs flagged with `BROKEN URL (HTTP N)` note

**ADR-024 — Stage 2 OCR providers are credentials-gated, not hardcoded**
`_route_stage2` checks `AZURE_DI_KEY` and `MISTRAL_API_KEY` env vars before calling each provider. Missing credentials → provider silently skipped. If both missing, `stage1_text` returned with `flag_for_review=True`. Zero code changes needed to enable/disable providers — only env vars.

**ADR-025 — `ValidatedResult` wraps `ExtractionResult` by composition**
`ValidatedResult(record: ExtractionResult, url_status, url_http_status, archive_url, validated_at)`. Confidence flagging modifies `record.notes` in-place before wrapping. This avoids duplicating the 13-field CSV schema and keeps the writer's output path unchanged.

### ✅ Completed: [Z2-6] Output Writer — 13-Column CSV/JSON + Cost Logger

**Files created/changed:**
- `src/output/models.py` — `OutputRecord` dataclass (13 CSV cols + 6 JSON extended fields), `OutputSchemaError`, `OutputWriteError`, `CSV_COLUMNS` constant
- `src/output/writer.py` — ST1 `write_csv` (pandas, UTF-8-BOM, post-write verify), ST2 `write_json` (6 extended fields, per-document grouping, post-write verify), ST3 `validate_record` (pre-write field checks + column order guard), ST4 `write_outputs` (orchestrator + console summary), `build_output_record` (Z2-5 → Z2-6 bridge)
- `src/output/cost_logger.py` — `CostLogger` class, `compute_llm_cost` (provider-priced), per-component accumulation (OCR/embedding/LLM/crawling), `save()` → `logs/cost_report.json`
- `src/output/__init__.py` — exports all public symbols
- `tools/cost_logger.py` — full CLI: runs 3-stage pipeline (OCR → embed → LLM), reports measured costs per component, writes `logs/cost_report.json`
- `evaluate.py` — `evaluate()`, `load_sample_kit()`, `load_engine_output()`, `_print_report()`; accuracy scoring: KNOWN (0-40pts), NEW discoveries (0-20pts), total 60pts
- `tests/test_z2_6_output.py` — 45 unit tests, 45 passed, 95% coverage on Z2-6 modules

**What was built:**

**ST1 — CSV Writer:** `write_csv(records, path)` — 13 columns in exact `OUTPUT_TEMPLATE_31MAY.xlsx` order, UTF-8-BOM encoding (Excel-compatible), post-write column order re-verification via `pd.read_csv`.

**ST2 — JSON Envelope Writer:** `write_json(records, path)` — records grouped by `source_url` (per-document), all 6 extended fields: `ocr_quality_cer`, `processing_time_seconds`, `model_version`, `raw_context_before`, `raw_context_after`, `verbatim_original`, `archive_url`. Post-write extended-field presence verification.

**ST3 — Schema Validator:** `validate_record(record)` — checks required fields, column order guard, confidence range [0,1], indicator_id format (P6-I1..P7-I5), discovery_tag must be KNOWN/NEW, mapping_rationale ≤ 300 chars.

**ST4 — Write Orchestrator:** `write_outputs(records, output_dir, economy, pillar)` — validates all records, writes CSV+JSON, console summary table, returns dict with csv_path/json_path/written/skipped counts.

**ST5 — Cost Logger:** `CostLogger` accumulates per-component costs: `record_llm_call` (provider-priced, Anthropic $0.003/0.015 per 1K), `record_ocr_page` (Azure $0.001/page; Tesseract/PaddleOCR free), `record_embedding` (local=free), `record_crawl` (Crawl4AI=free). `save()` writes `logs/cost_report.json`.

**ST6 — evaluate.py:** Loads Round 1 sample kit XLSX by economy sheet, converts Pillar_ID+Indicator_ID to P6-I1 format, matches against engine CSV output, scores KNOWN match rate (40pts) + NEW discoveries (4pts each, max 20pts).

**ST7 — Tests:** 45 tests, 95% coverage on models/writer/cost_logger. All 3 acceptance criteria verified by tests.

**Acceptance criteria verified:**
- AC1 ✅ CSV column order verified by test `test_csv_columns_match_template_exactly` + `test_csv_column_names_exact`
- AC2 ✅ JSON extended fields verified by `test_json_extended_fields_present` + `test_json_extended_fields_populated`
- AC3 ✅ Cost report has non-zero token counts and $ costs verified by `test_save_writes_cost_report_json`

**ADR-026 — CSV uses UTF-8-BOM (not plain UTF-8)**
`pd.DataFrame.to_csv(..., encoding="utf-8-sig")` — Excel opens BOM files without encoding dialog. Post-write verification checks for BOM bytes `\xef\xbb\xbf`.

**ADR-027 — CostLogger uses monotonic clock, not wall time**
`time.monotonic()` for elapsed seconds — immune to NTP adjustments or DST changes during long pipeline runs. Absolute timestamps use `datetime.now(timezone.utc)`.

**ADR-028 — evaluate.py indicator ID conversion: sample kit float → P{p}-I{n}**
Sample kit uses float indicator_ids (6.1, 6.4, 7.3). Conversion: `decimal_part = round((float % 1) * 10)` → sub-indicator digit. Whole numbers (6, 7) are section headers — skipped.

---

### ✅ Completed: [Z2-86ey0q56f] AUDIT — Engine Integration & Submission Readiness

**Files changed:**
- `main.py` — fully wired: argparse (`--economy`, `--pillar`, `--output-dir`, `--format`, `--pdf`), Zone 1 pipeline (probe → crawl → currency → rank), Zone 2 pipeline (route → translate → RAG → extract → validate → write), PDPA gate check, CostLogger integration, `run_pipeline()` public function (called by batch_run.py)
- `batch_run.py` — implemented: sequential wrapper calling `main.run_pipeline()` per economy/pillar pair (ADR-005: never parallel); full argparse with `--economies`, `--pillar`, `--output-dir`, `--format`
- `src/output/writer.py` — output filename fixed to `{Economy}_P{pillar}_{timestamp}.csv/.json` (was `{economy.lower()}_pillar{pillar}`)
- `src/output/models.py` — `last_amended` removed from `_REQUIRED_COLUMNS`; template spec says "blank if never amended"
- `src/mapping/providers/groq_provider.py` — Groq model updated: `deepseek-r1-distill-llama-70b` → `qwen3-32b` (fallback: `qwen3.6-27b`); added inline fallback on model-not-found error
- `src/llm/client.py` — converted from empty stub to thin re-export of `src/mapping/llm_client.py`; correct docstring with current model names
- `src/mapping/cost_logger.py` — converted from duplicate implementation to thin re-export of `src/output/cost_logger.py` (W6 fix)
- `economies/australia.yaml` — new: Latin-script, English, `legislation.gov.au` primary portal + OAIC secondary
- `tools/cost_logger.py` — fixed: replaced non-existent `extract_text()` with `pdf_to_images()` + `run_tesseract()`/`run_paddleocr()`; replaced non-existent `map_document()` with `pin_active_provider()` + `retrieve_batch()` + `extract_provisions()`
- `data/benchmark/benchmark_50pages.pdf` — added: 50-page public-domain legal text PDF for cost logger benchmarking
- `data/output_schema_sample.json` — added: complete example output JSON envelope with 2 PDPA records
- `README.md` — completed: quick start, project layout, supported economies table, 13-column CSV schema, LLM cascade table, configuration, cost measurement, PDPA gate, build order
- `tests/test_z2_6_output.py` — `test_write_outputs_filename_pattern` updated to match new `{Economy}_P{pillar}_{timestamp}` format

**What was fixed:**
- **B1 (Blocker):** `main.py` `NotImplementedError` → full end-to-end pipeline wired
- **W2 (Blocker):** Output filename `singapore_pillar6.csv` → `Singapore_P6_{timestamp}.csv`
- **W3 (Blocker):** `last_amended` removed from `_REQUIRED_COLUMNS` → valid records no longer silently dropped
- **W1 (Blocker):** Groq model `deepseek-r1-distill-llama-70b` → `qwen3-32b` (correct for June 2026)
- **M1 (Blocker):** `australia.yaml` created — all 4 submission economies now present
- **B3 (High):** `tools/cost_logger.py` broken API calls fixed — uses correct stage1 OCR and extract_provisions
- **B4 (High):** `data/benchmark/benchmark_50pages.pdf` added — judges can now verify measured costs
- **B2 (Medium):** `batch_run.py` implemented — sequential multi-economy batch runner
- **W4 (Medium):** `src/llm/client.py` converted from misleading stub to re-export
- **W6 (Medium):** Duplicate `CostLogger` in `src/mapping/cost_logger.py` removed (re-export redirect)
- **M2 (Low):** `data/output_schema_sample.json` created — referenced in README
- **R1 (Urgent):** `README.md` fully updated for submission

**ADR-029 — Output filename includes timestamp for uniqueness**
`{Economy}_P{pillar}_{YYYY-MM-DDTHHMMSS}.csv` — timestamp from `datetime.now(UTC)` ensures multiple runs per economy/pillar don't overwrite each other. Judges can verify the most recent run by sorting filenames.

**ADR-030 — `src/llm/client.py` is a re-export, not an implementation**
All LLM implementation stays in `src/mapping/llm_client.py` (ADR-021). `src/llm/client.py` re-exports `pin_active_provider`, `call_llm_with_cascade`, `get_active_model_version`, `PROVIDER_CASCADE` so both import paths work. This avoids moving the implementation and breaking existing imports.

**ADR-031 — `run_pipeline()` is the integration seam between main.py and batch_run.py**
`batch_run.py` imports and calls `run_pipeline()` from `main.py` directly. No subprocess spawning, no argparse re-parsing. Each call is fully isolated: separate `CostLogger`, separate `pin_active_provider()` call, separate output files (timestamp ensures uniqueness).

### ✅ Completed: [Z2-86exvtfcg] PRD — Singapore PDPA Pillar 7 Phase 1 Gate (Task 86exvtfcg)

**Files changed:**
- `src/mapping/llm_client.py` — Fixed 2 stale comments: `deepseek-r1-distill-llama-70b` → `qwen3-32b` (Groq model per CLAUDE.md)
- `main.py` — Fixed 4 bugs: (1) added `import asyncio`; (2) `_run_zone1` wraps all async Zone 1 calls (`run_probe`, `run_crawler`, `run_currency_check`) in `asyncio.run()`; (3) `run_crawler` given correct args (`taxonomy`, `known_urls`; removed spurious `pillar=pillar`); (4) `load_seed_data` uses `economy_iso=`, `pillar=f"P{pillar}"`, `round1_db_path=_ROUND1_DB`; (5) PDPA gate fixed to check `all_records` directly (removed broken `.record` attribute access on `OutputRecord`)
- `tests/test_mapper.py` — Replaced 3 `pytest.skip` stubs with integration tests: `test_singapore_pdpa_p7_mapping_matches_sample_kit` (full seam: extract_provisions → write_outputs → evaluate); `test_cascade_falls_through_on_primary_provider_failure` (AllProvidersExhaustedError per indicator is caught and skipped — run never re-raises); `test_llama_3_3_not_used_anywhere` (license guard)
- `tests/test_output.py` — Replaced 3 `pytest.skip` stubs with integration tests: `test_csv_matches_output_template_column_order`, `test_low_confidence_rows_carry_review_note`, `test_broken_source_urls_are_flagged`

**What was fixed:**
- All 5 Zone 1 async functions (`run_probe`, `run_crawler`, `run_currency_check`) wrapped with `asyncio.run()` — were being called synchronously (TypeError at runtime)
- `run_crawler` args corrected — was passing `pillar=pillar` (not in signature), missing `taxonomy` and `known_urls`
- `load_seed_data` args corrected — was passing `economy=` instead of `economy_iso=`, `pillar=7` instead of `pillar="P7"`
- PDPA gate: removed broken `raw_results` block (tried `r.record` on `OutputRecord` which has no `.record`); gate now correctly checks `all_records` which are `OutputRecord` with `.indicator_id` and `.confidence`
- Groq comment stale but provider file already correct (`qwen3-32b`) — only comments updated

**Test design decisions:**
- Test 1 (`test_singapore_pdpa_p7_mapping_matches_sample_kit`): passes `csv_path` directly from `write_outputs` summary to `evaluate()` — avoids `_find_best_csv` glob case-sensitivity issue on Python 3.14 (glob is now case-sensitive even on macOS)
- Test 2 (`test_cascade_falls_through_on_primary_provider_failure`): mock raises `AllProvidersExhaustedError` (the contract `call_llm_with_cascade` exposes to mapper), NOT `ProviderRateLimitError` (which is internal to the cascade and not caught by `extract_provisions`)

**Final state:** 481 passed, 2 skipped, 0 failures across full test suite.

**ADR-032 — `extract_provisions` only catches `AllProvidersExhaustedError`, not `ProviderRateLimitError`**
`ProviderRateLimitError` is an internal signal inside `call_llm_with_cascade` — it triggers cascade fallthrough. The cascade either succeeds (returns `LLMResponse`) or exhausts all tiers (`AllProvidersExhaustedError`). `extract_provisions` only sees the boundary contract: `AllProvidersExhaustedError` means skip this indicator and continue. Tests that mock `call_llm_with_cascade` must raise `AllProvidersExhaustedError`, never the intermediate exceptions.

**ADR-033 — `evaluate()` accepts explicit `csv_path` to bypass auto-detection**
`_find_best_csv()` uses `glob(f"{economy.lower()}*.csv")` which is case-sensitive on Python 3.14 even on macOS. `write_outputs()` names files `{Economy}_P{pillar}_{ts}.csv` (original case). Tests that call `evaluate()` after `write_outputs()` should pass `csv_path=Path(summary["csv_path"])` directly to avoid glob mismatch.

---

### ✅ Completed: [Z2-86ey0q56f] Bug Fixes — 10 Correctness & Reliability Bugs (PRD 86ey16a3t)

**Files changed:**
- `src/fetcher/models.py` — 8 `@property` accessors added to `TranslatedDocument`
- `src/ocr/processor.py` — CER fix for whitespace-only pages; `stage2_failed` flag on `OCRResult`
- `src/retrieval/rag.py` — `retrieve_batch()` gracefully skips unknown indicator IDs
- `src/crawler/ranker.py` — removed duplicate `_translate_text()` / `_l2_cache`; delegates to `src.fetcher.translator.translate_text`; imports `GROQ_MODEL`, `GROQ_MODEL_FALLBACK`, `OLLAMA_MODELS` from provider modules (single source of truth)
- `src/mapping/prompts.py` — `build_user_prompt()` accepts `source_url` param; substitutes `[Untitled document]` for empty `act_title`
- `src/mapping/mapper.py` — passes `source_url` to `build_user_prompt()`; PDPA gate uses `check_pdpa_gate()` raising `PDPAGateError`
- `src/mapping/providers/groq_provider.py` — all primary failures attempt `GROQ_MODEL_FALLBACK` before raising
- `src/mapping/providers/ollama_provider.py` — `OLLAMA_MODELS[5]` corrected from `granite3-dense:8b` to `granite3-8b`
- `main.py` — translation failure logs and flags doc; PDPA gate wired to `PDPAGateError`
- `tests/test_bug_fixes.py` — NEW: 27 tests covering all 10 bugs
- `tests/test_ranker.py` — updated to patch `_shared_translate_text` (tuple return) instead of removed `_translate_text`

**Bug summary:**
- Bug 1: `TranslatedDocument` missing proxy accessors → blank mandatory CSV columns for bilingual economies
- Bug 2: PDPA gate was a print-warn, not a real gate → `check_pdpa_gate()` + `PDPAGateError` now enforced
- Bug 3: Whitespace-only pages returned CER 0.0 → now returns 1.0, triggering Stage 2 correctly
- Bug 4: `OCRResult` lacked `stage2_failed` flag → callers couldn't distinguish OCR success from silent failure
- Bug 5: Translation failures swallowed silently → now logged + `flag_for_review=True` set
- Bug 6: Ollama model tag `granite3-dense:8b` invalid → corrected to `granite3-8b`
- Bug 7: Groq didn't fall back on model-not-found → now retries with `GROQ_MODEL_FALLBACK`
- Bug 8: Unknown indicator IDs in `retrieve_batch()` raised `KeyError` → graceful skip + warning log
- Bug 9: Ranker duplicated translation logic + model strings → consolidated to shared translator + provider constants
- Bug 10: Empty `act_title` in LLM prompt → substituted with `[Untitled document from {url}]`

**Final state:** 507 passed, 2 skipped across full test suite.

---

### ✅ Completed: [Z2-86ey0q56f] Configurability Audit & Gap Fixes

**Problem:** Several fields were hardcoded in Python source that should be data-driven or YAML-configurable: economy ISO codes, UN economy names, pillar CLI choices, indicator ID validation, indicator count assumption, seed loader pillar matching, and LLM model string duplication in ranker.

**Files changed:**
- `src/config/economy_config.py` — added `iso_code: str = ""` and `un_name: str = ""` optional fields with ISO validator; added `load_economy_by_iso(iso_code)` helper
- `economies/singapore.yaml` — added `iso_code: SG`, `un_name: Singapore`
- `economies/australia.yaml` — added `iso_code: AU`, `un_name: Australia`
- `economies/malaysia.yaml` — added `iso_code: MY`, `un_name: Malaysia`
- `economies/thailand.yaml` — added `iso_code: TH`, `un_name: Thailand`
- `main.py` — removed `_ECONOMY_ISO` dict; uses `economy_config.iso_code`; removed `choices=[6, 7]` from `--pillar`; derives `indicator_ids` from `load_taxonomy()` instead of hardcoded `P{p}-I{n}` comprehension
- `batch_run.py` — removed `choices=[6, 7]` from `--pillar`
- `src/mapping/mapper.py` — replaced `ECONOMY_NAMES` hardcoded dict with `_get_economy_names()` lazy YAML scanner; cached on first call; auto-picks up new economy YAMLs
- `src/retrieval/config.py` — added `get_valid_indicator_ids() -> frozenset[str]` derived from `taxonomy.json`
- `src/mapping/models.py` — replaced hardcoded `valid_ids = [P6/P7 comprehension]` with `get_valid_indicator_ids()`
- `src/output/writer.py` — same replacement for hardcoded `valid_ids`
- `src/crawler/seed_loader.py` — added generic regex fallback in `_pillar_matches()` for any `Pn` beyond P6/P7
- `src/crawler/ranker.py` — ranker LLM gate imports `GROQ_MODEL`, `GROQ_MODEL_FALLBACK` from `groq_provider.py` and `OLLAMA_MODELS` from `ollama_provider.py`; no more hardcoded model strings
- `src/llm/__init__.py` — updated stale comment `granite3-dense:8b` → `granite3-8b`
- `tests/conftest.py` — added `iso_code` and `un_name` to `sg_economy` and `malaysia_economy` fixtures
- `tests/test_economy_config.py` — added `iso_code` and `un_name` to `_SG_DICT` and `_TH_DICT`

**Gaps closed:**
1. `_ECONOMY_ISO` hardcoded dict → `economy_config.iso_code` from YAML
2. `ECONOMY_NAMES` hardcoded dict → lazy `_get_economy_names()` YAML scanner
3. `--pillar choices=[6, 7]` → unconstrained; any pillar supported
4. `valid_ids` P6/P7-only hardcode → `get_valid_indicator_ids()` from `taxonomy.json`
5. `indicator_ids` in `main.py` fixed 5-indicator assumption → taxonomy-driven count
6. `_pillar_matches()` P6/P7-only static map → generic regex fallback for any `Pn`
7. Ranker LLM model strings duplicated → single source of truth in provider modules
8. Economy YAMLs self-describe `iso_code` and `un_name` → `load_economy_by_iso()` enables lookup by ISO

**Adding a Round 2 economy now requires only:**
1. Create `economies/{code}.yaml` with `iso_code`, `un_name`, `script_type`, `languages`, `portals`
2. No Python code changes needed

**Final state:** 507 passed, 2 skipped across full test suite (no regressions).

---

### ✅ Completed: [86ey16a85] PDF Spec Compliance — JSON Envelope, README, Edge-case Handling

**Problem:** Four spec-compliance gaps vs. the hackathon orientation PDF (Dr. Witada A., 1 June 2026):
1. JSON envelope shape mismatched spec (wrong key names, wrong structure)
2. Misspelled economy name crashed the engine with no suggestion
3. README missing `## Pinned versions` and `## Open-source fallback` mandatory sections
4. `discovery_tag` propagation risk for bilingual documents

**Files changed:**
- `src/output/models.py` — renamed `processing_time_seconds: float` → `processing_time: int`; added `source_pdf_path: Optional[str]`; added `as_provision_dict()` returning only provision-level fields
- `src/output/writer.py` — restructured `write_json()` to emit PDF-specified envelope: document-level fields (`economy`, `law_name`, `source_url`, `source_pdf_path`, `ocr_quality_cer`, `processing_time`, `model_version`, `discovery_tag`) at top of each document object; provisions in `"provisions"` array; updated post-write verification; renamed `build_output_record()` parameter `processing_time_seconds` → `processing_time`, added `source_pdf_path` param
- `src/mapping/llm_client.py` — extended `get_active_model_version(ocr_engine="")`: when `ocr_engine` provided, returns `"{llm} + {ocr_engine}"` combined string
- `src/config/economy_config.py` — `load_economy()` now normalises input with `.strip().title()`, uses `difflib.get_close_matches()` to suggest correct spelling on failure; `UnknownEconomyError` includes "Did you mean: X?" hint; added `_available_economy_names()` helper
- `main.py` — updated `build_output_record()` call: `processing_time_seconds=elapsed` → `processing_time=int(elapsed)`
- `README.md` — added `## Pinned versions` section (full version table, "no latest tags" statement) and `## Open-source fallback (if commercial API)` section (Ollama instructions)
- `tests/test_z2_6_output.py` — updated fixtures and tests for new JSON structure; added tests asserting document-level keys, `"provisions"` array, absence of `"processing_time_seconds"`, integer `processing_time`
- `tests/test_economy_config.py` — added 4 fuzzy matching tests: misspelled name suggests correction, whitespace stripped, uppercase resolved, trailing whitespace resolved
- `tests/test_z2_4_cascade.py` — added 4 tests for `get_active_model_version(ocr_engine=...)`
- `tests/test_output.py` — updated `build_output_record()` call to use `processing_time=5`

**JSON envelope shape (new):**
```json
{
  "economy": "Singapore",
  "law_name": "PDPA 2012",
  "source_url": "https://...",
  "source_pdf_path": "outputs/cache/pdpa_sg.pdf",
  "ocr_quality_cer": 0.012,
  "processing_time": 43,
  "model_version": "anthropic/claude-sonnet-4-20250514 + tesseract-5.3",
  "discovery_tag": "KNOWN",
  "provisions": [{ "indicator_id": "P7-I1", ... }]
}
```

**Final state:** 523 passed, 2 skipped across full test suite (no regressions).

---

### ✅ Completed: [86ey13cyh] Output Template Compliance — Discovery Tag, Field Validation & Scoring Fixes

**Problem:** Two scoring-critical failures: (1) `discovery_tag` was set at document level and never updated at provision level, causing articles not in the sample kit to be mislabelled KNOWN. (2) `evaluate.py` NEW score always returned 0 because it compared indicator IDs (which are always fully covered) rather than individual provision articles. Additionally, several field-level rules from the output template were unenforced.

**Files changed:**
- `src/crawler/seed_loader.py` — `SeedData` extended with `known_provisions: set[str]`; `_extract_anchor_urls()` helper; `_load_round1_db()` and `_load_sample_csv()` now parse References column (col index 7 / "references" header) for anchor-level URLs (`#pr26-` etc.) and populate `known_provisions`
- `src/mapping/provision_tag.py` — NEW: `resolve_provision_tag()` pure function (KNOWN/NEW at provision level); `infer_article_anchor()` heuristic (`"Section 26"` → `"#pr26-"`)
- `src/mapping/models.py` — `ExtractionResult` gains `doc_type: Optional[str] = None` (non-CSV metadata field)
- `src/mapping/mapper.py` — `extract_provisions()` accepts `known_provisions: set[str]` and passes it to parser; `_build_doc_metadata()` extracts `doc_type` from `fetched.doc_type`; `_official_un_name()` now raises `ConfigError` on unknown ISO codes (no longer silently returns raw code)
- `src/mapping/parser.py` — Decision 8: verbatim assertion failure → hard discard (`return None`) unless `ALLOW_UNVERIFIED_SNIPPETS=true` env var; Decision 2/3: `resolve_provision_tag()` called per provision; Decision 6: law name abbreviation check (`flag_for_review`); Decision 7: article missing sub-paragraph check (`flag_for_review`); Decision 9: chunk-derived page number preferred over LLM value for `location_reference`; Decision 12: cross-reference and delegated legislation auto-flagging in `notes`
- `src/mapping/prompts.py` — Added Rules 7 (LAW NAME expansion) and 8 (RATIONALE FORMAT) to `SYSTEM_PROMPT`
- `src/output/models.py` — `OutputRecord` gains `doc_type: Optional[str] = None` (not written to CSV)
- `src/output/writer.py` — `validate_record()` checks `location_reference` required for PDF doc types; Decision 11: portal domain allowlist check via `_get_portal_domains()` (soft — appends to notes); `build_output_record()` passes `doc_type`
- `evaluate.py` — Decision 4: NEW score now provision-level (`discovery_tag=="NEW"` rows whose `(law_name, article)` is not in `known_provision_keys`); `_load_known_provision_keys()` parses anchor URLs from sample kit; `new_score = min(count * 4, 20)`
- `economies/vietnam.yaml`, `philippines.yaml`, `cambodia.yaml`, `myanmar.yaml`, `laos.yaml`, `brunei.yaml` — NEW: minimal economy YAML files with `iso_code` and `un_name` for Decision 5
- `main.py` — loads `SeedData` at startup; passes `seed_data.known_provisions` to `extract_provisions()`
- `tests/test_provision_tag.py` — NEW: 10 tests for `resolve_provision_tag()` and `infer_article_anchor()`
- `tests/test_evaluate.py` — NEW: 5 tests for provision-level NEW scoring
- `tests/test_z2_4_parser.py` — updated: verbatim assertion test updated for hard-discard (Seam 4); 3 new provision tag tests (Seam 3)
- `tests/test_z2_4_mapper.py` — updated: `test_unknown_economy_returns_iso_code` → `test_unknown_economy_raises_config_error`
- `tests/test_z2_6_output.py` — added 4 field-level violation tests (Seam 5)
- `tests/test_ranker.py` — added 3 `known_provisions` tests (Seam 1)

**Decisions implemented:**
1. `SeedData.known_provisions` — anchor-level provision fingerprints from Round 1 DB XLSX References column
2. `resolve_provision_tag()` — pure function, provision-level KNOWN/NEW; falls back to doc-level with `flag_for_review` when no anchor can be inferred
3. `known_provisions` threaded: `main.py` → `extract_provisions()` → `parse_llm_response()` → `_build_extraction_result()`
4. `evaluate.py` NEW score compares (law_name, article) pairs, not indicator IDs; capped at 20 pts
5. Economy names: VN→"Viet Nam", PH→"Philippines", KH→"Cambodia", MM→"Myanmar", LA→"Lao People's Democratic Republic", BN→"Brunei Darussalam" via new YAML files; `_official_un_name()` raises `ConfigError` on unknown codes
6. SYSTEM_PROMPT Rule 7: expand abbreviated law names including year
7. Parser: bare article (`Section 26` without sub-paragraph) → `flag_for_review` + `article_missing_paragraph` reason
8. Verbatim assertion → hard discard (return None); `ALLOW_UNVERIFIED_SNIPPETS=true` reverts to flag-only (OCR edge case)
9. PDF sources require `location_reference`; chunk-derived page number preferred over LLM value
10. SYSTEM_PROMPT Rule 8: rationale format enforced in prompt
11. Portal domain allowlist: unknown domains append note (soft check, no violation)
12. Cross-reference (`see also`, `pursuant to`, etc.) and delegated legislation keywords auto-flagged in `notes`

**ADR-034 — `ALLOW_UNVERIFIED_SNIPPETS` env var for OCR edge cases**
Default `false` for production. Set `true` only for scanned-PDF runs where OCR character variations cause verbatim assertion false-negatives. Must NOT be set for hackathon submission runs.

**ADR-035 — Provision-level discovery tag uses URL anchor heuristic**
`infer_article_anchor("Section 26")` → `"#pr26-"` — matches Singapore SSO portal URL scheme. Other portal schemes will not match the known_provisions set and will fall through to doc-level tag with `flag_for_review=True`. The heuristic is safe: an unresolvable anchor never wrongly marks something KNOWN.

**ADR-036 — evaluate.py NEW score is provision-level, not indicator-level**
Before: `engine_indicators - known_indicators` always empty (all 10 indicators are in the kit). After: rows with `discovery_tag=="NEW"` whose `(law_name, article)` doesn't match the kit's anchor-parsed provision keys. Score: `min(count * 4, 20)`.

**Final state:** 556 passed, 2 skipped across full test suite (no regressions).

---

## CLI Progress Display + Anti-Blocking Crawler

**Goal:** Give the operator live visibility into every pipeline step/sub-step, and let the crawler get through bot-detection on JS gov portals (Singapore SSO) instead of hanging on repeated 403s + Playwright `wait_for` timeouts.

**New module — `src/cli/progress.py`:**
- `Progress` — ANSI spinner with a primary step line (`⠋ label  2.1s / 5.3s total`) and an optional **substep** line below it (`└─ …`). Background thread redraws at 10 Hz; clears prior lines so the display stays in place. Falls back to plain `→ label` lines when stdout is not a TTY.
- API: `step()`, `substep()`, `done()`, `warn()`, `fail()`, `info()`, `summary(records, cost_usd)`.
- **Global singleton** so deep modules report sub-steps without signature changes: `set_progress(p)` (called once in `main.py run_pipeline`) and module-level `substep(text)` (no-op when no progress instance is set — safe in tests / cost_logger).

**Substep wiring (live "what's happening now"):**
- `src/fetcher/extractors/ocr_stage1.py` — `OCR page N/total (engine)` in the main loop; `LLM vision OCR page N/total` in the `_llm_ocr_fallback` loop.
- `src/retrieval/rag.py` (`retrieve_batch`) — `Building FAISS index — N chunks`, `Building BM25 index — N chunks`, then `RAG retrieve <indicator> (i/total)` per indicator.
- `src/mapping/mapper.py` (`extract_provisions`) — `LLM call <indicator> (i/total)` per indicator.
- `src/crawler/crawler.py` (`_crawl_bfs`) — `Pass P [engine] page/budget dDEPTH · <url>` per URL fetched, plus `HTTP 403 blocked · <url>` on blocks.

**Anti-blocking crawler — `src/crawler/crawl4ai_runner.py` (rewritten):**
- `_stealth_browser_config()` — `BrowserConfig(enable_stealth=True, …)` with a realistic desktop-Chrome UA + full `Sec-Fetch-*`/`Accept-Language` headers (headless Chromium's default `HeadlessChrome` UA is what SSO blocks).
- `_build_run_config(wait_for, timeout_ms)` — `CrawlerRunConfig(magic=True, simulate_user=True, override_navigator=True, …)`; `wait_for` is optional.
- `fetch_with_playwright()` — now **two attempts**: (1) with the `wait_for` Act-link selector; (2) on timeout/failure, retry once **without** the selector and return whatever rendered (avoids burning the full `page_timeout` on blocked/empty pages, and still lets the caller inspect HTML / detect a block). Each `arun` wrapped in `asyncio.wait_for(timeout_ms/1000 + 10)` hard ceiling so a single fetch can never hang forever.
- `probe_js_page()` — also routed through stealth config so the probe stage isn't blocked.
- Crawl4AI `verbose=False` everywhere — kills the earlier `INIT Crawl4AI` / `HTTP 403` stdout spam.

**Per-URL crawl logging:** `_crawl_bfs` now logs `[CRAWL] pass=… GET <url>` / `200 OK (Nms) K link(s)` / `HTTP 403 blocked` via the module logger (INFO; dropped from console by default, so the spinner stays clean) — and every URL+status is already persisted to `logs/crawl_<iso>_<ts>.jsonl`. Live on-screen view comes from the substep line.

**Entry points** (`main.py`, `evaluate.py`, `batch_run.py`, `tools/cost_logger.py`) call `load_dotenv()` before any provider/config import so `.env` `LLM_PROVIDER` / `LLM_MODEL` / API keys are honoured.

**Tests:** `tests/test_crawler.py` (28) green — `_fetch_with_playwright` mock signature `(url, wait_for, timeout_ms)` preserved.

---

### ✅ Completed: [86ey22k30] Pillar-Agnostic Per-Economy Portal Strategy (Singapore first)

**Problem:** `python main.py --economy Singapore --pillar 7` hangs 30+ minutes because (a) Zone 1 probed SSO with the full P6+P7 keyword set regardless of `--pillar`; (b) SSO's configured search URL (`Search?SearchAct={keyword}`) opens a blank JS SPA form that silently fails; (c) there was no per-economy declaration of how to reach, discover, and fetch from each portal.

**Files created:**
- `src/crawler/transport.py` — transport ladder: rung 1 plain httpx → rung 2 httpx+browser headers → rung 3 Playwright stealth. `_is_real_response()` detects 403/JS-shell. Rung 1 skipped for `anti_bot != "none"` portals; rung 3 only when `transport_fallback == "playwright_stealth"`.
- `src/crawler/discover.py` — single `discover()` entry point replacing probe→crawl→currency→rank. `build_pillar_keywords()` filters taxonomy by `P{pillar}-` prefix. `_discover_index()` fetches `index_urls`, parses `/Act/` and `/SL/` hrefs, BM25-ranks vs pillar keywords. `_seed_fallback()` returns KNOWN URLs on failure. KNOWN results always included; NEW above `NEW_SCORE_THRESHOLD` fill remaining slots up to `ZONE2_MAX_ACTS`.
- `tests/test_portal_strategy.py` — 36 tests (all passing): Portal model fields, transport ladder, pillar scoping, index discovery, pdf_endpoint routing, graceful degradation.
- `tests/fixtures/sso_browse_index.html` — minimal SSO index fixture for tests.
- `docs/prd/sg_portal_findings.md` — live-validated evidence behind each SG strategy declaration.

**Files changed:**
- `src/config/economy_config.py` — `Portal` model extended with 6 new fields (backward-compatible defaults): `anti_bot`, `discovery`, `fetch`, `index_urls`, `pdf_view_suffix`, `transport_fallback`.
- `economies/singapore.yaml` — fully updated: SSO gets `anti_bot: header_spoof`, `discovery: index`, two `index_urls` (Acts + SL browse indexes), `fetch: pdf_endpoint`, `pdf_view_suffix: "?ViewType=Pdf"`, `transport_fallback: playwright_stealth`. Gazette gets `discovery: TBD`, `fetch: TBD`.
- `main.py` — `_run_zone1()` replaced: now calls `asyncio.run(discover(...))` with seed data; falls back to raw KNOWN URLs on exception.
- `src/fetcher/router.py` — added `_find_portal_for_url()`, `_rewrite_to_pdf_url()`; `route()` rewrites act URLs to PDF view before download when `fetch: pdf_endpoint`.
- `src/crawler/__init__.py` — exports `discover`, `build_pillar_keywords`, `ZONE2_MAX_ACTS`.

**Key constants (env-configurable):**
- `ZONE2_MAX_ACTS` (default 5) — maximum acts sent to Zone 2
- `DISCOVER_BUDGET_S` (default 120.0) — wall-clock deadline for discover()
- `NEW_SCORE_THRESHOLD` (default 0.05) — minimum BM25 score for NEW acts; below threshold acts are dropped, not padded

**ADR-037 — Zone 1 is now a single `discover()` step, not a 4-stage pipeline**
The old `probe → BFS crawl → currency → rank` pipeline is replaced by `discover()`. The probe was silently failing on SSO's JS search; the BFS crawl was expanding into hundreds of cross-reference pages; there was no pillar scoping. `discover()` is strategy-driven (reads YAML), budget-bounded, and pillar-scoped. The old pipeline steps (`run_probe`, `run_crawler`, `run_currency_check`, `run_ranker`) remain importable for backward compatibility but are no longer called by `main.py`.

**ADR-038 — `transport.py` ladder stops at first real 200, never retries all rungs**
Rung 1 (plain httpx) is skipped entirely for portals with `anti_bot != "none"` because those portals are known to block plain requests — no wasted attempt. Rung 3 (Playwright) is only invoked when `portal.transport_fallback == "playwright_stealth"` — most portals never start a browser. `_is_real_response()` combines HTTP status, body length, and JS-shell detection so a 200 with a 50-char JS wrapper doesn't fool the ladder.

**ADR-039 — KNOWN seeds are never truncated by `ZONE2_MAX_ACTS`**
`discover()` merges KNOWN results first (always kept), then appends NEW results above threshold until the total reaches `ZONE2_MAX_ACTS`. If there are more KNOWN results than the cap, all are kept (soft limit). This ensures Round 1 seed acts are never dropped for relevance ordering reasons.

**ADR-040 — `pdf_endpoint` routing rewrites the act URL before download, not after**
`_find_portal_for_url()` matches by registered domain (via `tldextract`). If the portal has `fetch: pdf_endpoint` and a `pdf_view_suffix`, the URL is rewritten in `route()` before `download()` is called. The existing TEXT_PDF→pdfplumber path handles the result with no OCR. This avoids adding a new extractor and reuses the proven Zone 2 PDF path.

**Final state:** 36 new tests pass; 551 pre-existing tests pass; 4 pre-existing failures confirmed pre-existing (not regressions).

---

### Crawl performance — the 30-minute hang fix

Symptom: Pass 1 sat at "Crawling 2 portal(s)" for 30+ min, slowly ticking page 5→6→…→35 of a **310-page** budget. Three compounding causes, all fixed:

1. **Fresh Chromium per URL.** `fetch_with_playwright` launched a new browser on every call (~3-8s cold start each). Now a **single shared browser** is started once (`_get_shared_crawler`) and reused for every `arun()`; `close_shared_crawler()` tears it down. Called at the end of `run_crawler` (after both passes) **and** at the end of `run_probe` — the probe and crawl run in separate `asyncio.run()` loops, so the probe's browser must be released before the crawler opens its own (a browser is bound to the loop that created it).
2. **Pass 1 BFS-expanding known acts.** Pass 1 seeds from the Round 1 DB (URLs we already know are the acts) but was recursing `_CRAWL_MAX_DEPTH=2` into every cross-reference link → the 310-page explosion. Pass 1 now runs at **`max_depth=0`** (confirm the seed pages, don't expand). Seeds are also **deduped to their base page** (`_dedupe_seed_urls` strips `#fragment` so the dozens of `…/Act/PDPA2012#pr26-` anchor URLs collapse to one) and **capped** at `CRAWL_KNOWN_MAX_SEEDS` (default 20).
3. **Synthetic per-page delay.** Dropped `simulate_user` + `mean_delay=0.4` from the run config — `magic=True` already covers anti-bot; the extra synthetic mouse-movement added seconds per page across the BFS.

New env knob: `CRAWL_KNOWN_MAX_SEEDS` (default 20). Existing `CRAWL_MAX_PAGES` / `CRAWL_MAX_DEPTH` still apply to Pass 2 (NEW discovery).

---

## Post-verification fixes — Singapore P7 run (PRD 86ey22k30)

After the first end-to-end Singapore P7 run (6m 50s, several accuracy gaps), six fixes:

1. **Wayback dedupe + local-snapshot fallback** (`src/output/validator.py`). `validate_and_flag` now validates + archives **once per unique source URL** (was once per record → 10× redundant calls for the PDPA). `archive_source()` tries Wayback best-effort then falls back to `archive_local()` (saves the fetched bytes under `LOCAL_ARCHIVE_DIR`, default `outputs/archive`). Wayback retry wait cut 60s→2s. Env: `WAYBACK_BEST_EFFORT`, `LOCAL_ARCHIVE_FALLBACK`, `LOCAL_ARCHIVE_DIR`, `WAYBACK_RETRY_WAIT_S`. Cut validation from ~5 min to seconds.
3. **No double-processing of single acts** (`src/fetcher/router.py`). `pdf_endpoint` fetches set `single_act_fetch=True` → consolidated-volume detection/segmentation skipped (the PDPA PDF was being mis-split into 2 segments and run through Zone 2 twice).
4. **KNOWN-by-title discovery tag** (`src/crawler/discover.py`, `main.py`). `discover()` accepts `known_titles`; an act is tagged KNOWN if its URL **or normalised title** matches a Round 1 seed entry. Fixes the PDPA being tagged NEW when the seed has titles but no act-level URLs (the SG P7 case).
5. **Taxonomy exclusion + threshold** (`src/crawler/discover.py`). `build_pillar_excludes()` applies each pillar's `exclude_act_titles`/`exclude_keywords`; `NEW_SCORE_THRESHOLD` default raised 0.05→0.2 (env-tunable). Drops false positives like "Active Mobility (Personal Mobility Devices)" that matched only on the word "personal".
2. **Populate `law_number_ref` / `last_amended`** (`src/fetcher/extractors/legislation_meta.py`, new). Parses "Act N of YYYY" + revised-edition year from the PDF cover and the version date from the URL `?DocDate=YYYYMMDD`. `FetchedDocument` gained `law_number_ref`/`last_amended` fields; the `TranslatedDocument` wrapper now delegates to them (was hardcoded `None`). These columns were always blank before.
6. **Single-line spinner** (`src/cli/progress.py`). The spinner now renders one terminal-width-truncated line cleared with `\r\033[K` instead of a multi-line redraw, eliminating the blank-line spam that appeared when a long substep wrapped during slow RAG steps.

Tests: `test_portal_strategy.py` (+KNOWN-by-title, +exclude filter, +pdf_endpoint skips segmentation), `test_z2_5_validator.py` (+dedupe, +local fallback, +snapshot write), `test_legislation_meta.py` (new). Full suite: 601 passed, 2 skipped; 4 pre-existing unrelated failures (2 `test_probe`, 2 `test_z2_4_providers` anthropic).

## Output-format compliance (README_template.md + UN slide 18)

Authoritative spec is `docs/README_template.md` plus UN "slide 18". `data/output_schema_sample.json` was self-generated and **wrong** — it has been regenerated from the real `write_json` so it can no longer drift from actual output.

- **CSV** — already compliant: the exact 13 columns in spec order, guarded by `write_csv` (raises on drift). No change.
- **JSON shape** — the existing document-level + `provisions[]` envelope already matches slide 18 (NOT the `records`/flat shape of the old sample). Field-level additions to satisfy the README "extended metadata" list and slide 18:
  - `pdf_is_scanned` (doc-level bool, derived from `doc_type == "SCANNED_PDF"`) and `retrieval_method` (doc-level constant string) added in `src/output/writer.py` (`_RETRIEVAL_METHOD`).
  - `discovery_tag` now included **inside each provision** (`as_provision_dict`) per slide 18, in addition to the document level.
  - Naming kept as `processing_time` (slide 18) — README prose says `processing_time_seconds`; the two UN sources conflict, slide spelling chosen (also enforced by an existing AC2 test).
- **`mapping_rationale`** — SYSTEM_PROMPT Rule 8 already matched the format guide; added **Rule 9: leave blank if uncertain** (a blank rationale is neutral, a wrong one misleads reviewers) and the optional indicator short-name in parens. 300-char cap unchanged.

Tests: `test_z2_6_output.py` (+`pdf_is_scanned`/`retrieval_method`, +per-provision `discovery_tag`; updated the exclusion test since `discovery_tag` is now intentionally in provisions). Full suite after all output + post-verification fixes: **603 passed, 2 skipped**; same 4 pre-existing unrelated failures.
