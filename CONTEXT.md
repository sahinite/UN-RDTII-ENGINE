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
| **7-tier LLM cascade** | Anthropic → OpenAI → DeepSeek → Groq → Qwen → Ollama (qwen2.5:7b) → Ollama (granite3-8b). Pinned per run via `LLM_PROVIDER`. Llama 3.3 excluded (non-Apache 2.0). |
| **CER** | Character Error Rate — OCR quality metric. Stage-2 OCR triggers at CER ≥ 5%. |
| **RAG pipeline** | Hybrid BM25 + dense retrieval with cross-encoder reranking; top-5 chunks per indicator, each with `location_reference`. |
| **location_reference** | `(act_name, part, article_number)` tuple for verifiable citations. |
| **SeedData** | `known_urls`, `known_titles`, `known_provisions` (anchored URLs), `known_sections` (act title → prose section tokens) — loaded from Round 1 DB xlsx + Sample CSV. Compound titles split on `;`/newline. |

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

### ADR-041 — Unified portal strategy = stable interface + fixed adapter set
Onboarding a new economy is config-only against already-implemented adapters; a "single literal strategy for all 11" is rejected as infeasible (SG=HTML, AU=JSON API, TH/KH/LA/MM=scanned OCR). New code only for a genuinely-new portal *category*. Spec: `docs/prd/unified-portal-strategy.md`. *(status: steps 1–7 complete — SG golden baseline, `.docx` branch, SPA/SSR probe, shared rank/tag tail, AU `api`+`api_versioned_pdf` live, `auto` best-effort adapter + `discovery` default→`auto`, AU validated end-to-end. Open: mapping-stage quality items from the AU run, tracked separately.)*

### ADR-042 — Discovery adapter emits raw `(title, url)` only; rank/tag is shared
`index`/`api`/`auto`/`seed_only` each only *list candidates*. BM25 rank, taxonomy exclude, KNOWN/NEW tag, indicator-aware cap are lifted out of `_discover_index` into the shared `discover()` tail so adapters cannot drift. NEW discovery is always-on; seeds are the KNOWN-tag reference + floor, not the discovery source.

### ADR-043 — Fetch adapter resolves a document URL; extraction stays universal
`pdf_endpoint`/`api_versioned_pdf`/`html*`/`pdf_link`/`auto` only produce `(title,url,portal) → document_url(s)`. The `router.py` extractor (sniff → PDF/OCR/HTML + segment + translate) is untouched. "Text only inside rendered HTML" is the existing `extract_html` branch, not a new seam.

### ADR-044 — Extractor branch set closed at five; `.docx` added now
{text-PDF, scanned-PDF→OCR, static-HTML, JS-HTML, `.docx`/`.doc`}. `.docx` built proactively (AU offers Word) as an additive `detect_type` case — cannot regress the existing four.

### ADR-045 — One shared SPA-vs-SSR probe feeds discovery and fetch
The only bit `detect_type` can't sniff is static-HTML vs JS-SPA (both return `200 text/html` — the AU `/latest/` trap). A single probe utility resolves it for both the `auto` crawler and the static-vs-JS fetch branch. Built once, shared.

### ADR-046 — `auto` is a best-effort safety net, not a reliable universal crawler
No declared strategy → `auto` probes SPA/SSR (shared spa_probe), renders SPAs with Playwright, and best-effort extracts candidate links, labelling the run "declare a strategy for production quality." Production quality comes from a ~30-min declared strategy (as for AU), not from perfecting a universal crawler. No scaffold/suggestion CLI is built. The `Portal.discovery` **default is `auto`** so an undeclared portal auto-crawls; explicit `TBD` deliberately skips. `fetch:auto` renders SPA document shells before extraction.

### ADR-047 — Config stays a strict typed menu; new adapters extend the Literals
New adapter names are one-word additions to the `discovery`/`fetch` `Literal`s; `extra="forbid"` (ADR-003) stays. No free-form params box — the strict menu catches typos and protects validated economies from silent breakage.

### ADR-048 — Validated economies protected by golden-output snapshots
"Don't change architecture" = freeze the pattern + freeze validated-economy output, not "move no code." Before editing any shared hot path, capture a golden snapshot of each validated economy (SG first); the refactor's acceptance test is "snapshots byte-identical" — automatic re-validation instead of manual re-testing.

### ADR-058 — FRL versioned-PDF resolver walks compilations until a PDF exists
`api_versioned_pdf` built one dated URL from the LATEST compilation date and 404'd whenever
FRL hadn't generated that compilation's `text/original/pdf` yet (Telecom I&A C2004A02124,
C2004A05145 — the newest ~4 compilations 404, older ones serve a real PDF). `_resolve_versioned_pdf_url`
now walks `_inforce_version_dates` (registered compilations, newest first) and returns the
first date whose URL passes `_url_serves_pdf` (streams the first chunk, checks `%PDF` magic —
never downloads a full multi-MB file to probe). Recovered Telecom I&A (→2025-04-04), ASIO,
DATA into AU P7 (act_missing 17→13); acts with no PDF at any compilation (C2004A05145)
correctly return None. The recovered acts' Round 1 sections then surface as `provision_missing`
in the recall audit — i.e. fetch is fixed, the remaining gap is LLM extraction (issue A).

### ADR-059 — Diagnostics: per-economy-pillar mis-map + KNOWN-recall audit
Both root-cause logs now write to `logs/diagnostics/{ISO}_P{pillar}_{mismaps,recall}.json`
(per-run, no clobber). `main._audit_known_recall` is the under-recall counterpart to the
over-fire mis-map log: for every Round 1 (act, section, indicator) it records found vs missing,
classifying misses as `act_missing` (fetch/discovery) or `provision_missing` (act present but
section not extracted → LLM-reject/retrieval-miss). Evidence across SG+AU × P6+P7 (2026-07-04):
over-fire=2 (rare, auto-pruned), real under-recall≈2–3, but `act_missing`=25 dominates — so the
biggest recall lever was fetch (ADR-058), not the LLM. Parks issue A with data.

### ADR-057 — Citation-label guards (C-safe)
Compilation-PDF chunking leaks non-section values into the citation. Two deterministic
guards: (1) `parser` drops a chunk-derived `Art. N` from `location_reference` when N is a
4-digit YEAR (1800–2099, e.g. "Art. 2020") or equals the page number ("Art. 371 | Page 371")
— the LLM `article` field stays authoritative; (2) `html_extractor` now mirrors `pdf_text`'s
`derive_act_title(full_text, url)` fallback so URL-only seeds (e.g. AU `/details/` pages that
fall back to HTML) no longer emit an empty `law_name` → schema-violation → dropped row. The
uglier `article`-field garbles ("Section 8.1 and 8.2", Act-title leakage) are LLM output,
deferred to the drift/prompt work (ADR-054/known-wrong-indicator-rootcause).

### ADR-056 — Canonical act-identity key in discovery
Discovery deduped and capped by `_normalise_url`, so variant URLs of ONE act each burned a
slot: legislation.gov.au `/details/c…`, `/c…/latest/text` and bare `/C…` are the same act.
AU P7 had 14 seed URLs → only 12 distinct acts, but the two `/latest/text` duplicates pushed
real acts (ASIO C2004A02123, DATA C2022A00011) past the `ZONE2_MAX_KNOWN_ACTS=12` cap.
`_canonical_act_key` keys legislation.gov.au URLs by registration id (C/F-number) and
everything else by `_normalise_url` (SG/SSO unchanged — `/Act/CA2018` ≠ `/acts-supp/9-2018`,
so SG's consolidated-vs-supplement duplicate is still handled at output by ADR-053). Result:
all 5 Round-1 AU P7 acts now reach fetch; DATA Act appears in output. Remaining AU absences
(ASIO, Telecom I&A) are the separate `api_versioned_pdf` 404/`/details/` resolver bug.

### ADR-055 — KNOWN cross-indicator prune (ground-truth) + mis-map logging
The same provision can legitimately serve multiple indicators (Round 1 files My Health s.77
under BOTH 6.1 and 6.2), so blind "one provision → one indicator" dedup would delete valid
findings. `main._prune_known_cross_indicator` uses `known_sections_by_indicator` as ground
truth: for a **KNOWN** provision it keeps exactly the indicator(s) Round 1 assigns and drops
out-of-set copies — but only when a correct-indicator copy survives (never loses a known
provision outright; sole wrong-indicator copies are kept). **NEW** provisions are untouched
(a NEW may also serve 2 indicators and there is no ground truth to prune it — dropping one
risks a real 20-pt finding). Every out-of-set KNOWN row is a *confirmed* mis-map, logged to
`logs/known_mismaps.json` with the source chunk's retrieval signal (`source_rerank_score` +
`source_retrieval_method`, threaded ExtractionResult→OutputRecord). Runs after cross-doc
dedup, before null assessments. Early evidence (SG s.26, AU s.77): mis-maps have HIGH
confidence but strongly NEGATIVE rerank scores → **LLM over-fire**, not retrieval over-match.
Root-cause fix is deferred (see memory known-wrong-indicator-rootcause).

### ADR-054 — Seed-guided retrieval (gentle) + indicator drift is an LLM-extraction limit
`seed_loader` builds `known_sections_by_indicator` (`P7-I3 → {act → sections}`, DB `7.3`→`P7-I3` via `_db_indicator_to_engine`). `retrieve_batch` uses it: per indicator, `_inject_seed_sections` locates the Round 1 section chunk for THIS act (`_find_section_chunk` — exact `article_number`, else the `N.` heading in text scored by body length so the operative provision beats the Contents/TOC listing; handles empty article_number on large acts like Employment s.95 / PDPA s.25) and, **only if entirely absent**, prepends it. **Gentle by decision:** an earlier promote-to-front variant that reordered already-retrieved sections displaced other chunks from the LLM token budget and drove total records *down* (24→19) without changing the LLM's verdict, so present sections are now left untouched. Investigation showed the "drift" is mostly an **LLM extraction** issue, not retrieval: the target sections ARE retrieved, but the LLM declines them — correctly for PDPA s.25 (a retention *limitation*, not a *minimum*; Round 1 scored it 0), over-strictly for Employment s.95 ("keep for the *prescribed* period" → delegated). True recovery of the Employment-style cases needs I3 prompt tuning (tracked as future work), not retrieval. KNOWN-only, additive, never removes NEW discoveries.

### ADR-053 — Cross-document provision dedup at output
The mapper's `_deduplicate` only folds duplicates WITHIN one document. The same act can be fetched under two URLs — consolidated `/Act/CA2018` (from SSO index title-match) and as-enacted `/acts-supp/9-2018` (from a Round 1 seed ref) — producing identical provisions in separate documents. `main._dedup_cross_document` folds `all_records` on `(law_name, indicator_id, article, snippet[:80])` (whitespace/case-folded), keeping the higher-quality copy: consolidated `/Act/` source > higher confidence > concrete (non-"unknown") location. Runs before null assessments + the PDPA gate. Root-cause act-identity dedup at discovery (mapping `9-2018`→`CA2018`) is deferred — output dedup fixes correctness and catches any cross-doc dupe.

### ADR-052 — Zone-2 SPA render uses an isolated per-call crawler, not the shared one
`_render_spa_sync` wraps each page in its own `asyncio.run()` (a fresh event loop per call). The module-level `_shared_crawler` is bound to whichever loop first `start()`ed it, so the *second* Zone-2 render reused a Playwright browser whose transport lived on the first, now-closed loop — every op then hung until the 2×(timeout+10) hard ceiling (~80s), failing exactly one PDPC page per run (the pattern: first render OK, next times out). `crawl4ai_runner.fetch_isolated()` creates and closes a dedicated crawler inside the caller's loop; `_render_spa_sync` uses it. The shared singleton stays for the BFS crawler / discovery, which run under a single `asyncio.run`. Verified: three PDPC pages render back-to-back in ~3s each.

### ADR-051 — `discovery: sitemap` for JS-SPA portals with no crawlable index
A JS SPA (pdpc.gov.sg) returns a content-less shell to httpx, so `index` (needs HTML anchor links) and `auto` (its `classify_render` probe mis-reads the shell as SSR) both fail to enumerate pages. Such portals commonly publish a standard `sitemap.xml` (advertised in robots.txt). `_discover_sitemap` fetches it directly (own httpx call — the transport ladder's `_is_real_response` rejects bodyless XML), extracts every `<loc>` page URL, derives a BM25 title from the URL slug, and hands `(title, url)` candidates to the shared `_rank_exclude_tag` tail like any other adapter. Nested sitemap-index `.xml` locs are skipped (flat sitemaps are the gov norm). With `ZONE2_MAX_NEW_ACTS=0` (build-gate default) the sitemap adds nothing — its candidates are all NEW and dropped, and known pages still arrive via seed injection; its value is realized when NEW discovery is enabled. Offline golden harness mocks `_fetch_sitemap_xml` to a pinned `tests/fixtures/pdpc_sitemap.xml`.

### ADR-050 — `fetch: html_js` renders JS-only portals unconditionally
`fetch: auto` gates its Playwright render on `classify_render(...).is_spa`. That probe measures `body.get_text()` **without** stripping nav/footer chrome, so a content-less SPA shell wrapped in a large menu (pdpc.gov.sg: 42–95 chars of real content but a big nav) reads as SSR and the render is skipped — then `extract_html` (which DOES strip chrome) sees <200 chars and raises "JS-rendered". Rather than retune the shared heuristic (risk to validated AU/SG paths), the declared-but-unimplemented `html_js` Literal is now wired in `router.py` to render every HTML page via Playwright with no probe gate. Portals whose pages always require JS declare `fetch: html_js`; PDFs on the same domain are unaffected (they take the PDF branch). Verified end-to-end on the two PDPC pages that failed the 2026-07-02 SG P7 run (95 → 4250 chars).

### ADR-049 — Provision KNOWN matching uses Round 1 prose sections, not just anchor URLs
Round 1 identifies most known provisions by prose section number in the act/comment columns ("Section 199", "Section 11(3)"), NOT by `#`-anchored URLs. Anchor-only matching (`known_provisions`) therefore left Pillar 7 with an empty match set and tagged every provision NEW — false-NEWs that graders reclassify. `seed_loader` now also builds `known_sections` (act title → section tokens), and `resolve_provision_tag()` tags KNOWN when `(normalised law_name, section)` is present, **indicator-agnostic** (KNOWN = the provision is in Round 1 and the run found it, regardless of which indicator surfaced it). Anchor-URL matching is retained as a second KNOWN path.

---

## Implementation State

All stories Z1-1 through Z2-6 are complete. Below is the current module-level summary.

### Zone 1 — Evidence Discovery

| Module | Purpose |
|--------|---------|
| `src/config/economy_config.py` | `EconomyConfig` + `Portal` Pydantic models; `load_economy(name)` with fuzzy matching; `load_economy_by_iso()`; `iso_code`/`un_name` fields |
| `src/crawler/discover.py` | **Active Zone 1 entry point.** `discover()` routes per-portal strategy (`index`/`api`/`sitemap`/`auto`/`seed_only`/`TBD`). `build_pillar_keywords()` filters by `P{pillar}-`. `build_pillar_excludes()` gathers exclude lists. `_discover_index()` fetches browse indexes; `_discover_sitemap()` fetches `sitemap.xml` for JS-SPA portals (slug→title, skips nested `.xml`); both BM25-rank + exclude + tag via the shared `_rank_exclude_tag` tail. |
| `src/crawler/transport.py` | Transport ladder: plain httpx → httpx+browser headers → Playwright stealth. `_is_real_response()` detects 403/JS-shell. |
| `src/crawler/seed_loader.py` | `SeedData` with `known_urls`, `known_titles`, `known_provisions`, `known_sections`, `known_sections_by_indicator`. Splits compound titles on `;`/newline. `_extract_section_tokens()` harvests prose section numbers from the act/comment/coverage columns; `_db_indicator_to_engine()` maps `7.3`→`P7-I3`. Generic `_pillar_matches()` for any Pn. |
| `src/crawler/crawl4ai_runner.py` | Stealth Crawl4AI/Playwright. Shared browser singleton. Two-attempt fetch (with/without selector). |
| `src/crawler/probe.py` | Legacy probe — `load_taxonomy()` / `validate_taxonomy()` still used at startup. |
| `src/crawler/crawler.py` | Legacy BFS crawler — shared `_normalise_url()` imported by other modules. |
| `src/crawler/currency.py` | Legacy currency check. |
| `src/crawler/ranker.py` | Legacy ranker with LLM gate. |

### Zone 2 — Intelligent Mapping

| Module | Purpose |
|--------|---------|
| `src/fetcher/router.py` | Zone 2 entry: `route()` dispatches TEXT_PDF/SCANNED_PDF/HTML. `pdf_endpoint` URL rewrite. `single_act_fetch` skips volume detection. `fetch: auto` renders SPA shells only when `classify_render.is_spa`; `fetch: html_js` renders **every** HTML page via Playwright unconditionally (for portals whose SPA shell fools `classify_render`, e.g. pdpc.gov.sg). |
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
| `src/mapping/provision_tag.py` | `resolve_provision_tag()` (KNOWN/NEW per provision — matches on anchor URL OR `(act title, section)` against `known_sections`, indicator-agnostic), `infer_article_anchor()` / `infer_section_token()` heuristics. |
| `src/mapping/providers/` | `AnthropicProvider`, `OpenAIProvider`, `DeepSeekProvider` (deepseek-chat, DEEPSEEK_API_KEY), `GroqProvider` (qwen/qwen3-32b + qwen/qwen3.6-27b fallback), `QwenProvider` (qwen-plus via DashScope, DASHSCOPE_API_KEY), `OllamaProvider` (qwen2.5:7b P6, granite3-8b P7). |
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
| Singapore | SG | SSO: `index` + `pdf_endpoint` + `header_spoof`; PDPC: `sitemap` discovery + `html_js` fetch (JS SPA regulator guidance, discovered via sitemap.xml); Gazette: `TBD` | Reference / Phase 1 gate |
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

### Retrieval recall — text quality + tuning

- **pdfplumber `x_tolerance=1.5`** (`pdf_text.py`, env `PDF_X_TOLERANCE`). The default `x_tolerance=3` merged words on tightly-kerned gov PDFs (SSO) — ~339 run-together blobs per act (`"responsibleforensuring…"`) that corrupted chunk embeddings and tanked retrieval recall. 1.5 → 0 blobs. **Highest-leverage accuracy fix** — helps every indicator/economy and the verbatim output.
- **RAG hyper-params env-overridable** (`retrieval/config.py`): `BM25_TOP_K`/`DENSE_TOP_K`=30, `FUSION_TOP_K`=30, `RERANK_TOP_N`=12. Raise `RERANK_TOP_N` toward 20 for more recall at higher LLM token cost.
- **Taxonomy keywords/in_scope** for P7-I4 (DPO) and P7-I5 (gov access) rewritten to match real statutory wording (e.g. PDPA "designate an individual responsible for compliance" = DPO).
- **AU compilation chunking** (`chunker.py`). legislation.gov.au compilation PDFs use `6A Heading` section titles (space, no period) and repeat running page-headers (`Part I Preliminary`, `Section 6A`) + footers (`Privacy Act 1988 3`) on every page. pdfplumber's hierarchy parses those running headers as Part/Division entries, so the hierarchy path dropped ~97% of a 472-page act (Privacy Act 1988 collapsed to 34 chunks, cover/TOC only → the LLM "cited" the act title). Three guards: (1) a **coverage guard** rejects a hierarchy chunk set that retains <`CHUNK_HIERARCHY_MIN_COVERAGE` (0.5) of the text and falls back to raw-text regex splitting; (2) the article-boundary regex now matches the space-separated `6A`/`26WK` heading form; (3) `_strip_page_furniture()` removes TOC leader lines, running `Section N` headers, and `<page> <Act Title>` footers on the regex path. Result: Privacy Act → 876 chunks, 91.9% coverage, real APP/NDB sections retrieved.

**Known limitation (documented):** specific provisions buried deep in large/topically-diverse acts are not reliably extracted — e.g. PDPA DPO clause ranks ~18th of 54 chunks; CPC s.39-40 access powers sit in a 1M-char code. Even when forced into LLM context, `trim_chunks_to_budget` + LLM conservatism can drop them. Reliable fix needs token-budget tuning / section-aware retrieval — tracked as future work, not a single bug.
