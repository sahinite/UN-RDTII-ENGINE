# PRD — Extraction-Recall + KNOWN-Tagging Fix (Singapore P7)

Status: ready-for-agent
Scope: Zone 2 retrieval/mapping + Zone 1 seed loading. Build gate is the Singapore Pillar 7 end-to-end run.

## Problem Statement

When we run Singapore Pillar 7, two things are wrong from a user's point of view:

1. **Buried provisions are silently missed.** Specific in-scope provisions that live deep inside large acts never make it into the output. Four confirmed cases: PDPA s.11(3) (DPO → 7.4), Criminal Procedure Code s.39/40 (computer-data access → 7.5), Companies Act s.199 (retention → 7.3), Income Tax Act s.67 (retention → 7.3). The right document is fetched, but the right *clause* is never extracted.

2. **Every output record is tagged `NEW`; `KNOWN` never appears.** Even for acts that are demonstrably in the Round 1 ground-truth database (PDPA, Cybersecurity Act), the `discovery_tag` comes out `NEW` at both document and provision level. This makes the KNOWN/NEW signal useless and misrepresents which findings reproduce Round 1 versus which are genuinely new discoveries.

Separately, a related precision problem was observed on the same run: P7-I5 ("government access to personal data") produced a large share of false positives (e.g. Radiation Protection, Infrastructure Protection acts tagged as government-access), because the indicator's query is far broader than what the RDTII methodology actually measures.

## Solution

Fix the pipeline so that in-scope provisions buried in large acts are reliably extracted, the KNOWN/NEW tag is correct, and P7-I5/I3 precision reflects the RDTII methodology — **without** trading recall for precision or vice versa. The work is sequenced so each change is attributable to a single cause and gated by measurement, not assumption.

Four workstreams, in dependency order:

- **Step 0 — Seed-loader economy-code fix (unblocks KNOWN tagging).** Normalise both sides of the economy comparison so Round 1 seeds actually load. Today the loader compares the DB's normalised economy ("Singapore" → "SG") against the raw ISO-3 input ("SGP") and silently matches nothing, so `known_urls`/`known_titles`/`known_provisions` are all empty and discovery tags everything `NEW`.
- **Step 1 — Subsection-aware chunking (fixes recall).** Rewrite the chunker to emit ~1,000–1,200-char, subsection-preferring chunks with a parent-heading prefix, so buried clauses get sharp embeddings instead of being diluted inside one oversized section chunk.
- **Step 2 — Offline retrieval harness (measures, in isolation).** A no-LLM scored harness over the four labelled cases that reports, stage by stage, whether the target chunk (a) reaches the fused candidate pool and (b) survives into the reranked top-N. Recall and precision are measured separately.
- **Step 3 — Evidence-gated taxonomy refinement (fixes I5 precision; light touch on I3).** Tighten P7-I5 toward the methodology's actual scope (personal/communications/electronically-stored data + judicial authorisation) via legal question, probe keywords, out-of-scope/negative examples, and an explicit LLM decision-boundary paragraph — measured prompt-first for attribution. P7-I3 receives only a light period-focused adjustment, because the methodology defines it broadly.

Bounded design constraint across all steps: the number of chunks sent to the LLM stays bounded (~10–15). Smaller chunks sharpen retrieval; they do not flood the LLM context. The reranker — not the token-budget trimmer — becomes the real gatekeeper.

## User Stories

1. As an RDTII analyst, I want provisions buried deep inside large acts to be extracted, so that the output reflects what the legislation actually says rather than only its prominent sections.
2. As an RDTII analyst, I want PDPA s.11(3) to be mapped to indicator 7.4, so that the DPO requirement is captured.
3. As an RDTII analyst, I want Criminal Procedure Code s.39/40 to be mapped to indicator 7.5, so that police computer-data access powers are captured.
4. As an RDTII analyst, I want Companies Act s.199 to be mapped to indicator 7.3, so that its statutory retention period is captured.
5. As an RDTII analyst, I want Income Tax Act s.67 to be mapped to indicator 7.3, so that its statutory retention period is captured.
6. As an RDTII analyst, I want documents that appear in the Round 1 database to be tagged `KNOWN`, so that I can distinguish reproductions of Round 1 from genuinely new findings.
7. As an RDTII analyst, I want provision-level `KNOWN`/`NEW` tags to be correct when the Round 1 database carries an anchor-level reference, so that the tag is trustworthy at clause granularity.
8. As an RDTII analyst, I want a clear flag when a provision tag is unresolvable (no anchor could be inferred), so that I know the tag fell back to document level rather than being silently wrong.
9. As an RDTII analyst, I want P7-I5 to stop tagging routine inspection/licensing/safety statutes as government-access, so that the indicator reflects the RDTII privacy scope.
10. As an RDTII analyst, I want P7-I5 to still capture genuine law-enforcement access to computer/communications data (e.g. CPC s.39/40), so that tightening precision does not destroy recall.
11. As an RDTII analyst, I want P7-I3 to keep capturing valid statutory retention obligations (Companies/Income Tax), so that methodology-valid findings are not dropped by an over-narrow query.
12. As an RDTII analyst, I want the LLM given an explicit decision boundary for P7-I5, so that ambiguous retrieved chunks are not over-classified as government-access.
13. As an engineer, I want the number of chunks reaching the LLM to remain bounded (~10–15) even after chunks get smaller, so that token cost and over-extraction do not grow simply because there are more chunks.
14. As an engineer, I want an offline retrieval harness that scores the four target cases stage by stage (in pool / in reranked top-N / extracted), so that I can attribute any miss to retrieval, reranking, or the LLM rather than guessing.
15. As an engineer, I want recall and precision measured separately, so that a recall fix and a precision fix are never conflated.
16. As an engineer, I want the seed loader covered by a test, so that the economy/pillar matching that silently failed cannot regress unnoticed.
17. As an engineer, I want each pipeline knob (candidate-pool size, reranker, prompt) changed only when the harness proves it is the bottleneck, so that changes stay minimal and attributable.
18. As a hackathon reviewer, I want the KNOWN/NEW counts in the output to be meaningful, so that the engine's discovery claims can be verified against Round 1.
19. As an RDTII analyst, I want the P7 false-positive count to be flat or lower after the change, so that precision is not sacrificed for recall.
20. As an RDTII analyst, I want precision/F1 to be flat or improved overall, so that the change is a net win.
21. As a maintainer, I want the economy-code fix applied to both the Round 1 DB path and the Sample CSV path, so that seed loading is correct regardless of source.
22. As a maintainer, I want the chunking change to preserve existing chunk invariants (every chunk has a location reference, ids are unique, short documents still yield at least one chunk), so that downstream mapping/output contracts are unaffected.

## Implementation Decisions

**Step 0 — Seed-loader economy-code fix**
- In the seed loader, the economy comparison must normalise **both** the DB/CSV row value and the incoming `economy_iso` argument before comparing, rather than normalising only the row. Root cause: the DB stores "Singapore" (→ normalised "SG") and the engine passes ISO-3 "SGP", so the equality check never matches and all rows are skipped. Apply identically to the Round 1 DB loader and the Sample CSV loader.
- No change to the public `load_seed_data` signature or to `SeedData`. The fix is internal to the row-matching predicate.
- Expected effect: `known_urls`/`known_titles`/`known_provisions` populate for Singapore P7; document-level `discovery_tag` becomes `KNOWN` for seed-matched acts; the provision tagger stops short-circuiting to `NEW`.
- Known residual: `known_provisions` is sparse (only a minority of Round 1 reference cells carry a `#`-anchor). So provision-level tags will still resolve `NEW` unless the inferred anchor matches a stored anchor. This is acceptable for this PRD — document-level KNOWN is the primary fix; provision-level anchor coverage is noted in Out of Scope.

**Step 1 — Subsection-aware chunking**
- Target chunk size ~1,000–1,200 characters, splitting preferentially at subsection boundaries rather than only at top-level section headers.
- Each chunk is prefixed with its parent heading (act/part/section context) so the embedding carries the provision's location, not just its body text.
- The existing chunking strategies (section-hierarchy first, regex article-splitting fallback) are retained; the oversize-splitting behaviour is generalised from "only split sections over the max" to "prefer subsection-sized chunks throughout".
- Chunk invariants preserved: unique `chunk_id`, every chunk carries a `LocationReference` with a non-empty act title, short documents still yield ≥1 chunk.

**Step 2 — Offline retrieval harness**
- A no-LLM harness that, for each of the four labelled cases, runs the real retrieval pipeline (chunk → BM25 + dense → fusion → rerank) and reports three booleans: target chunk in fused pool (Recall@FusionTopK), target chunk in reranked top-N (Recall@N), and (optionally, gated separately) whether the LLM extracts it.
- Requires a small one-time label set: for each case, the target subsection identity (act + section, e.g. PDPA s.11(3)) used to identify "the correct chunk".
- Precision is measured as a separate gate: the P7-I5 false-positive tally on the full P7 run, compared against the prior baseline (17/24 on the last run).

**Step 3 — Taxonomy + prompt refinement (evidence-gated)**
- **P7-I5 (high priority):** redefine the legal question toward "compel disclosure / intercept / monitor / obtain / access personal data, communications data, subscriber information, or electronically stored information, particularly without prior judicial authorisation". Replace generic "documents/production" probe keywords with privacy/communications-specific terms. Add rich out-of-scope entries (routine regulatory/safety/licensing inspections) and negative few-shot examples. Add an explicit LLM decision-boundary paragraph instructing the model not to classify general inspection/licensing/audit powers as 7.5 unless the provision concerns personal or communications data.
- **P7-I5 recall guardrail:** the decision boundary must treat law-enforcement access to computer/electronically-stored/communications data as in-scope even when the literal token "personal" is absent (so CPC s.39/40 survives). The harness re-asserts CPC s.39/40 lands in the reranked top-N after tightening.
- **P7-I3 (light touch):** the RDTII methodology defines 7.3 as "minimum period of data retention requirements" with the **only** exception being government data — it is **not** restricted to personal/communications data. Therefore I3 is **not** narrowed to personal/comms data. The discriminator is the presence of a fixed minimum/mandatory retention period; keywords centre on the period hinge ("minimum retention period", "shall retain for [N] years"), and government-data retention is excluded per the methodology. Companies s.199 and Income Tax s.67 remain valid.
- **No hard act-title exclusions** as the primary mechanism. Discrimination is by data type / provision content (legal question, keywords, out-of-scope, negatives, prompt), not by Act name — act-title lists overfit to Singapore and do not generalise across economies. (A prior act-title exclude/cap change regressed results and was reverted; that decision stands.)
- **Attribution rule:** the P7-I5 prompt-boundary change is measured **in isolation first** (prompt only, taxonomy unchanged) against the FP baseline before keyword/out-of-scope surgery, so the FP reduction is attributable.
- **Bounded top-k:** `RERANK_TOP_N` stays ~12. Candidate-pool sizes (BM25/dense/fusion top-k) and the reranker are changed **only if** the harness shows, respectively, that the target chunk fails to reach the pool, or reaches the pool but not the reranked top-N.

## Testing Decisions

Good tests here assert **external behaviour** at the highest existing seam, not internal chunk geometry. We test "the right provision is retrievable / the right tag is produced", not "the chunker split at offset N".

- **Seed loader (new test file).** Test `load_seed_data` directly: given the Round 1 DB and an ISO-3 economy code ("SGP") plus pillar ("P7"), it returns non-empty `known_urls`/`known_titles`. This is the regression guard for the silent-empty bug. Prior art: none for the loader itself (that absence is why the bug shipped); follow the fixture style of `tests/test_provision_tag.py`.
- **Provision tag (existing seam).** `tests/test_provision_tag.py` already covers `resolve_provision_tag` (known/new/unresolvable/empty-set/pdf-suffix). Keep green; add a case asserting a KNOWN document with no matching anchor falls back to document-level KNOWN and flags unresolvable, so the Step 0 fix's document-level KNOWN path is locked in.
- **Chunking (existing seam).** `tests/test_rag.py` drives `chunk_document`. Extend with: chunks are bounded near the target size, a known buried subsection (DPO-style clause in a long section) appears as its own chunk, and the existing invariants (unique ids, location reference present, short doc ≥1 chunk) still hold.
- **Offline retrieval harness (new seam).** A scored script/test over the four labelled cases asserting Recall@FusionTopK and Recall@N. This is the acceptance instrument, not a unit test; it should be runnable on demand and report per-case pass/fail. Prior art: the orchestrator/reranker usage in `tests/test_rag.py`.
- **Taxonomy/prompt (existing seam).** `tests/test_z2_4_prompts.py` / `tests/test_z2_4_mapper.py` cover prompt construction and mapping. Add assertions that the P7-I5 system prompt contains the decision-boundary text and that the refined taxonomy entry loads with the new scope fields.

Acceptance gates (the harness + a full P7 run must show):
1. All four recall cases resolved (target chunk in reranked top-N **and** extracted to the correct indicator).
2. P7-I5 false-positive count flat or lower vs the 17/24 baseline.
3. Average chunks sent to the LLM remains bounded (~10–15).
4. Precision/F1 flat or improved overall.

## Out of Scope

- Provision-level KNOWN coverage beyond what the Round 1 database's anchored references support. Most reference cells are act-level, so provision-level tags will often remain `NEW` even after Step 0; building anchor inference to close that gap is a separate effort.
- Other economies and pillars. This PRD is scoped to the Singapore P7 build gate; generalisation follows once P7 is verified.
- P6 indicators. The taxonomy refinement here is P7-only (I5 and I3).
- Swapping or retraining the reranker model, or enlarging candidate pools, unless the offline harness proves the bottleneck is there.
- Any change to the LLM provider cascade or output schema/CSV contract.

## Further Notes

- The methodology source of truth for I3/I5 scope is `docs/RDTII_methodology_and_scoring_criteria.csv` (7.3 = minimum retention period, exception government data; 7.5 = government access to personal data, scored when access is permitted without court orders).
- The economy-code fix is independent of the chunking and taxonomy work and should land first; it is a one-predicate change with a clear before/after and it unblocks correct `discovery_tag` for every subsequent run.
- A consequence of bounded top-k with smaller chunks: ~12 small chunks (~3,600 tokens) sit comfortably under the prompt token budget, so the token-budget trimmer stops being the active gate and the reranker becomes the real control. This is intentional and puts the precision burden on retrieval quality, with the LLM verbatim/confidence checks as the final safety net rather than the front line.
- Prior baseline for comparison: the last Singapore P7 run produced 24 records with a large P7-I5 false-positive share and 4/4 documents tagged `NEW`.
