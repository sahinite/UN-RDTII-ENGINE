# PRD — Pillar-Agnostic, Per-Economy Portal Strategy (Singapore first)

**Status:** ready-for-agent
**Build gate:** must complete a verified Singapore run end-to-end (Zone 1 + Zone 2) on the new strategy before onboarding any further economy.

---

## Problem Statement

Running the engine for Singapore (`python main.py --economy Singapore --pillar 7`) hangs for 30+ minutes in Zone 1 "Crawling" and frequently produces nothing usable. The operator cannot tell what it is doing, and when it does finish it has spent most of its time re-fetching pages it did not need.

Three things are wrong from the operator's perspective:

1. **It crawls the wrong things.** Zone 1 probes the Singapore Statutes Online (SSO) portal with the full taxonomy keyword set (both Pillar 6 and Pillar 7) regardless of the `--pillar` argument, then BFS-crawls every cross-referenced page on each known act. A pillar-7 run should not be touching pillar-6 evidence at all.

2. **The discovery mechanism does not actually work against the live portal.** The configured search URL (`Search?SearchAct={keyword}`) opens a blank advanced-search form rather than running a search; SSO's real search is a stateful JavaScript application that mints an opaque token on submit. So "NEW" discovery silently fails and the engine falls back to slowly grinding KNOWN act pages through a fresh headless browser per URL.

3. **The approach assumes every portal behaves like the one it was built against.** There is no first-class, per-economy description of how a given government portal must be approached (anti-bot handling, how to discover relevant instruments, how to obtain full document text). Adding a second economy means re-deriving all of this and risks repeating the same failure.

The operator needs a Singapore run that finishes well within **10 minutes**, produces accurate RDTII output, works for **any pillar** (not just Pillar 7), and rests on an architecture where onboarding the next economy is additive rather than a rewrite.

## Solution

Introduce a **per-economy portal strategy** that the engine executes. Each economy's configuration declares, per portal, how to reach it (`anti_bot`), how to find pillar-relevant instruments (`discovery`), and how to obtain complete document text (`fetch`). The engine reads these declarations and runs the matching, validated procedure; anything not yet configured degrades gracefully to the Round 1 seed URLs and is logged, rather than hanging.

Singapore is the first economy implemented concretely, validated against the live portals:

- **SSO (primary):** reachable with browser-like request headers (`anti_bot: header_spoof`); relevant instruments are discovered by reading the in-force browse indexes for **Acts and Subsidiary Legislation** (`discovery: index`) and ranking their titles against the **pillar-scoped** keyword set; complete authoritative text is obtained from the per-document **PDF print endpoint** (`fetch: pdf_endpoint`, `?ViewType=Pdf`), which is a text-layer PDF and therefore needs no OCR.
- **Singapore Government Gazette (secondary):** declared but its concrete `discovery`/`fetch` strategy is filled in after SSO is verified, because it serves gazette notices / recently-published subsidiary legislation (a discovery + currency source needed by other pillars), not consolidated act text.

Zone 1 is restructured from "probe → BFS crawl → currency → rank" into a single **discover** step (reachability check → strategy-driven discovery → merge KNOWN seeds → pillar-scoped ranking → cap). Zone 2 gains `pdf_endpoint` fetch routing. Hard wall-clock budgets and seed fallback make a runaway crawl structurally impossible. The number of acts handed to Zone 2 is capped (`ZONE2_MAX_ACTS`) so the LLM-bound mapping stage stays inside the time budget.

A companion findings document records, per economy, the live evidence behind each declared strategy.

## User Stories

1. As an operator, I want a Singapore pillar-7 run to finish Zone 1 + Zone 2 in under 10 minutes, so that I can iterate during the hackathon.
2. As an operator, I want the same command to work for any pillar (e.g. `--pillar 6`, `--pillar 8`) without code changes, so that the engine is not hard-wired to one indicator set.
3. As an operator, I want a pillar-7 run to ignore pillar-6 keywords and evidence entirely, so that discovery is precise and fast.
4. As an operator, I want to see which URL the engine is fetching at each moment and how long the run has taken, so that I can tell progress from a hang.
5. As an operator, I want the engine to never run longer than a bounded budget per portal/stage, so that a misbehaving portal cannot stall the whole run.
6. As an operator, I want the engine to fall back to the Round 1 seed URLs (KNOWN acts) when discovery fails or is unconfigured, so that I still get output instead of a crash or hang.
7. As an operator, I want the Personal Data Protection Act mapped from its complete text (all sections and schedules), so that no provisions are missed because the page lazy-loaded only the first sections.
8. As an operator, I want SSO documents fetched without OCR when a text-layer PDF is available, so that runs are fast and cost nothing for OCR.
9. As an operator, I want discovery to find in-force Acts **and** Subsidiary Legislation, so that pillars whose instruments are regulations are covered, not just pillars backed by a consolidated Act.
10. As an operator, I want NEW (not-in-seed) instruments discovered and tagged, so that the run earns discovery accuracy points beyond the Round 1 database.
11. As an operator, I want only a bounded number of top-ranked acts sent to Zone 2, so that the LLM mapping stage stays within the time budget.
12. As an operator, I want weakly-relevant discovered acts dropped below a relevance threshold rather than padded to fill the cap, so that precision is preserved.
13. As an economy onboarder, I want each economy's portal strategy declared in that economy's configuration file, so that the engine is guided by an explicit, validated description rather than shared assumptions.
14. As an economy onboarder, I want a portal whose strategy is not yet configured to degrade to seed-only and log a clear message, so that a half-onboarded economy is safe and obvious rather than silently broken.
15. As an economy onboarder, I want a companion findings document per economy recording the live evidence (reachability, discovery endpoints, fetch format), so that future maintainers understand why each strategy was chosen.
16. As a developer, I want fetching to try cheap HTTP-with-headers first and only escalate to a stealth browser when a request is blocked or returns a JS shell, so that we pay the browser cost only when necessary.
17. As a developer, I want the SSO portal flagged so it no longer launches a browser per URL on the hot path, so that the prior 30-minute hang cannot recur.
18. As a developer, I want the discovery step to be a unit-testable function that takes a portal + fixture HTML and returns ranked candidates, so that I can verify behavior without the network.
19. As a developer, I want the transport ladder to be a unit-testable function whose escalation can be observed by mocking responses, so that I can assert it stops at the first real success.
20. As a developer, I want Zone 2 to honor a `pdf_endpoint` fetch declaration by rewriting the act URL to its PDF view and routing to the existing PDF path, so that no new extraction pipeline is needed.
21. As a developer, I want the existing OCR cascade preserved but not exercised for SSO, so that scanned-PDF portals onboarded later still work.
22. As an operator, I want KNOWN acts from the Round 1 database always included in the candidate set, so that authoritative known instruments are never dropped by ranking.
23. As a maintainer, I want the strategy abstraction documented in CONTEXT.md and an ADR, so that the design intent survives beyond this change.

## Implementation Decisions

### Strategy abstraction (general)

- **Per-portal declarations** added to the `Portal` model: `anti_bot`, `discovery`, `fetch`, plus strategy parameters (`index_urls`, `pdf_view_suffix`, `transport_fallback`). Existing `js_required`/`playwright_*` fields are retained for backward compatibility but are no longer on the SSO hot path.
- **`anti_bot`**: `none` | `header_spoof` | `playwright_stealth`.
- **`discovery`**: `index` | `search` | `search_js` | `seed_only` | `TBD`. Only `index` and `seed_only` are implemented in this PRD; the rest are accepted values that route to the graceful-degradation path.
- **`fetch`**: `pdf_endpoint` | `html` | `html_wholedoc` | `html_js` | `pdf_link` | `TBD`. Only `pdf_endpoint` (and the existing default HTML/PDF routing) is implemented here.
- **Transport ladder**: a single fetch primitive that escalates httpx (plain) → httpx (browser headers) → stealth Playwright, stopping at the first response that is a real `200` with non-block, non-JS-shell content. `header_spoof` portals are expected to succeed at rung 2; `transport_fallback: playwright_stealth` enables rung 3 per portal. Reuses the existing stealth Crawl4AI runner.
- **Safety rails**: per-portal and per-stage wall-clock budgets and a global Zone-1 cap (env-configurable). On budget exhaustion, discovery failure, or a `TBD` strategy, the engine returns the Round 1 seed KNOWN URLs for that portal/economy and logs an explicit "strategy not configured / discovery failed for portal X" message.

### Per-economy strategy file

- The economy's `economies/{code}.yaml` **is** the machine-readable strategy, validated by the existing `EconomyConfig`/`Portal` Pydantic schema. This is the single source of truth the engine executes.
- A companion human-readable findings document per economy (under a docs location) records the live evidence and rationale. It is documentation, not executed.

### Singapore concrete strategy (validated against live portals)

- **SSO** — `anti_bot: header_spoof`; `discovery: index` over two in-force browse indexes (Acts and Subsidiary Legislation), titles ranked against pillar-scoped keywords; `fetch: pdf_endpoint` with suffix `?ViewType=Pdf`; `transport_fallback: playwright_stealth`. Validated: plain request → 403, browser-headers request → 200; browse index server-rendered (not a SPA), ~500+ in-force Acts retrievable with a large page size across a small number of pages; the act PDF endpoint returns a complete multi-Part text-layer PDF (all sections and schedules present).
- **Gazette** — declared with `anti_bot: none` (reachable without headers) and `discovery`/`fetch` left `TBD` until the SSO path is verified; until then it degrades to the seed/skip path.

### Zone 1 — new discover flow

- Replace the active probe → BFS-crawl path with: (1) reachability check per portal via the transport ladder; (2) strategy-driven discovery (`index` for SSO: fetch index URLs, parse `(title, document-url)` pairs); (3) merge Round 1 KNOWN seed URLs; (4) rank candidate **titles** against the **pillar-scoped** keyword set using the existing ranker (BM25 + sentence-transformer), KNOWN always retained, NEW kept only above a relevance threshold; (5) emit a `Zone1Result` list capped at `ZONE2_MAX_ACTS`.
- **Remove the hardcoded `P6+P7`** in the crawler and the full-taxonomy keyword construction; the pillar argument scopes the taxonomy/keywords end-to-end. Currency checking remains but is effectively pass-through for an in-force index.

### Zone 2 — fetch routing

- The router honors `fetch: pdf_endpoint`: when set for the source portal, the act document URL is rewritten to its PDF view (`+ pdf_view_suffix`) and routed to the existing PDF → pdfplumber extraction path. Text-layer PDFs mean the existing OCR cascade (Tesseract/PaddleOCR → pdfplumber → LLM-vision) is not exercised for SSO but remains intact for future scanned-PDF portals.

### Caps and budgets

- `ZONE2_MAX_ACTS` (env, default 5): maximum acts handed to Zone 2 (KNOWN + top-ranked NEW). Wall-clock budgets and the global Zone-1 cap are env-configurable.

## Testing Decisions

Good tests here assert **external behavior at the highest stable seam**, never implementation details (no asserting on log strings, internal call counts beyond escalation observation, or private helpers). Network is never hit in tests; portal responses are supplied as fixtures/mocks.

- **Strategy config parsing** — at the `load_economy()` / `Portal` seam: a YAML declaring the new fields validates and exposes them with correct defaults; unknown/`TBD` values are accepted. Prior art: `tests/test_economy_config.py` (already extended with `iso_code`/`un_name`).
- **Transport ladder** — at the `transport.fetch(url, portal)` seam: with the httpx layer and Playwright runner mocked, a `403` at the headerless rung followed by a `200` at the headers rung yields the `200` body and does not escalate to Playwright; a JS-shell/empty `200` escalates; a portal without `transport_fallback` never invokes Playwright. Prior art: `tests/test_crawler.py` monkeypatching `_fetch_with_playwright`.
- **Index discovery + ranking + cap** — at the `discover(...)` seam: given fixture index HTML and a pillar, the function returns candidate `Zone1Result`s with KNOWN seeds merged and always retained, NEW candidates above threshold included and weak ones dropped, results capped at `ZONE2_MAX_ACTS`. Prior art: `tests/test_crawler.py` (mock HTML → candidate acts), `tests/test_ranker.py`.
- **Pillar scoping** — at the keyword-construction seam: a pillar-7 run yields only `P7-*` keywords; a pillar-6 run only `P6-*`. Prior art: `tests/test_ranker.py` / `tests/test_crawler.py`.
- **`pdf_endpoint` fetch routing** — at the `route()` seam (`tests/test_z2_1_router.py`): a portal with `fetch: pdf_endpoint` causes the act URL to be rewritten with the PDF suffix and routed to the PDF extraction path; a portal without it is unaffected.
- **Graceful degradation** — at the `discover(...)` / Zone-1 orchestrator seam: a discovery timeout or a `TBD` strategy returns the seed KNOWN URLs rather than raising or hanging.

New seams (`transport.fetch`, `discover`) are introduced at module-function level — the highest point that is testable without driving `main.py`. They mirror the existing crawler test style (monkeypatch the fetch primitive, feed fixture HTML).

## Out of Scope

- Concrete `discovery`/`fetch` implementations for the Singapore Gazette (declared `TBD`; implemented as the second increment of "completing Singapore", after SSO is verified).
- The `search`, `search_js`, `html_wholedoc`, `html_js`, `pdf_link` strategies — accepted as declared values that route to graceful degradation, but not implemented here.
- Onboarding any economy other than Singapore.
- Scanned-PDF OCR work (the cascade is preserved unchanged; SSO does not exercise it).
- Changes to the RAG, mapping/LLM cascade, scoring, or output-writing stages beyond what fetch routing and the act cap require.
- The SSO JavaScript search SPA (token-based) — explicitly not used; discovery uses the browse index instead.

## Further Notes

- Live-validated facts behind the Singapore strategy (to be captured in the findings doc): SSO returns 403 to header-less requests and 200 with browser headers; the in-force Acts browse index is server-rendered and enumerable with a large page size; the act PDF endpoint (`?ViewType=Pdf`) returns a complete text-layer PDF including deep sections and schedules; the plain act HTML page and `?ViewType=Print` are lazy-loaded/shell and must not be used as the full-text source; the Gazette returns 200 without headers and is a jQuery (non-SPA) site whose primary functions are Search and Current Notices.
- The per-URL progress display (spinner step + substep line) added previously remains the operator's live view into discovery and fetch.
- Build sequence: (1) config-model fields + transport ladder; (2) SSO `index` discovery + `pdf_endpoint` fetch + pillar scoping + budgets/fallback; (3) verify Singapore end-to-end on at least two pillars within 10 minutes; (4) probe and implement the Gazette strategy + findings doc; (5) document in CONTEXT.md + ADR and mark Singapore complete.
