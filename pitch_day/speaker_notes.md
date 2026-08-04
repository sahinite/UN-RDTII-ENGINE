# Speaker Notes — RDTII Extraction Engine

Target duration: 7 minutes 25 seconds. The seven-minute warning should arrive during the final slide.

## 0:00-0:50 — Hook

“RDTII mapping is expensive because the evidence is scattered across government portals, long PDFs, scanned documents, and multiple legal instruments. Researchers must find the law, locate the exact provision, interpret it, and copy it into a structured template.

Our engine recovered all 27 known indicator checks across six Singapore, Australia, and Malaysia runs against the Round 1 reference set. That is 100 percent known-indicator coverage — not a claim of universal legal accuracy. The latest Singapore Pillar 7 run cost 43 cents in model usage.”

## 0:50-1:20 — Architecture in 30 seconds

“The architecture has two zones. Zone 1 discovers and ranks evidence from official portals and separates KNOWN from NEW. Zone 2 fetches the document, uses OCR or translation when required, retrieves and maps the relevant passages, validates the evidence, and writes CSV and JSON. Every output keeps the source, provision, indicator, confidence, and review notes visible.”

Do not explain individual modules.

## 1:20-3:10 — Differentiators

“Four design choices make the engine reusable.

First, it does not stop at reproducing the sample kit: it detects NEW candidate provisions independently.

Second, it supports eight cloud and local provider tiers. One provider is pinned for each run so a result is consistent and auditable.

Third, every run records token use, OCR activity, latency, and cost instead of relying on estimates.

Fourth, economy-specific portal behavior lives in YAML configuration. Singapore, Australia, and Malaysia use the same core pipeline.”

## 3:10-5:30 — Singapore P7 evidence demo

“Singapore Pillar 7 gives us a clean, official-source demonstration from Singapore Statutes Online.

First, section 50 subsection 4 of the Personal Data Protection Act requires investigation records to be retained for one year. The engine retained that exact sentence, mapped it to P7-I3, and tagged it as a NEW candidate with 0.92 confidence.

Second, section 11 subsection 3 requires an organisation to designate responsibility for compliance. It maps to P7-I4 as a KNOWN provision with 0.98 confidence. Both rows preserve the official URL, section, verbatim text, rationale, and discovery tag.”

Show, in order:

1. archived official Singapore Statutes Online text;
2. exact provision;
3. indicator and discovery tag;
4. confidence and rationale;
5. CSV/JSON output.

The full online run took 9 minutes 37 seconds. Use the completed output during the pitch; do not start a full rerun. If a judge requests execution, show the prototype or a controlled local step and keep the prepared CSV/JSON visible.

## 5:30-6:40 — Measured proof

“Across the six current evaluator runs, the engine recovered 27 out of 27 known indicator checks and surfaced 73 NEW candidate provisions. The latest Singapore Pillar 7 run cost 43 cents in model usage. The system already covers three economies across both required pillars.

These figures have boundaries: known coverage is not universal legal accuracy, and NEW candidates still require policy review. The engine makes that review faster by preserving the exact evidence, source, confidence, and notes.”

## 6:40-7:25 — Closing

“The engine does not replace legal judgment. It removes the expensive evidence-preparation bottleneck while keeping every conclusion auditable.

It makes discovery faster, keeps the evidence visible, and provides a configuration-driven foundation for additional economies and pillars.

From official source, to exact provision, to RDTII indicator, to reviewer-ready output: that is how AI can accelerate regulatory analysis without hiding the law.”

Stop. Do not add another example after the seven-minute warning.
