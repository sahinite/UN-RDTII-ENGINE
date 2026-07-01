# Australia Portal Findings — Live-Validated Evidence

**Economy:** Australia
**Validated:** 2026-07-01 (live inspection via Claude-in-Chrome on legislation.gov.au)
**Purpose:** Documents live-observed facts behind each strategy declaration in `economies/australia.yaml`. Future agents updating AU strategy must update this file with new observations.

---

## Portal 1 — Federal Register of Legislation (FRL)

**URL:** https://www.legislation.gov.au
**Declared strategy (live):** `anti_bot: none`, `discovery: api`, `fetch: api_versioned_pdf`, `api_base: https://api.prod.legislation.gov.au/v1`, `api_collection: Act`, `pdf_path_suffix: text/original/pdf` — adapters implemented (`_discover_api` / `_resolve_versioned_pdf_url`); see [unified-portal-strategy.md](unified-portal-strategy.md) step 5.

### Why AU is architecturally different from Singapore

SSO (SG) is a **server-rendered** site: its browse index is HTML we scrape + BM25-rank (`discovery: index`). The FRL (AU) is a **single-page Angular app** backed by a clean **public OData JSON API**. We do not scrape rendered HTML; we query the API directly. This is the first portal to need an `api` discovery adapter and an `api_versioned_pdf` fetch adapter.

### Anti-bot findings

**None.** Plain `curl`/`httpx` with the default User-Agent (no browser headers) returns `200` on both the API and the PDF files. No `header_spoof`, no Playwright needed. (Contrast: SG SSO 403s a header-less request.)

| Rung | Method | FRL API result | FRL PDF result |
|------|--------|----------------|----------------|
| 1 | Plain httpx (no headers) | 200 `application/json` | 200 `application/pdf` |

### Discovery findings — why `api` (not `index`)

The Browse tab (Collection → Browse by Name → letter A–Z) fires **one OData request per letter**. For "P":

```
https://api.prod.legislation.gov.au/v1/titles/search(criteria='and(
  browsebyname("P"),collection(Act),status(InForce))')
  ?$select=id,name,year,number,isPrincipal
  &$orderby=name asc&$count=true&$top=100&$skip=0
```

Returns clean JSON — each row has `id` (e.g. `C2004A03712`), `name`, `year`, `number`, `isPrincipal`, `isInForce`.

- Scope with `collection(Act)` + `status(InForce)` for in-force Acts (316 Acts under "P" alone).
- Enumerate the whole collection by iterating `browsebyname` A–Z, or page with `$top`/`$skip` (`$count=true` gives the total).
- Pillar targeting: `$filter=contains(name,'Privacy')` narrows by title keyword.
- The discovered `id` is a **title ID**, not a document URL — fetch resolves it (below).

Verified: `contains(name,'Privacy')` under "P" returns 11 titles; **Privacy Act 1988 = `C2004A03712`** (principal, in force).

### Fetch findings — why `api_versioned_pdf` (two-step, version-aware)

The PDF lives at a **date-stamped** URL:

```
https://www.legislation.gov.au/{titleId}/{start}/{start}/text/original/pdf
```

⚠️ **The `/latest/text/original/pdf` form does NOT work** — it returns the SPA HTML shell (`text/html`), not the PDF. This trap holds even server-side (curl gets the shell). You **must** use explicit dates.

Step 1 — resolve the latest in-force version's start date via the versions API:

```
https://api.prod.legislation.gov.au/v1/versions/search(criteria='affects(Amend,Disallow)')
  ?$filter=titleId eq 'C2004A03712'&$orderby=start desc
```

Pick the row with `isLatest: true` → use its `start` (e.g. `2026-06-04`). **Skip future-dated rows** (`registerId: null`) — those are scheduled but not-yet-registered compilations.

Step 2 — build the dated URL:

```
https://www.legislation.gov.au/C2004A03712/2026-06-04/2026-06-04/text/original/pdf
```

Verified: returns `application/pdf`, `%PDF-2.0`, **1.92 MB, 472 pages, text-layer** (pdfplumber extracts ~3000 chars/page — e.g. page 5 begins "Subdivision E—Integrity of credit reporting information"). **No OCR needed.** A `.../text/original/word` sibling returns the `.docx` (the Word extractor branch — see unified PRD).

The downloaded file is named by **registerId** (`C2026C00227.pdf`, compilation 104), confirming the file endpoint is keyed on the resolved compilation, not the title ID.

### Relevant instruments for Pillar 7 (data protection)

| Title | Title ID | Notes |
|-------|----------|-------|
| Privacy Act 1988 | `C2004A03712` | Principal act, in force; primary P7 evidence |
| Privacy Amendment (Notifiable Data Breaches) Act 2017 | `C2017A00012` | Amendment (folded into the consolidated Privacy Act) |
| Privacy Amendment (Enhancing Privacy Protection) Act 2012 | `C2012A00197` | Amendment |

---

## Portal 2 — Office of the Australian Information Commissioner (OAIC)

**URL:** https://www.oaic.gov.au
**Declared strategy:** `anti_bot: none`, `discovery: TBD`, `fetch: TBD`

### Status: TBD — not yet validated

Secondary regulator portal (privacy guidance, APP guidelines). Relevant as supplementary P7 context, but the primary P7 evidence is the consolidated Privacy Act 1988 on the FRL. Strategy filled in only if pillar coverage requires OAIC guidance.
