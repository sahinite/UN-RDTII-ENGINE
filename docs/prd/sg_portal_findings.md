# Singapore Portal Findings — Live-Validated Evidence

**Economy:** Singapore  
**Validated:** 2026-06-25  
**Purpose:** Documents live-observed facts behind each strategy declaration in `economies/singapore.yaml`. Future agents updating SG strategy must update this file with new observations.

---

## Portal 1 — Singapore Statutes Online (SSO)

**URL:** https://sso.agc.gov.sg  
**Declared strategy:** `anti_bot: header_spoof`, `discovery: index`, `fetch: pdf_endpoint`

### Anti-bot findings

SSO serves a real HTML response (not a bot block) when the HTTP request carries browser-like `Accept`, `Accept-Language`, and `User-Agent` headers. A plain `httpx` request with no headers returns an HTTP 403 or a minimal JS-shell response. Setting headers consistent with Chrome 124 on macOS (`header_spoof`) is sufficient to obtain the index pages.

Playwright (`playwright_stealth`) is configured as `transport_fallback` for SSO. It is invoked only when `header_spoof` rungs 1 and 2 both fail (e.g. SSO tightens bot detection). Under normal conditions, Playwright is not needed for index fetches.

### Discovery findings — why `index` (not `search`)

SSO's search endpoint (`/Search?SearchAct={keyword}`) opens a blank advanced-search form. Submitting it programmatically fails because the real search is a stateful JavaScript application that generates an opaque session token on submit; there is no stable URL template for a keyword search result.

The browse index pages are stable, paginated-friendly URLs:

- Acts: `https://sso.agc.gov.sg/Browse/Act/Current/All?PageSize=500&SortBy=Title&SortOrder=ASC`
- Subsidiary Legislation: `https://sso.agc.gov.sg/Browse/SL/Current/All?PageSize=500&SortBy=Title&SortOrder=ASC`

`PageSize=500` returns up to 500 instruments per page (the full Acts list is ~400; the SL list is larger). These pages include `<a href="/Act/XXXX">` and `<a href="/SL/XXXX-SNNN">` links for every in-force instrument. `discovery: index` fetches these two URLs, parses `/Act/` and `/SL/` hrefs, then BM25-ranks titles against the pillar-scoped keyword set.

### Fetch findings — why `pdf_endpoint`

Every SSO act page (`/Act/XXXX`) has a "PDF" button that navigates to `https://sso.agc.gov.sg/Act/XXXX?ViewType=Pdf`. This endpoint returns:
- Content-Type: `application/pdf`
- A text-native PDF (pdfplumber can extract clean text; no OCR needed)
- The complete consolidated text: all Parts, Sections, Schedules

This avoids: (a) JS rendering of the main HTML act page; (b) per-section lazy loading; (c) OCR cost and CER risk.

`fetch: pdf_endpoint` with `pdf_view_suffix: "?ViewType=Pdf"` causes Zone 2 router to rewrite `https://sso.agc.gov.sg/Act/PDPA2012` → `https://sso.agc.gov.sg/Act/PDPA2012?ViewType=Pdf` before downloading.

### Relevant instruments for Pillar 7

BM25-ranked against P7 keywords (data protection, personal data, privacy, consent, data breach, PDPA) from the full in-force index, the top-scoring SSO instruments are:

| Rank | Title | SSO Code |
|------|-------|----------|
| 1 | Personal Data Protection Act 2012 | PDPA2012 |
| 2 | Personal Data Protection (Notification of Data Breaches) Regulations 2021 | PDPA2012-S362 |
| 3 | Personal Data Protection (Fees) Regulations 2021 | PDPA2012-S460 |
| 4 | Personal Data Protection (Appeal) Regulations 2021 | PDPA2012-S461 |
| 5 | Spam Control Act | SPAM2007 |

Acts ranked below threshold (< `NEW_SCORE_THRESHOLD=0.05`) are dropped, not padded to the cap.

---

## Portal 2 — Singapore Government Gazette

**URL:** https://www.egazette.gov.sg  
**Declared strategy:** `anti_bot: none`, `discovery: TBD`, `fetch: TBD`

### Status: TBD — not yet validated

The Gazette portal serves recently-published subsidiary legislation and gazette notices. It is relevant as a currency/recency source (instruments published since the last SSO consolidation) but is not needed for the primary Pillar 7 run which is backed by the consolidated PDPA on SSO.

The concrete `discovery` and `fetch` strategy will be filled in when Pillar coverage requires gazette notices or when SSO index discovery is insufficient for a particular pillar.

Until declared, Zone 1 skips this portal and logs `"discovery": "TBD"`.

---

## Transport Ladder — Observed Rung Behaviour

| Rung | Method | SSO result |
|------|--------|------------|
| 1 | Plain httpx (no headers) | 403 Forbidden |
| 2 | httpx + browser headers | 200 OK — real HTML |
| 3 | Playwright stealth | 200 OK — real HTML (fallback only) |

Rung 1 is skipped for SSO because `anti_bot: header_spoof` signals that plain requests are known to fail. `_is_real_response()` checks: status != 200, empty body, and JS-shell detection (body < `_JS_SHELL_MIN_CHARS` = 200 characters).
