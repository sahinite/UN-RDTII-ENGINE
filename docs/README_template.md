# \[Tool Name\] — AI Tool for Digital Trade Regulatory Analysis

UN Global Hackathon on AI for Digital Trade Regulatory Analysis  
Team: \[Team Name\] | Round: \[1 / Final\]  
Last updated: \[YYYY-MM-DD\]

**Round 1 requirement:** Submit the completed **Quick Start** section with your Functional Prototype (judges must be able to set up and run your tool). All other sections are recommended now and mandatory for Stage 3\.

---

## What This Tool Does

This tool automates two tasks required by the UN Regional Digital Trade Integration Index (RDTII):

**Task 1 — Automated Evidence Discovery**  
Given an economy name and a regulatory topic, the tool crawls official government legal portals, retrieves the relevant legislation (including scanned/image-based PDFs), and extracts clean, structured text — without any manual steps.

**Task 2 — Intelligent Mapping & Categorization**  
The extracted text is mapped to specific RDTII indicator IDs. Each matched provision is recorded with an exact article-level citation, a verbatim text snippet, and a Discovery Tag marking whether the provision was found independently (NEW) or matched a known example (KNOWN).

**Mandatory scope:** Pillar 6 (Cross-border Data Flows) and Pillar 7 (Domestic Data Protection)  
**Economies covered:** \[List the economies your tool covers\]

---

## Quick Start

⚠ **Required for Round 1 submission.** A reviewer with basic Python knowledge should be able to run this in under 10 minutes using only the steps below — no help from your team.

### 1\. Clone the repository

git clone https://github.com/\[your-org\]/\[repo-name\].git

cd \[repo-name\]

### 2\. Set up the environment

\# Python 3.10+ required

python \-m venv venv

source venv/bin/activate        \# Windows: venv\\Scripts\\activate

pip install \-r requirements.txt

### 3\. Configure API keys

Copy the example environment file and fill in your credentials:

cp .env.example .env

Open `.env` and set:

LLM\_PROVIDER=openai              \# or: ollama, anthropic, mistral

LLM\_MODEL=gpt-4o                 \# see "Swapping the LLM" section below

OPENAI\_API\_KEY=sk-...            \# leave blank if using a self-hosted model

OCR\_ENGINE=azure                 \# or: tesseract, paddleocr, mistral\_ocr

AZURE\_DI\_ENDPOINT=https://...    \# only needed if OCR\_ENGINE=azure

AZURE\_DI\_KEY=...

### 4\. Run the tool

python main.py \--economy Singapore \--pillar 6

**Output:** Results are written to `outputs/Singapore_P6_[timestamp].csv` and `outputs/Singapore_P6_[timestamp].json`

---

## Full Usage

python main.py \\

  \--economy "Malaysia" \\

  \--pillar 6 \\          \# 6, 7, or all

  \--output-dir outputs/ \\

  \--format csv          \# csv, json, or both

### Run on multiple economies

python batch\_run.py \--economies Singapore Australia Malaysia \--pillar 6 7

### Run on a provided PDF (bypass crawler)

python main.py \--economy Singapore \--pillar 6 \--pdf path/to/law.pdf

---

## Architecture Overview

Input: Economy \+ Pillar

       │

       ▼

┌─────────────────────────────────────────────┐

│  TASK 1 — Evidence Discovery                │

│                                             │

│  1\. Portal Crawler                          │

│     └─ Navigates gov. portal hierarchy      │

│     └─ Retrieves source PDF URL             │

│                                             │

│  2\. Document Processor                      │

│     └─ Downloads PDF                        │

│     └─ OCR (if scanned)                     │

│     └─ Structural parsing (articles/§§)     │

└─────────────────────────────────────────────┘

       │  Clean structured text

       ▼

┌─────────────────────────────────────────────┐

│  TASK 2 — Mapping & Categorization          │

│                                             │

│  3\. Retrieval (RAG)                         │

│     └─ Chunking \+ embedding                 │

│     └─ Hybrid search (BM25 \+ dense)         │

│     └─ Reranking                            │

│                                             │

│  4\. LLM Mapping                             │

│     └─ Maps provision to RDTII indicator    │

│     └─ Extracts verbatim snippet            │

│     └─ Assigns Discovery Tag                │

│     └─ Generates article-level citation     │

└─────────────────────────────────────────────┘

       │

       ▼

Output: CSV / JSON with 13 fields per provision

### Key modules

| Module | File | Description |
| :---- | :---- | :---- |
| Portal Crawler | `src/crawler/crawler.py` | Navigates gov. portals, retrieves PDF URLs |
| OCR Processor | `src/ocr/processor.py` | Handles scanned PDFs, preserves structure |
| Chunker | `src/retrieval/chunker.py` | Splits text into article-level chunks |
| Embedder | `src/retrieval/embedder.py` | Generates embeddings for semantic search |
| Mapper | `src/mapping/mapper.py` | LLM prompt \+ RDTII indicator mapping logic |
| Output Writer | `src/output/writer.py` | Formats and writes CSV/JSON output |

---

## Swapping the LLM

This section is required by the hackathon rubric (No Vendor Lock-in).  
The system is designed so the LLM can be replaced by changing one config value.

### Use OpenAI (default)

LLM\_PROVIDER=openai

LLM\_MODEL=gpt-4o

OPENAI\_API\_KEY=sk-...

### Use a self-hosted open-weight model (Ollama)

Install Ollama and pull a model:

ollama pull llama3

ollama serve

Set in `.env`:

LLM\_PROVIDER=ollama

LLM\_MODEL=llama3

\# No API key needed

No other code changes required. The LLM interface is abstracted in `src/llm/client.py`.

### Use Mistral (cloud)

LLM\_PROVIDER=mistral

LLM\_MODEL=mistral-large-latest

MISTRAL\_API\_KEY=...

### Adding a new LLM provider

1. Create a new class in `src/llm/providers/` inheriting from `BaseLLMClient`  
2. Implement the `complete(prompt, system)` method  
3. Register the provider in `src/llm/client.py`

---

## Swapping the OCR Engine

| Engine | Config value | Notes |
| :---- | :---- | :---- |
| Azure Document Intelligence | `azure` | Best accuracy; requires API key |
| Tesseract | `tesseract` | Free, open-source; lower accuracy on complex layouts |
| PaddleOCR | `paddleocr` | Good multilingual support; self-hosted |
| Mistral OCR | `mistral_ocr` | Strong on mixed-language documents |

Change `OCR_ENGINE` in `.env` — no code changes needed.

---

## Supported Economies & Portals

| Economy | Official Portal | Language | Notes |
| :---- | :---- | :---- | :---- |
| Singapore | sso.agc.gov.sg | English | Machine-readable PDFs |
| Australia | legislation.gov.au | English | Machine-readable PDFs |
| Malaysia | federalgazette.agc.gov.my | English / Malay | Mixed scanned and digital |
| \[Add more\] |  |  |  |

---

## Output Format

Each run produces two files:

### CSV (`outputs/[economy]_P[pillar]_[timestamp].csv`)

Columns are in this exact order. Do not rename or reorder — judges validate programmatically.

| \# | Column | Required | Description |
| :---- | :---- | :---- | :---- |
| 1 | economy | Required | Official UN country name |
| 2 | law\_name | Required | Full official statute name and year |
| 3 | law\_number\_ref | Required | Official act/law number (e.g. Act 709, B.E. 2562\) |
| 4 | last\_amended | Required | Year of most recent amendment; blank if never amended |
| 5 | indicator\_id | Required | RDTII code (e.g. P6-I1, P7-I3) |
| 6 | article | Required | Exact article and paragraph (e.g. Art. 26(2)) |
| 7 | discovery\_tag | Required | NEW \= independent find; KNOWN \= sample kit |
| 8 | location\_reference | Optional | PDF: page number; HTML: URL anchor or section path |
| 9 | verbatim\_snippet | Required | Exact quoted text — no paraphrasing |
| 10 | mapping\_rationale | Optional | Max 300 chars: why this provision maps to this indicator |
| 11 | source\_url | Required | Direct URL to law on official government portal |
| 12 | confidence | Optional | Model certainty score (0.00–1.00) |
| 13 | notes | Optional | OCR issues, partial doc, bilingual sources, cross-references |

### JSON (`outputs/[economy]_P[pillar]_[timestamp].json`)

Same fields plus extended metadata: `ocr_quality_cer`, `processing_time_seconds`, `raw_context_before`, `raw_context_after`, `pdf_is_scanned`, `retrieval_method`.

See `output_schema_sample.json` for a complete example.

---

## Actual Cost Per Document

Required by the hackathon rubric for UN sustainability assessment.  
**These must be measured costs from real runs — not estimates. Show your working.**

Benchmark: one average-sized legal document (50 pages, \~3,500 characters/page)

### How we measured this

Run the included cost logger on your benchmark document:

python tools/cost\_logger.py \--pdf data/benchmark/benchmark\_50pages.pdf \--economy Singapore \--pillar 6

This records token counts, API call costs, and processing time to `logs/cost_report.json`.

### Measured results

| Component | Engine used | Measured cost |
| :---- | :---- | :---- |
| OCR | \[your OCR engine\] | $\[X.XXX\] |
| Embedding | \[your embedding model\] | $\[X.XXX\] |
| LLM mapping | \[your LLM \+ version\] | $\[X.XXX\] |
| Crawling | \[self-hosted / API\] | $\[X.XXX\] |
| **Total (current stack)** |  | **$\[X.XX\] per document** |
| **Total (open-weight swap)** | \[e.g. Llama 3 \+ Tesseract\] | **$\[X.XX\] per document** |

**Measured on:** \[date\]  
**Benchmark document:** \[law name, economy, page count\]  
**Token counts:** Input \[X,XXX\] tokens | Output \[XXX\] tokens  
**Wall-clock time:** \[X.X\] seconds per document

### Cost log excerpt

{

  "document": "PDPA\_Singapore\_86pages.pdf",

  "measured\_on": "2026-07-15",

  "ocr": { "engine": "\[engine\]", "pages": 86, "cost\_usd": 0.000 },

  "embedding": { "model": "\[model\]", "tokens": 0, "cost\_usd": 0.000 },

  "llm": { "model": "\[model\]", "input\_tokens": 0, "output\_tokens": 0, "cost\_usd": 0.000 },

  "total\_cost\_usd": 0.000,

  "processing\_time\_seconds": 0.0

}

Fill in the JSON above with your actual `logs/cost_report.json` output before submitting.  
Judges will verify cost claims against your code. Unexplained discrepancies will be flagged.

---

## Known Limitations

Transparency here builds credibility with judges. Be honest.

- **Dynamically rendered portals:** The crawler cannot handle portals that require JavaScript execution. Affected economies: \[list any\].  
- **Bilingual documents:** Provisions in non-English sections of mixed-language PDFs may be missed. Affected economies: \[list any\].  
- **Delegated legislation:** The tool retrieves primary statutes but does not automatically follow cross-references to subordinate regulations.  
- **OCR on handwritten annotations:** Handwritten margin notes in gazette scans are excluded from extraction.  
- **Confidence calibration:** Confidence scores are relative, not calibrated probabilities. Scores below 0.80 should be flagged for human review.

---

## Running the Test Suite

pytest tests/

Key test files:

| Test file | What it tests |
| :---- | :---- |
| `tests/test_crawler.py` | Portal navigation and PDF retrieval |
| `tests/test_ocr.py` | OCR accuracy on sample scanned PDFs |
| `tests/test_mapper.py` | Indicator mapping against known examples |
| `tests/test_output.py` | Output schema validation |

---

## Reproducing the Sample Kit Results

To verify the tool against the provided Round 1 sample kit:

python evaluate.py \--sample-kit data/sample\_kit/ \--economy Singapore

This outputs a comparison report showing which known provisions were matched and which new ones were discovered.

---

## Team

| Role | Name | Responsibility |
| :---- | :---- | :---- |
| Technical Lead | \[Name\] | AI architecture, OCR, pipeline |
| Substantive Lead | \[Name\] | Legal/policy analysis, output QA |
| \[Additional member\] | \[Name\] | \[Responsibility\] |

---

## License

This project is released under the **Apache License 2.0** in accordance with the hackathon submission requirements.

See [LICENSE](http://LICENSE) for the full text.

---

## Key Dates

| Date | Milestone |
| :---- | :---- |
| 1–5 June 2026 | Substantive workshops (RDTII framework, legal text decoding) |
| 11–15 June 2026 | Technical workshops (RAG, OCR stack, responsible AI) |
| **20 July 2026** | **Round 1 submission deadline — all 44 shortlisted teams submit** |
| 31 July 2026 | 20 teams shortlisted announced (from 44 submissions) |
| 3 August 2026 | Live online pitching session (20 teams) |
| 5 August 2026 | 5 finalists announced — advance to Bangkok finale |
| October 2026 | Grand Finale — Bangkok, pitching and award ceremony |

Your submission must be complete and functional by **20 July 2026 midnight**. Late submissions will not be reviewed.

---

## Acknowledgements

Built as part of the UN Global Hackathon on AI for Digital Trade Regulatory Analysis, organised by UNESCAP and KMITL.  
