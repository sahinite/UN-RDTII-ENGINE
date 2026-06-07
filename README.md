# RDTII Extraction Engine

UN Global Hackathon on AI for Digital Trade Regulatory Analysis
Team: [Team Name] | Round: 1 | Last updated: 2026-06-07

> Scaffold generated ahead of implementation. See the full technical plan
> (`RDTII_Engine_Technical_Plan_v2.docx`) and the ClickUp board "UN ESCAP"
> (epics ZONE 1 — Evidence Discovery, ZONE 2 — Intelligent Mapping) for the
> authoritative design + build sequence. Story IDs referenced in code/dir
> comments (e.g. `[Z1-3]`, `[Z2-4]`) map 1:1 to ClickUp stories.

## Quick start (fill in once runnable)

```
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your API keys
python main.py --economy Singapore --pillar 6
```

## Project layout

```
src/config/      economy YAML schema + loader            [Z1-1]
src/crawler/     auto-probe, Crawl4AI, currency, ranker  [Z1-2..Z1-5]
src/fetcher/     fetch + route + segment + translate     [Z2-1, Z2-2]
src/ocr/         OCR two-stage cascade processor         [Z2-1, Z2-5]
src/retrieval/   chunking, embedding, RAG pipeline       [Z2-3]
src/llm/         5-tier LLM cascade client + providers   [Z2-4]
src/mapping/     indicator mapping logic                 [Z2-4]
src/output/      CSV/JSON writer + URL/citation validator[Z2-5, Z2-6]
tools/           cost_logger.py (measured, not estimated)[Z2-6]
economies/       per-economy YAML adapter files          [Z1-1]
tests/           pytest suite (mirrors src/ modules)
data/            sample_kit/ (Round 1 ground truth), benchmark/ (cost-logger PDF)
outputs/         CSV/JSON run outputs (gitignored)
logs/            run logs + cost reports (gitignored)
docs/            schema docs, "add a new economy" guide   [Z1-1.5]
```

## Build order (PDPA-first gate)

Per the planning decisions, **Phase 1 must achieve a fully-verified Singapore
PDPA (Pillar 7) end-to-end run before expanding** to other acts, indicators,
or economies. Follow the ClickUp story sequence: `[Z1-1]` → `[Z1-5]` →
`[Z2-1]` → `[Z2-6]`.

## License

Apache License 2.0 — see `LICENSE`.
