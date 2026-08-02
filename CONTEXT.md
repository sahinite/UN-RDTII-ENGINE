# CONTEXT.md

Domain glossary, architectural decisions, and implementation state for the RDTII Extraction Engine.
Read before touching any module; update as work lands (remove deprecated details).

---

## Domain Glossary

| Term | Definition |
|------|-----------|
| **Economy** | One Asia-Pacific jurisdiction, described by a YAML file in `economies/`. |
| **EconomyConfig** | Pydantic single source of truth: `economy_name`, `iso_code`, `un_name`, `script_type`, `languages`, `portals`, overrides. `extra='forbid'`. |
| **Portal** | A government portal in an economy YAML; declares strategy fields (`anti_bot`, `discovery`, `fetch`, `index_urls`, `pdf_view_suffix`, `transport_fallback`, …). |
| **script_type** | `latin`→tesseract / `asian`→paddleocr (derived `@property`; `ocr_engine_override` forces one). |
| **be_year_conversion** | Buddhist-Era→Gregorian flag (Thailand/Cambodia). |
| **KNOWN / NEW** | Discovery tags: KNOWN = act/provision in the Round 1 DB; NEW = engine-discovered (top scoring differentiator). |
| **Zone 1 / Zone 2** | Evidence discovery (`discover()`) / intelligent mapping (fetch→OCR→translate→chunk→embed→RAG→map→validate→write). |
| **Pillar** | An RDTII dimension: P6 Cross-border Data, P7 Domestic Data Protection. |
| **Quality gate** | Optional generic economy/pillar output check; `off` in production, `warn` or `fail` for CI/build validation. |
| **8-tier LLM cascade** | Anthropic → OpenAI → Gemini → DeepSeek → Groq → Qwen → Ollama(qwen2.5) → Ollama(granite3). Pinned per run via `LLM_PROVIDER`; Llama 3.3 excluded (license). |
| **CER** | OCR Character Error Rate; Stage-2 OCR triggers at CER ≥ 5%. |
| **RAG** | Hybrid BM25 + dense + cross-encoder rerank; top-5 chunks/indicator, each with `location_reference`. |
| **SeedData** | `known_urls/titles/provisions/sections` + `known_sections_by_indicator`, from Round 1 DB xlsx + Sample CSV. |

---

## Architectural Decisions (condensed)

Each line is the decision + why. Numbers are stable references.

**Config & economy onboarding**
- **ADR-001** `ocr_engine` derived from `script_type` (`_SCRIPT_TO_OCR`), not configured; `ocr_engine_override` wins.
- **ADR-002** Validation pure (`model_validate`); `load_economy()` is thin I/O with fuzzy name match.
- **ADR-003** `extra='forbid'` on all config models — unknown YAML keys raise immediately.
- **ADR-004** Economy YAML filename = full lowercase name (`load_economy("Singapore")`→`singapore.yaml`).
- **ADR-047** Config is a strict typed menu; new adapters extend the `discovery`/`fetch` `Literal`s (no free-form params).
- **ADR-041** Unified portal strategy = stable interface + fixed adapter set; onboarding a new economy is config-only. New code only for a genuinely new portal *category*.

**Zone 1 — discovery**
- **ADR-037** Zone 1 is a single strategy-driven `discover()` (replaced probe→crawl→currency→rank; legacy modules importable but unused).
- **ADR-042** Adapters (`index`/`api`/`sitemap`/`auto`/`seed_only`) only emit raw `(title,url)`; BM25 rank + taxonomy exclude + KNOWN/NEW tag + cap are a shared tail so adapters can't drift.
- **ADR-046** `auto` is a best-effort safety net (SPA/SSR probe → Playwright → best-effort links), not a universal crawler; `Portal.discovery` default = `auto`, explicit `TBD` skips.
- **ADR-051** `discovery: sitemap` for JS-SPA portals with no crawlable index (fetch `sitemap.xml`, slug→title).
- **ADR-038** Transport ladder stops at first real 200; rung 1 skipped for `anti_bot!=none`, rung 3 (Playwright) only when `transport_fallback==playwright_stealth`.
- **ADR-039** KNOWN seeds never truncated by `ZONE2_MAX_ACTS`; NEW fills remaining slots.
- **ADR-056** Canonical act-identity key (legislation.gov.au keyed by registration id) so URL variants of one act don't each burn a discovery slot.
- **ADR-062 (run profile)** `RUN_PROFILE` gate/submit/explore sets NEW-act cap 0/3/8; `submit`(=3) is the intended submission setting; profile printed at startup.

**Zone 2 — fetch / extract / OCR**
- **ADR-043** Fetch adapters resolve a document URL only; the `router.py` extractor stays universal.
- **ADR-044** Extractor branch set closed at five {text-PDF, scanned→OCR, static-HTML, JS-HTML, docx}.
- **ADR-045** One shared SPA-vs-SSR probe feeds both `auto` discovery and the static-vs-JS fetch branch.
- **ADR-040** `pdf_endpoint` rewrites the URL (matched by registered domain) before `download()`.
- **ADR-065/066** Singapore SSO: browser escalation for the anti-bot 202 gate + `html_wholedoc` (`?WholeDoc=1`) render for the operative text; complete Playwright `networkidle` render, chunker boundary fix for `26.—(1)` headings, and longest-body section selection so the operative provision (not the TOC stub) is retrieved.
- **ADR-058** FRL `api_versioned_pdf` resolver walks compilations newest-first until a real `%PDF` exists (recovered AU acts that 404'd on the latest compilation).
- **ADR-050** `fetch: html_js` renders every HTML page via Playwright unconditionally (for SPA shells that fool `classify_render`, e.g. pdpc.gov.sg).
- **ADR-052** Zone-2 SPA render uses an isolated per-call crawler (`fetch_isolated`), not the shared singleton (loop-binding hang fix).
- **ADR-024** Stage-2 OCR (Azure DI → Mistral) is credentials-gated; missing keys silently skip.
- **ADR-014** `FetchedDocument` is the single Zone-2 contract; validation is enforced at each extractor's end, and its deterministic `__post_init__` metadata enrichment applies `law_number_ref`/`last_amended` uniformly to text-PDF, HTML, OCR/image, and DOCX paths. `last_amended` is an evidenced amendment/compilation year, not a generic page "current as at" year.

**Translation & retrieval**
- **ADR-061** Argos is offline-primary translator (Argos→DeepL→Google), run in a **single persistent subprocess** (native runtime segfaults co-resident with torch/faiss; multiple workers thrash).
- **ADR-060** Per-economy retrieval: EN economies (SG/AU) use `all-MiniLM-L6-v2` + English reranker; non-EN (MY) use multilingual models and skip full-doc translation (RAG over original text). Both default to the English model. (MY P7 ~50→~7 min.)
- **ADR-017** `verbatim_original` is always source-language `raw_text` (unmodified; BE conversion only affects text sent to translation).
- **ADR-054** Seed-guided retrieval is *gentle* — injects the Round 1 section chunk only if entirely absent (reordering present chunks dropped records). Residual "drift" is an LLM-extraction limit, not retrieval.

**Mapping, tagging & output**
- **ADR-021** LLM cascade lives in `src/mapping/llm_client.py`.
- **ADR-032** `extract_provisions` catches only `AllProvidersExhaustedError` (rate-limit is an internal cascade signal).
- **ADR-034** `ALLOW_UNVERIFIED_SNIPPETS` default false → verbatim assertion failure = hard discard.
- **ADR-049/063/064** KNOWN = act+provision identity, independent of portal/URL: match on anchor URL **or** `(act title, section)` against `known_sections`; `match_known_act` fuzzy-resolves titles (exact / acronym / distinctive-token) with over-match guards. Runs for every provision (no NEW short-circuit).
- **ADR-068** Seed fixes: match on `(Act NNN)` designation; split multi-act cells only on `;`/blank lines; consistent engine-id indicator keys.
- **ADR-035** Provision tag uses a URL-anchor heuristic (`Section 26`→`#pr26-`); unresolvable → doc-level fallback + `flag_for_review`.
- **ADR-062 (citation/source)** `location_reference` is derived deterministically (`Art. {token} | Page {n}` from the chunk containing the snippet), never LLM-emitted; `secondary`-type portals flag every provision for primary-source verification.
- **ADR-055** KNOWN cross-indicator prune uses `known_sections_by_indicator` as ground truth (keeps only Round 1's indicator(s), never drops a sole copy); out-of-set KNOWN rows logged as confirmed mis-maps.
- **ADR-053** Cross-document provision dedup at output on `(law_name, indicator, article, snippet[:80])`, keeping the higher-quality copy.
- **ADR-036** `evaluate.py` NEW score is provision-level: `min(count*4, 20)`.
- **ADR-026** CSV is UTF-8-BOM (post-write BOM check). **ADR-029** Output filename includes timestamp.
- **ADR-067** Ollama local models: send `think:false` for thinking models and size `num_ctx` (default 8192) to fit the prompt, else silent empty output.

**Archiving (this session)**
- Exact-file archiving: JS-rendered HTML is snapshotted from the in-memory `archive_bytes`/`archive_ext` (a plain re-fetch returns the truncated SSR shell or a false soft_404); static PDFs re-fetched. `validate_and_flag(document_blobs=…)` threads the bytes; `archive_content`/`archive_local` share `_write_snapshot`; the Wayback rate-limit sleep only fires when a live Wayback call was made.

**Process / snapshots**
- **ADR-005** Economies never run in parallel within one process (process-global model state); `batch_run.py` parallelises across isolated `python main.py` subprocesses.
- **ADR-048** Validated economies protected by golden-output snapshots — refactor acceptance test is "snapshots byte-identical".
- **ADR-009** `taxonomy.json` at project root; validated at startup.
- **ADR-059** Per-economy-pillar diagnostics: mis-map + KNOWN-recall audit → `logs/diagnostics/`.

---

## Implementation State

Stories Z1-1 … Z2-6 complete. Module summary:

**Zone 1** — `config/economy_config.py` (`EconomyConfig`/`Portal`, `load_economy`), `crawler/discover.py` (active entry: `index`/`api`/`sitemap`/`auto`/`seed_only`/`TBD` + shared rank/exclude/tag tail), `crawler/transport.py` (ladder + `_is_real_response`), `crawler/seed_loader.py` (`SeedData`, prose-section harvesting, `_db_indicator_to_engine`), `crawler/crawl4ai_runner.py` (stealth singleton), `crawler/{probe,crawler}.py` (legacy: taxonomy loader + shared `_normalise_url`).

**Zone 2** — `fetcher/router.py` (`route()` dispatch; `pdf_endpoint`/`html_wholedoc`/`html_js`/`auto`/`pdf_link`), extractors (`pdf_text`, `ocr_stage1` CER gate, `html_extractor`, `llm_ocr`, `legislation_meta`), `fetcher/segmenter.py` (volume split), `fetcher/translator.py` (3-layer, Argos→DeepL→Google), `fetcher/models.py` (`FetchedDocument` incl. `archive_bytes`/`archive_ext`, `TranslatedDocument`), `ocr/processor.py` (Stage-2 Azure→Mistral), `retrieval/` (chunker, per-economy embedder/reranker, BM25, RRF fusion, `rag.py`), `mapping/` (mapper + optional quality gate, `llm_client.py` cascade, `parser.py` verbatim assertion, `prompts.py` Rules 1–9, `provision_tag.py`), `output/` (writer 13-col CSV + JSON envelope, validator, cost_logger, models), `cli/progress.py`.

**LLM providers** (`src/mapping/providers/`): `AnthropicProvider`, `OpenAIProvider` (gpt-4o default; gpt-5/o-series auto-switch to `max_completion_tokens` + `reasoning_effort`), `GeminiProvider` (gemini-2.5-flash, OpenAI-compatible, `GEMINI_API_KEY`/`GOOGLE_API_KEY`), `DeepSeekProvider`, `GroqProvider` (+ fallback model), `QwenProvider` (DashScope), `OllamaProvider` (qwen2.5:7b / granite3-8b).

**Pipeline** — `main.py` (`run_pipeline`, `_run_zone1`, optional quality gate), `batch_run.py` (`--parallel`), `evaluate.py` (KNOWN 40pts + NEW 4pts/max20), `tools/cost_logger.py`.

### Economy Configs (3 in `economies/`)

| Economy | ISO | Strategy | Status |
|---------|-----|----------|--------|
| Singapore | SG | SSO: `index` + `html_wholedoc` + `header_spoof`; PDPC: `sitemap` + `html_js`; Gazette `TBD` | Reference / Phase-1 gate |
| Australia | AU | legislation.gov.au `api` + `api_versioned_pdf`; OAIC | Ready |
| Malaysia | MY | Multiple portals (website + PDF) | Ready |

Adding an economy = a new YAML only (no Python changes).

### Key Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `LLM_PROVIDER` / `LLM_MODEL` | cascade / provider default | Pin provider / override model |
| `RUN_PROFILE` | gate | gate/submit/explore → NEW-act cap 0/3/8 |
| `ZONE2_MAX_ACTS` | 5 | Max acts to Zone 2 |
| `ALLOW_UNVERIFIED_SNIPPETS` | false | Skip verbatim assertion (OCR edge) |
| `WAYBACK_BEST_EFFORT` / `LOCAL_ARCHIVE_FALLBACK` | true / true | Wayback non-blocking; local snapshot fallback |
| `OLLAMA_NUM_CTX` | 8192 | Local-model context window (must fit the prompt) |
| `QUALITY_GATE_MODE` | off | `off` / `warn` / `fail`; generic economy/pillar output validation |
| `QUALITY_GATE_MIN_CONFIDENCE` | 0.80 | Minimum provision confidence for the quality gate |

### Taxonomy (`taxonomy.json`)

- **P6 Cross-border Data:** 6.1 ban/local-processing, 6.2 local storage, 6.3 infrastructure, 6.4 conditional flow, 6.5 binding data-transfer commitments.
- **P7 Domestic Data Protection:** 7.1 comprehensive framework, 7.2 cybersecurity framework, 7.3 minimum retention, 7.4 DPIA/DPO, 7.5 government access.
- Each indicator: `rdtii_ref`, `category`, `scoring`, `probe_keywords`, `exclude_*`, `in_scope`, `out_of_scope`, `negative_examples`.

### Output Format

- **CSV** — 13 columns (`OUTPUT_TEMPLATE_31MAY.xlsx` order), UTF-8-BOM.
- **JSON** — document envelope + `provisions[]` (per UN slide 18); `mapping_rationale` 300-char cap, blank if uncertain; `processing_time` integer seconds.

### Test Suite

Tests live under `tests/`. Run `pytest` for the full suite or a focused file such as `pytest tests/test_economy_config.py`; keep golden discovery fixtures in `tests/golden/` byte-stable unless intentionally updating expected behavior.

### Known accuracy notes

- **pdfplumber `x_tolerance=1.5`** (`PDF_X_TOLERANCE`) — the highest-leverage retrieval fix (default 3 merged words on kerned gov PDFs).
- **AU compilation chunking** — coverage guard + space-separated `6A` boundary regex + `_strip_page_furniture()` recover legislation.gov.au compilations (Privacy Act 1988: 34→876 chunks, 91.9% coverage).
- **Limitation:** provisions buried deep in large/diverse acts aren't reliably extracted (rank low, or LLM declines even when in-context); needs section-aware retrieval / token-budget tuning — future work. See [[known-provision-matching-gap]], [[indicator-drift]].
