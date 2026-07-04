# SG vs AU — Pillar 7 Common Issues Analysis

**Purpose:** cross-economy comparison of Singapore P7 and Australia P7 runs to isolate
issues that appear in **both**, so fixes target shared root causes rather than
one-off symptoms. Economy-specific issues are listed separately at the end.

**Evidence base:**
- SG P7 — confirmation run `Singapore_P7_2026-07-03T171145` (23 records; 3 KNOWN, 20 NEW)
- AU P7 — run `Australia_P7_2026-07-04T055311` (13 records; 0 KNOWN, 13 NEW; 1 skipped)

Round 1 ground truth per economy is in `data/database/ESCAP-RDTII-2.1_ Round 1 Database.xlsx`
(sheets `Singapore`, `Australia`).

---

## Common issues (present in BOTH economies)

### C1 — Indicator drift: provisions mapped to the WRONG indicator
The retrieval surfaces the act, but the LLM files a provision under the wrong indicator
(or picks a different section than Round 1's).
- **SG:** Employment Act **s.103** (inspection powers) → I5 instead of Round 1's **s.95**
  (records retention) → I3; Telecom **s.78** → I5 not the retention provision → I3;
  PDPA I3 returns **s.22A** instead of **s.25**.
- **AU:** My Health Records **s.77** tagged under **I3** as well as I1/I2 (Round 1 only
  cites 6.1/6.2); general **Telecommunications Act 1997** pulled in where Round 1 P7
  means the **Interception & Access Act 1979**.
- **Root cause:** LLM extraction/mapping judgment, NOT retrieval ranking (confirmed on SG:
  the target section was already retrieved yet the LLM declined/re-filed it). See ADR-054.
- **Status:** gentle seed-guided retrieval added (KNOWN-only, additive); does not force the
  LLM. True fix needs per-indicator prompt tuning — open.

### C2 — Low KNOWN recall vs Round 1 (symptom shared; causes differ)
The tool fails to reproduce Round 1's specific known provisions under the right indicator.
- **SG:** PDPA s.25, Employment s.95, Telecom retention missed under I3 (LLM declines / drift).
- **AU:** **0 KNOWN** — the acts carrying the I5 sections (Telecom I&A, DATA Act, ASIO)
  failed to fetch, and the one extractable section (Privacy Act **s.33D**) stayed NEW due to
  a Round 1 title-attribution mismatch (33D filed under the "PIA 2020" guideline title, not
  the Act).
- **Root cause (shared symptom):** KNOWN provisions don't reach the output — via LLM
  rejection (SG) or fetch failure + title mismatch (AU).
- **Status:** section-matching KNOWN tagging works when the provision is extracted
  (proved on SG PDPA 11(3)/Companies 199/ITA 67 and AU-P6 My Health s.77). Recall upstream
  is the gap.

### C3 — Weak act-identity resolution in discovery
Discovery treats each URL as a distinct act, so the same act appears multiple times / the
wrong variant is chosen / the KNOWN cap drops real acts.
- **SG:** Cybersecurity Act fetched under TWO URLs — consolidated `/Act/CA2018` and
  as-enacted `/acts-supp/9-2018` → duplicate provisions.
- **AU:** same act reachable under multiple IDs (`C2004A02124` vs `.../latest/text` vs
  `/details/...`); wrong Telecom act substituted; **14 known seed URLs capped to 12**
  (`ZONE2_MAX_KNOWN_ACTS`) so real acts are dropped before fetch.
- **Root cause:** no canonical act-identity key; URL-level dedup only.
- **Status:** output-level cross-document dedup (ADR-053) hides SG's duplicate rows but does
  NOT prevent the double fetch or the AU cap/variant selection — open.

### C4 — Duplicate / redundant provision rows
- **SG:** cross-document dupes from the same act under two URLs (see C3).
- **AU:** within-document near-dupes — SOCI Act **"Section 4" ×2** (I2),
  **"Section 43(1)"** and **"Section 43(1)-(2)"** (I5); plus the SAME provision emitted under
  multiple indicators (My Health s.77 across I1/I2/I3 in P6, analogous pattern in P7).
- **Root cause:** per-document dedup keys on (article, snippet[:50]); cross-document dedup keys
  on (law, indicator, article, snippet) — neither collapses same-provision-across-indicators or
  near-identical article variants.
- **Status:** partial — cross-doc dedup covers SG's flavour only.

### C5 — Malformed article / citation labels on large legislation PDFs
Both economies pull large "compilation" PDFs whose structure the chunker/parser mis-labels.
- **SG:** `article` values like **"Art. 371 | Page 371"**, **"Art. 2020 | Page 539"**
  (page numbers parsed as article numbers); running-header furniture leaking into
  `verbatim_snippet`; operative sections with **empty `article_number`** (Employment s.95,
  PDPA s.25 carry `''`).
- **AU:** Privacy Act APPs mis-formatted — **"Section 8.1"**, bare **"8.2"**,
  **"Section 9(8.3)"** (Australian Privacy Principle numbers parsed as sections); one row
  dropped for **empty `law_name`** (schema violation).
- **Root cause:** chunker section/heading extraction and the article-label formatter don't
  handle legislation.gov.au / SSO compilation layouts (schedules, running headers, space-form
  headings). Partly addressed for AU compilations (see CONTEXT "AU compilation chunking") but
  article-label quality and empty labels remain.
- **Status:** open.

### C6 — LLM nondeterminism + single-provider fragility (infra)
- **SG:** record count varied run-to-run (27 → 24 → 19 → 23) at temperature 0; an OpenAI
  quota 429 mid-run once produced silently-partial output.
- **AU:** same single-provider `.env` (`LLM_PROVIDER=openai`, no fallback key), same exposure.
- **Root cause:** one pinned provider, no cascade fallback; inherent LLM variance.
- **Status:** open (see memory: llm-single-provider-no-fallback).

---

## Suggested fix priority (shared-first)

1. **C3 act-identity** — canonical act key in discovery (collapse consolidated/supplement/
   variant URLs; make the KNOWN cap act-aware). Fixes the double fetch (SG) + variant/cap
   drops (AU) at the source, and shrinks C4.
2. **C5 citation labels** — repair the article-label formatter (drop page-as-article, strip
   furniture, never emit empty `law_name`/`article`). Improves both economies' output quality.
3. **C4 dedup** — extend dedup to collapse same-provision-across-indicators and near-identical
   article variants.
4. **C1/C2 recall** — per-indicator prompt tuning so the LLM accepts delegated-period /
   correctly-indicatored provisions (higher risk; validate carefully).
5. **C6 infra** — add a fallback LLM provider so a 429 fails over instead of truncating.

---

## Economy-specific issues (NOT common — listed for completeness)

**AU-only**
- **Fetch reliability (dominant AU blocker):** `api_versioned_pdf` builds dated PDF paths that
  **404** (`C2004A02124` Telecom I&A, `C2004A05145` at `.../2026-06-04/.../text/original/pdf`),
  and cannot resolve the **`/details/…`** URL form (`c2021a00098`, `c2021c00496`, `c2023c00106`)
  → key I3/I5 acts (Telecom I&A, DATA Act, ASIO) never fetched. This is why AU KNOWN = 0.
- Round 1 title-attribution mismatch (Privacy Act s.33D filed under the PIA guideline title).

**SG-only**
- PDPC regulator pages are JS SPAs needing render (`html_js`) + sitemap discovery — resolved.
- Criminal Procedure Code 2010 is a ~2M-char code; target provisions buried (retrieval/chunk
  coverage limit).

---

*Generated 2026-07-04 as pre-fix analysis. No code changes made in producing this file.*
