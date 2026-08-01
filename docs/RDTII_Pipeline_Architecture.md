# RDTII pipeline architecture

Run with:

```bash
python main.py --economy Singapore --pillar 7
```

The economy YAML controls portal access, discovery, fetching, OCR, language,
translation, and model choices. The pillar selects the indicators from
`taxonomy.json`.

![RDTII pipeline architecture](RDTII_Pipeline_Architecture.jpg)

```mermaid
flowchart TD
    A["Start: economy + pillar"] --> B["Load and validate economy YAML"]
    B --> C["Load taxonomy and select pillar indicators"]
    C --> D["Prepare run<br/>OCR languages, translation,<br/>multilingual or English retrieval"]
    D --> E["Connect to and smoke-check LLM"]
    E --> F["Load Round 1 seed data<br/>known acts, URLs, provisions, sections"]

    F --> G{Local PDF supplied?}
    G -- "Yes: --pdf" --> H["Create one Zone 1 result<br/>from the local PDF"]
    G -- No --> I["Zone 1: Evidence discovery"]
    I --> I1["Read economy portal strategies"]
    I1 --> I2["Discover through index, API,<br/>sitemap, search, auto, or seeds"]
    I2 --> I3["Rank and filter for pillar"]
    I3 --> I4["Merge Round 1 seeds<br/>and tag KNOWN or NEW"]
    I4 --> J["Zone 1 document list"]
    H --> J

    J --> K{"For each discovered document"}
    K --> L["Fetch complete source<br/>HTML, PDF, API, or linked PDF"]
    L --> M["Extract text<br/>PDF text, HTML, DOCX, or OCR"]
    M --> N["Translate when configured"]
    N --> O["RAG retrieval<br/>chunk + BM25/dense search + rerank"]
    O --> P["LLM provision extraction<br/>map passages to pillar indicators"]
    P --> Q["Validate evidence and output fields"]
    Q --> R["Build records and log cost"]
    R --> K

    K -->|All documents complete| S[Cross-document deduplication]
    S --> T["KNOWN cross-indicator cleanup<br/>and recall diagnostics"]
    T --> U["Add null assessments<br/>for assessed indicators with no provision"]
    U --> V{"Quality gate enabled?"}
    V -- Yes --> W["Run configured quality checks<br/>CI / RUN_PROFILE=gate"]
    V -- No --> X["Write CSV and/or JSON outputs"]
    W --> X
    X --> Y["Save cost report, archives,<br/>diagnostics, and run summary"]
```

## What each part does

| Part | Main code | Role |
|---|---|---|
| Startup | `main.py` | Loads configuration, taxonomy, seeds, models, and runtime settings. |
| Zone 1 | `src/crawler/` | Finds relevant legal and regulatory documents for the economy and pillar. |
| Fetch and OCR | `src/fetcher/`, `src/ocr/` | Gets complete documents and converts them to usable text. |
| Retrieval | `src/retrieval/` | Finds the passages most relevant to each pillar indicator. |
| Mapping | `src/mapping/` | Uses the LLM to extract and map evidence to indicators. |
| Validation | `src/output/validator.py` | Checks evidence, anchors, fields, and review flags. |
| Output | `src/output/` | Writes CSV/JSON and records costs, archives, and diagnostics. |

## Important branches

- The optional quality gate is generic and can be enabled for CI/build
  validation; production can skip it or run it in warning-only mode.
- `--pdf` skips live discovery and starts Zone 2 with one local document.
- A failed discovery falls back to known Round 1 seed URLs when available.
- English-only economies use English retrieval models; other economies use
  multilingual retrieval over the original text.
- `TBD` portal strategies are skipped until configured.
