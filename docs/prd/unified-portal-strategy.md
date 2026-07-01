# PRD — Unified Portal Strategy (config-only economy onboarding)

**Status:** Design agreed (grill session 2026-07-01). Not yet implemented.
**Extends:** [crawl-strategy-pillar-agnostic.md](crawl-strategy-pillar-agnostic.md) (SG-first PRD).
**Findings that motivated it:** [sg_portal_findings.md](sg_portal_findings.md), [au_portal_findings.md](au_portal_findings.md).

---

## Problem

Singapore was validated with `discovery: index` + `fetch: pdf_endpoint` (HTML scrape + BM25). Australia then turned out to be a completely different shape (OData JSON API + version-dated PDF URLs). Left unmanaged, every new economy (MY, TH, VN, PH, KH, MM, LA, BN, ID) risks a fresh mini-architecture. We want **onboarding a new economy to be config-only** — never a re-architecture — and to **never regress already-validated economies**.

## Goal (acceptance criterion)

**Config-only onboarding against a fixed set of adapters behind a stable interface.** A "single literal strategy for all 11" is a mirage (SG = HTML, AU = JSON API, TH/KH/LA/MM = scanned PDFs needing OCR). The achievable, honest win: **the interface never changes and we never re-architect; adding an economy = filling YAML fields that route to already-implemented adapters.** The success metric is *"can economy N+1 be onboarded without writing new code?"* — new code is written only the first time a genuinely-new *category* of portal appears (we estimate ~4–5 categories total).

---

## Agreed decisions (grill session 2026-07-01)

### D1 — Target is a stable interface + fixed adapter set (not one literal strategy)
Onboarding is config-only for any portal that reuses an existing adapter. Code is touched only for a truly-new portal *category*.

### D2 — NEW discovery is always-on and mandatory
Seeds (Round 1/Round 2 DB) do **not** contain all acts. Every run must actively pull candidates from the live portal, rank against the pillar, and tag each **KNOWN** (matches a seed) or **NEW**. Seeds are the *reference set for KNOWN-tagging* and a guaranteed floor — **not** the discovery source. (`_MAX_NEW_ACTS=0` is a build-gate throttle, not the target design.)

### D3 — Discovery seam: adapter emits raw `(title, url)` candidates only
An adapter's *only* job: given `(portal, pillar_keywords)`, emit a flat `list[(title, url)]`. The shared orchestrator (`discover()`) does the universal tail **once** for all economies: BM25 rank → taxonomy exclude → KNOWN/NEW tag → indicator-aware cap. Rank/exclude/tag are lifted **out** of `_discover_index` so `index`/`api`/`auto`/future adapters all reuse them and cannot drift.
- Adapters: `index` (HTML browse scrape), `api` (OData/JSON query), `auto` (best-effort probe — see D7), `seed_only` (degradation floor).

### D4 — Fetch seam: adapter resolves a document URL; extraction is universal and untouched
A fetch adapter's *only* job: `(title, url, portal) → final_document_url(s)`. The universal extractor (`router.py` `download` + `detect_type` + `extract_text_pdf`/`extract_ocr_stage1`/`extract_html` + `segment_volume` + OCR cascade + translation) takes any URL → text + metadata. Adapters never reach into extraction.
- `pdf_endpoint` (SG: suffix rewrite), `api_versioned_pdf` (AU: resolve latest-version date → dated URL), `html`/`html_wholedoc`/`html_js` (page URL is the document), `pdf_link`, `auto`.
- "Text only inside rendered HTML" is **not** a seam-breaker — it's the existing `extract_html` branch; the adapter just hands over the page URL.

### D5 — Extractor branch set is closed at five; `.docx` built now
Branches: {text-layer PDF, scanned PDF→OCR, static HTML, JS-rendered HTML, **`.docx`/`.doc`**}. `detect_type` already auto-sniffs the first four from bytes/content-type. `.docx` is the one foreseeable gap (AU offers Word; some portals may serve *only* Word) — build it now as an additive `detect_type` case + a `python-docx` extractor. Additive → cannot regress the existing four.

### D6 — One shared "SPA vs server-rendered" probe feeds both discovery and fetch
`detect_type` distinguishes PDF/scanned/HTML but **cannot** distinguish static HTML from a JS-rendered SPA (a SPA returns a `200 text/html` shell — the AU `/latest/` trap). That single undecidable bit is resolved by **one shared probe utility**, consumed by both sides: it tells the `auto` *discovery* adapter whether to crawl static links or render-then-scrape, **and** tells *fetch* whether to use the static-HTML or JS-HTML branch. Build once, both sides call it. *(Confirmed: build once and share — no duplication.)*

### D7 — `auto` is a best-effort safety net, NOT a guaranteed-reliable universal crawler
When a portal declares no strategy, `auto` runs: probe SPA-vs-SSR (D6) → best-effort list candidates (from known seed URLs + portal homepage; static → crawl links, SPA → render-then-scrape) → flows through the same shared rank/tag tail (D3). It grabs *whatever* it can and clearly labels the run *"running on auto — declare a strategy for production quality."* Production quality comes from a ~30-minute declared strategy (as done for AU), not from perfecting a universal crawler (a bottomless pit that would mean endless rework). **No scaffold/probe-suggestion tool** is built — auto stays a runtime safety net only.

### D8 — Config is a strict menu (typed Literals), not a free-form box
New adapter names are added to the `discovery`/`fetch` `Literal` enums (a one-word edit, done once per new adapter category). `extra="forbid"` stays (ADR-003). Reusing an existing adapter is pure config. This catches typos immediately and protects validated economies from silent breakage — the opposite of a free-form params box.

### D9 — "Don't break validated economies" is enforced by golden snapshots
"Don't change architecture" = **freeze the pattern + freeze validated-economy output** (SG, and any other completed economy), *not* "move no code" (D3 requires internal extraction). Before any edit to a shared hot path (`_discover_index`, `discover()`), capture a **golden-output snapshot** of each validated economy's run; the refactor's acceptance test is "snapshots byte-identical." This replaces manual re-testing with automatic re-validation.

---

## Config schema additions (strict menu — D8)

Added to the `Portal` model (`src/config/economy_config.py`), keeping `extra="forbid"`:
- `discovery` Literal += `api`, `auto`
- `fetch` Literal += `api_versioned_pdf`, `auto`
- New optional fields for the `api`/`api_versioned_pdf` adapters, e.g. `api_base`, `api_collection`, `pdf_path_suffix` (final field list decided at implementation).

Australia's target declaration:
```yaml
anti_bot: none
discovery: api
api_base: https://api.prod.legislation.gov.au/v1
api_collection: Act
fetch: api_versioned_pdf
pdf_path_suffix: text/original/pdf
```

## Build sequence (golden-snapshot-gated — D9)

1. ✅ **Baseline first:** golden-output snapshots of SG discovery captured (P6/P7 × known_only/with_new) — `tests/golden/` + `tests/test_golden_sg_discovery.py`. *Gate satisfied: shared-hot-path edits are now regression-guarded.*
2. ✅ Add `.docx` extractor branch (D5) — `src/fetcher/extractors/docx_text.py`, wired into `router.detect_type`/`route`, `python-docx` in requirements, `tests/test_docx_extractor.py`. Additive; SG golden unchanged; full suite green (654 passed).
3. ✅ Build the shared SPA/SSR probe utility (D6) — `src/crawler/spa_probe.py` (`classify_render` pure fn + `probe_render` network wrapper), `tests/test_spa_probe.py`. Marker-primary / length-secondary logic, calibrated live: AU FRL→SPA (`ng-version`), SG SSO→SSR. New files only; SG golden unchanged; suite 664 passed.
4. ✅ Refactor `discover()`: `_discover_index` now emits raw `(title, url)` only; the shared `_rank_exclude_tag()` tail does BM25 rank + taxonomy exclude + KNOWN/NEW tag + threshold (per portal, so BM25 corpus unchanged). `api`/`auto` will reuse it. *Acceptance met: SG golden byte-identical; suite 666 passed.*
5. ✅ Config Literals + fields (D8: `discovery:api`, `fetch:api_versioned_pdf`, `api_base`/`api_collection`/`pdf_path_suffix`); `_discover_api` (OData `contains(name,term)` per pillar keyword → raw candidates → shared tail) + `_resolve_versioned_pdf_url` (versions API → dated PDF). `australia.yaml` live. Verified against the live API (Privacy Act → KNOWN, correct dated PDF URL); 13 tests; SG golden unchanged; suite 679 passed.
6. ✅ Implement `auto` discovery + fetch as best-effort safety net (D7). `_discover_auto` (probe SSR/SPA → render SPAs via Playwright → generic link extract → shared tail); router `fetch:auto` renders SPA doc shells before extraction. **Portal `discovery` default flipped `TBD`→`auto`** so an undeclared portal auto-crawls (explicit `TBD` still skips). 9 tests; SG golden byte-identical; suite 688 passed.
7. ✅ Australia validated end-to-end (user run, Australia_P7): `api`+`api_versioned_pdf` deliver real text-layer PDF text; CSV 13-col UTF-8-BOM + JSON schema-conformant; OCR pages=0. Open quality items (mapping stage, not discovery/fetch): Privacy Act 1988 degenerate mapping (title-as-verbatim on the 472-pp doc); provision-level tag always NEW for PDF sources (no anchors); a seed law_name/instrument mismatch. Tracked separately.

## Out of scope
- A scaffold / settings-suggestion CLI (D7 — explicitly declined).
- A free-form strategy-params escape hatch (D8 — strict menu chosen).
- Making `auto` a trustworthy standalone universal crawler (D7).
- Re-architecting the RAG/mapping/scoring/output stages.

## Testing
Per the SG-first PRD: assert at the highest stable seam, mock network. New seam-level tests: `list_candidates` adapters return `(title,url)` from fixtures; shared rank/tag tail tested once; `api`/`api_versioned_pdf` URL construction tested against recorded API JSON fixtures (no live calls); `.docx` extraction fixture; SPA/SSR probe against static-vs-shell HTML fixtures; **golden-snapshot regression per validated economy (D9).**
