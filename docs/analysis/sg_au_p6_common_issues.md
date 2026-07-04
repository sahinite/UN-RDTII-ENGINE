# SG vs AU — Pillar 6 Common Issues Analysis

**Purpose:** cross-economy comparison of Singapore P6 and Australia P6 runs to isolate
issues that appear in **both**. Companion to `sg_au_p7_common_issues.md`.

**Evidence base:**
- SG P6 — `Singapore_P6_2026-07-03T171145` (4 records; 3 KNOWN, 1 NEW; +1 null assessment)
- AU P6 — `Australia_P6_2026-07-04T060105` (8 records; 3 KNOWN, 5 NEW)

**Round 1 ground truth (Pillar 6 — Cross-border Data Policies):**
| | Singapore | Australia |
|---|---|---|
| 6.1 | PDPA 2012 (score 0, no localization ban) | My Health Records Act 2012 **s.77** |
| 6.2 | Companies Act 1967 **s.199** (0.5) | My Health Records Act 2012 **s.77** |
| 6.3 | PDPA 2012 (score 0, no infra req) | Privacy Act 1988 (no section) |
| 6.4 | PDPA 2012 **s.26** (score 1, transfer) | Privacy Act 1988 (no section) |

> Note: both P6 pillars are **small (2 distinct acts each)** and both **fetched cleanly**
> — so several P7 common issues (fetch/resolver failures, act-identity duplicates,
> the KNOWN cap) simply do not surface here. The common set is therefore shorter and
> honest, not padded.

---

## Common issues (present in BOTH economies)

### P6-C1 — Same KNOWN provision emitted under EXTRA indicators (cross-indicator duplication + over-mapping)
The one section-matched KNOWN provision is repeated across more indicators than Round 1
assigns, inflating KNOWN counts and producing near-identical rows.
- **SG:** PDPA **s.26(1)** emitted under **I1 AND I4** (same law/article/snippet). Round 1
  ties s.26 to 6.4 (I4, cross-border transfer); the **I1** row is spurious.
- **AU:** My Health Records **s.77(1)** emitted under **I1, I2 AND I3**. Round 1 ties s.77 to
  6.1/6.2 (I1/I2); the **I3** row is spurious.
- **Root cause:** (a) the mapper accepts the same provision for multiple indicators without a
  best-indicator arbitration; (b) dedup keys include `indicator_id`, so the cross-doc dedup
  (ADR-053) does NOT collapse "same provision, different indicator".
- **Impact:** over-counts KNOWN, adds redundant rows a grader will discount.
- **Ties to:** P7 **C1** (indicator drift) + P7 **C4** (duplicate rows) — same shared root
  across pillars.
- **Status:** open.

### P6-C2 — Weak indicator/section assignment for the SECOND known act (mapping quality)
The economy's second Round 1 act is mapped imprecisely.
- **SG:** Companies Act mapped to **s.363(4)** for I2, but Round 1's 6.2 provision is
  Companies **s.199** → the Round 1 known section is missed and a different one substituted
  (same "wrong section for indicator" pattern as SG-P7 Employment s.103-vs-s.95).
- **AU:** Privacy Act 1988 spread across I2/I4/I5 as NEW; the cross-border-relevant provisions
  (APP 8) land near I4 but the spread is loose. Round 1 cites Privacy Act with **no section**,
  so NEW is correct — the weakness is the scattered indicator placement, not the tag.
- **Root cause:** LLM extraction/mapping judgment (same family as P7 C1/C2); asymmetric between
  economies (SG has a concrete wrong-section miss; AU is looser placement).
- **Status:** open.

---

## Shared SUCCESS (works in both — keep, don't regress)

- **Section-matching KNOWN tagging fired correctly in both:** SG PDPA **s.26** → KNOWN,
  AU My Health **s.77** → KNOWN (conf 1.00). The prose-section KNOWN mechanism (ADR-049)
  generalizes across economies when the provision is actually extracted.
- **Both correctly emit NEW cross-border provisions** where Round 1 gave no section
  (SG Companies s.363; AU Privacy Act APP 8 / s.20Q / s.26XE) — the 20-point NEW path works.

---

## P7 common issues that did NOT recur in P6 (why)

| P7 issue | In P6? | Reason |
|---|---|---|
| C3 act-identity / duplicate-URL acts | No | 2 clean acts per economy; no consolidated-vs-supplement variants fetched |
| C5 malformed citation labels | **AU only** | AU garbled APP labels ("8.1", "8.2", "Section 9(8.3)"); SG P6 labels were clean → not common |
| AU fetch 404 / `/details/` unresolved | No | P6 acts (`C2012A00063`, `C2004A03712`) resolved fine |
| C6 LLM nondeterminism | Not observed | small runs; not exercised enough to show variance |

*(C5 is AU-specific for P6: the Australian Privacy Principles live in Schedule 1 and the
article formatter mislabels APP numbers as sections — but SG P6 had no equivalent, so it is
NOT a P6 common issue even though it IS a P7 common issue.)*

---

## Suggested fix priority (P6-shared)

1. **P6-C1 (cross-indicator duplication / over-mapping)** — highest-value shared fix:
   add best-indicator arbitration (or a dedup pass that collapses same-(law, article, snippet)
   across indicators, keeping the best-fit indicator). Directly cuts spurious KNOWN rows in
   both economies and overlaps P7 C4.
2. **P6-C2 (mapping precision for the second act)** — folds into the broader P7 C1/C2
   prompt-tuning work; lower priority, higher risk.

---

*Generated 2026-07-04 as pre-fix analysis. No code changes made in producing this file.*
