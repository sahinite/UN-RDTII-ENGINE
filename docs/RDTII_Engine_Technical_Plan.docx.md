  
**UN GLOBAL HACKATHON — AI FOR DIGITAL TRADE REGULATORY ANALYSIS**

**RDTII Extraction Engine**

Technical Build Plan & Architecture Document  |  Version 2.0  |  June 2026

| What This Engine Does Accepts an economy \+ RDTII pillar as input. Autonomously crawls official government portals, extracts verbatim provisions, maps them to RDTII indicators (P6-I1 to P6-I5 and P7-I1 to P7-I5), and outputs a structured CSV and JSON file — replacing a process that currently requires 10+ researchers and 1-4 weeks per country. |
| :---- |

| Mandatory Economies | Pillars | Indicators | Submission | Live Demo |
| :---- | :---- | :---- | :---- | :---- |
| Australia, Singapore, Malaysia | P6 \+ P7 | 10 total | 20 July 2026 | 3 August 2026 |

# 

# **1\. Executive Summary**

The RDTII Extraction Engine is an end-to-end AI pipeline that automates ESCAP's manual RDTII data collection process for Pillars 6 and 7 across Singapore, Australia, and Malaysia. A single command — python run.py \--country SG \--pillar 6 — crawls official government portals, extracts verbatim legal provisions, maps them to RDTII indicators, and produces a judge-ready CSV and JSON output.

The engine addresses all nine failure modes identified by ESCAP and is built for both technical and policy judges — every row contains a clickable URL, exact article reference, and verbatim snippet verifiable against the source in seconds.

| Dimension | Our Approach | Differentiator |
| :---- | :---- | :---- |
| Discovery | Two-pass Zone 1: seed known URLs \+ independent NEW discovery | Maximises NEW provisions (20/40 accuracy points) |
| Extraction | RAG: article-level chunking \+ BM25/dense hybrid \+ reranking | \~90% token reduction vs full-document LLM calls |
| Cost | DeepSeek V3 for Zone 1; Claude Sonnet 4 for Zone 2 only | \~70% cost reduction vs all-Sonnet pipeline |
| Scalability | Per-economy YAML adapters with unlimited portals \+ auto-probe | Add any economy \= one YAML file, zero code changes |
| Resilience | Crawl4AI \+ currency check \+ URL validation \+ Wayback archiving | Zero broken URLs; no outdated laws in output |

# **2\. Problem Statement**

ESCAP's RDTII database tracks digital trade regulations across 48 Asia-Pacific economies. Updating it requires 10+ researchers spending 1-4 weeks per country: searching government portals, reading legislation, extracting provisions, and scoring against the RDTII framework. This process is slow, expensive, inconsistent, and impossible to scale at the pace of regulatory change.

## **2.1 Nine Automation Failure Modes — and Our Mitigations**

| Failure Mode | Root Cause | Our Mitigation |
| :---- | :---- | :---- |
| Incorrect legal citation | LLM paraphrases instead of quoting verbatim | Verbatim-only extraction enforced in prompt; post-extraction assertion checks snippet exists in raw context |
| Missing act/regulation | Crawler misses relevant laws | Two-pass discovery: Round 1 Database seed URLs \+ independent search |
| Misinterpretation of law | Generic prompts without scope rules | Per-indicator prompts with in-scope/out-of-scope rules and negative examples (e.g. Banking Act S.47 NOT in scope for P6-I1) |
| Broken/incorrect URLs | No live validation | HTTP GET on every URL before output; 404/timeout \= auto-rejected |
| Outdated/cancelled laws | No currency check | Two-stage check: portal cancellation scan \+ keyword detection; auto-searches for replacement law |
| Anti-bot/JS rendering | Static crawlers fail | Crawl4AI (Playwright-based) with randomised wait times |
| Scanned PDFs \+ images | Text extraction fails | Auto-detection: text PDFs to pdfplumber; scanned to Tesseract+OpenCV; images to OCR; consolidated volumes segmented |
| Hallucinated indicators | LLM invents indicator IDs | Taxonomy JSON hardcoded; startup validation against output template indicator reference sheet |
| Multilingual documents | English-only pipelines | Three-layer translation: portal keywords, act titles, full document text via DeepL API |

# **3\. Objectives**

* **Automate Zone 1 (Evidence Discovery) and Zone 2 (Intelligent Mapping) for P6 and P7 across Singapore, Australia, and Malaysia**

* Produce judge-ready output matching the official 13-column CSV schema and JSON envelope exactly

* Maximise NEW provision discovery — the single largest scoring differentiator (20/40 accuracy points)

* Handle all document types: text-native PDFs, scanned PDFs, HTML portals, images, consolidated volumes, and multilingual documents

* Deliver modular architecture: any LLM or OCR engine swappable via .env config without rewriting core pipeline

* Produce measured cost data per document run via cost\_logger.py as required by the hackathon rubric

* Design for scalability: adding any new economy requires only a YAML adapter file, zero code changes

# **4\. Competition Scope**

## **4.1 Economies — Round 1**

| Economy | Primary Portal | Language | Points | Special Requirement |
| :---- | :---- | :---- | :---- | :---- |
| Singapore | sso.agc.gov.sg | English | 10 | JS-rendered; Playwright required; anti-bot handling |
| Australia | legislation.gov.au | English | 10 | Text-native PDFs; well-structured; large act library |
| Malaysia | agc.gov.my \+ federalgazette.agc.gov.my | English / Bahasa | 20 | Error-check existing Round 1 DB entries \+ new discovery; bilingual docs |

*Note: Thailand, India, and Indonesia are Final Round only. They are not in scope for the 20 July submission.*

## **4.2 Indicator Scope — 10 Indicators Total (P6 \+ P7 Only)**

| Indicator ID | Name | Legal Question |
| :---- | :---- | :---- |
| P6-I1 | General prohibition / restriction | Does the law restrict cross-border transfer of personal data as a default? |
| P6-I2 | Adequacy standard | Can data be transferred to countries deemed to have adequate protection? |
| P6-I3 | Contractual safeguards | Are standard contractual clauses or BCRs accepted as transfer mechanisms? |
| P6-I4 | Consent exception | Can transfer proceed with individual consent? |
| P6-I5 | Other exceptions | What other lawful bases permit cross-border transfer (vital interests, public interest)? |
| P7-I1 | Legal basis for processing | Does the law require a lawful basis for collecting/processing personal data? |
| P7-I2 | Purpose limitation | Is data restricted to the purpose for which it was collected? |
| P7-I3 | Data subject rights | Do individuals have rights to access, correct, or delete their data? |
| P7-I4 | Data breach notification | Is there a mandatory data breach notification requirement? |
| P7-I5 | Enforcement & penalties | Is there a supervisory authority and penalty regime? |

*Indicator IDs follow the output template exactly. These are hardcoded in taxonomy.json and validated at engine startup against the template's Indicator Reference sheet. Zone 3 scoring is intentionally excluded — it remains with a human researcher per hackathon spec.*

# **5\. System Architecture**

## **5.1 End-to-End Pipeline**

| Step | Component | Action | Output |
| :---- | :---- | :---- | :---- |
| 1 | CLIrun.py | python run.py \--country SG \--pillar 6\. Accepts ISO code, full name, or variation. Normalises to ISO at entry. | Config object; taxonomy.json loaded; economy YAML loaded |
| 2 | Economy Adaptereconomies/sg.yaml | Load portal list, language, translation config, crawl strategy | Ranked portal list for this economy |
| 3 | Zone 1 — Auto-Probecrawler/probe.py | Send indicator keywords to each portal search. Skip portals returning zero results. | Active portals only |
| 4 | Zone 1 — Crawl4AI Crawlercrawler/crawler.py | Crawl active portals. 2-tier depth, domain-locked. Extract act titles, descriptions, URLs. | Candidate act list |
| 5 | Zone 1 — Currency Checkcrawler/currency.py | Verify each act is still in force. Detect cancellation. Auto-search for replacement. | In-force acts only; cancelled flagged |
| 6 | Zone 1 — Rankercrawler/ranker.py | Semantic similarity \+ BM25 \+ exclusion filter \+ LLM gate (DeepSeek V3). Score on title \+ 500-char preamble. | Top 3-5 acts with PASS/FAIL |
| 7 | Zone 2 — Fetcher+Routerfetcher/router.py | Download act. Detect: text PDF / scanned PDF / HTML / image. Route to correct extractor. | Plain text per document |
| 8 | Zone 2 — Segmenterfetcher/segmenter.py | Detect consolidated volumes (200+ pages or multiple act headers). Split by act boundary. | Individual law segments |
| 9 | Zone 2 — Translationfetcher/translator.py | If non-English: translate full text via DeepL. Store original in verbatim\_original. | English text; original preserved |
| 10 | Zone 2 — RAG Pipelineretrieval/rag.py | Chunk by article boundary. Embed. BM25+dense hybrid. Cross-encoder rerank. Return top-5 chunks. | Top-5 relevant chunks per indicator |
| 11 | Zone 2 — LLM Extractormapping/mapper.py | Claude Sonnet 4 on top-5 chunks with per-indicator prompt. Extract verbatim, article ref, rationale, coverage. | Structured provision JSON |
| 12 | Zone 2 — Validatoroutput/validator.py | Live HTTP GET on source URL. Wayback Machine archive. OCR correction pass. | Validated URL \+ archive URL |
| 13 | Output Writeroutput/writer.py | Assemble 13-column CSV \+ JSON envelope. Validate all required fields. Flag incomplete rows. | outputs/{Economy}\_P{pillar}\_{ts}.csv \+ .json \+ logs/ |

## **5.2 Zone 1 — Two-Pass Discovery Strategy**

Zone 1 runs two passes to maximise both KNOWN validation and NEW discovery:

* **Pass 1 — KNOWN: Seed Zone 1 with all URLs from the Round 1 Database and Sample Government Portals CSV. Extract from all known laws first. Every row gets Discovery Tag \= KNOWN.**

* **Pass 2 — NEW: Re-run Zone 1 with broader search terms. Any act found outside the seed list becomes a candidate for Discovery Tag \= NEW.**

This two-pass approach is the primary mechanism for scoring on the 20/40 NEW discovery points — the single largest differentiator in the hackathon rubric.

## **5.3 Law Currency Check**

| Scenario | Engine Action | Output |
| :---- | :---- | :---- |
| Law still in force | URL validated. Document fetched. Proceeds normally. | Normal output row |
| Cancelled, replacement found | Parse cancellation notice. Fetch replacement URL. Validate. | Row with replacement law \+ note |
| Cancelled, no replacement | Re-search portal. LLM identifies current equivalent. Record gap if not found. | Flag for Review \= Yes |
| Sectoral law (no horizontal) | Record sectoral law. Also search for horizontal law. | Sectoral row \+ note on absence of horizontal |

## **5.4 Zone 2 — RAG Pipeline Detail**

| Step | Method | Tool | Purpose |
| :---- | :---- | :---- | :---- |
| Chunking | Split by article/section boundary (not fixed tokens) | PyMuPDF \+ regex heading detection | Preserves legal structure — S.26 stays intact |
| Embedding | sentence-transformers/all-MiniLM-L6-v2 | Local (no API cost) | Semantic vectors for dense retrieval |
| BM25 search | rank\_bm25 on chunked text | rank\_bm25 library | Keyword-exact matching for legal terms |
| Dense search | Cosine similarity on embeddings | numpy | Semantic matching for indicator legal question |
| Fusion | Reciprocal Rank Fusion (BM25 \+ dense) | Custom | Merged ranked list from both retrieval methods |
| Reranking | cross-encoder/ms-marco-MiniLM-L-6-v2 | Hugging Face | Scores top-20, returns top-5 chunks to LLM |
| LLM extraction | Per-indicator prompt on top-5 chunks | Claude Sonnet 4 | Verbatim extraction, mapping rationale, article ref |

## **5.5 Document Processing Router**

| Document Type | Detection Method | Processing Path |
| :---- | :---- | :---- |
| Text-native PDF | pdfplumber returns text on first attempt | pdfplumber — section hierarchy preserved |
| Scanned/image PDF | pdfplumber returns empty text | OpenCV preprocessing \+ Tesseract 5.3 (target CER \< 5%) |
| HTML portal page | Crawl4AI detects HTML response | BeautifulSoup DOM parser — article hierarchy \+ URL anchors extracted |
| Embedded images in HTML | Crawl4AI screenshot mode | Tesseract OCR on screenshot |
| Consolidated multi-law volume | 200+ pages OR multiple act headers detected | Document segmenter splits by act boundary using font-size detection |
| Non-English document | Language code in economy YAML | DeepL API translation before chunking; original stored in verbatim\_original |

# **6\. Technology Stack**

| Component | Technology | License | Rationale |
| :---- | :---- | :---- | :---- |
| CLI entry point | Python 3.11 \+ argparse | PSF | Single command. Accepts all country name variants. Normalises to ISO. |
| Web crawling | Crawl4AI (Playwright-based) | MIT | JS rendering, anti-bot, clean markdown, async, depth \+ domain control. No custom crawler needed. |
| Semantic ranking | sentence-transformers/all-MiniLM-L6-v2 | Apache 2.0 | Local, free, fast. Zone 1 ranking and Zone 2 embeddings. |
| BM25 keyword search | rank\_bm25 | Apache 2.0 | Keyword retrieval for Zone 1 and RAG hybrid search. |
| Reranking | cross-encoder/ms-marco-MiniLM-L-6-v2 | Apache 2.0 | Top-20 to top-5 for LLM extraction. |
| Text PDF extraction | pdfplumber | MIT | Best-in-class structured text with section hierarchy. |
| OCR — Stage 1 (Latin) | Tesseract 5.3 \+ OpenCV | Apache 2.0 | Default for Latin-script economies. Target CER \< 5%. Selected via economy YAML. |
| OCR — Stage 1 (Asian scripts) | PaddleOCR | Apache 2.0 | Thai, Lao, Mandarin, Hindi. Auto-selected via economy YAML when language is non-Latin. |
| OCR — Stage 2 fallback (quality) | Azure Document Intelligence | Commercial | Auto-triggered when CER \>= 5%. Complex layouts, degraded scans. Requires AZURE\_DI\_KEY. |
| OCR — Stage 2 fallback (mixed-lang) | Mistral OCR | Commercial | Auto-triggered when CER \>= 5% on mixed-language documents. Requires MISTRAL\_API\_KEY. |
| HTML parsing | BeautifulSoup4 \+ Crawl4AI | MIT | Article-level DOM parsing. URL anchors for location\_reference. |
| Document segmentation | PyMuPDF (fitz) | AGPL | Splits consolidated volumes by act boundary. |
| LLM — Zone 1 gate | DeepSeek V3 / Qwen 2.5 via Groq | MIT / Apache 2.0 | Free tier, binary PASS/FAIL relevance gate. |
| LLM — Zone 2 extraction (primary) | Claude Sonnet 4 (pinned: claude-sonnet-4-20250514) | Commercial | Highest precision for verbatim legal extraction. |
| LLM — Zone 2 fallback 1 | OpenAI GPT-4o | Commercial | If Anthropic API fails or rate-limits during demo. |
| LLM — Zone 2 fallback 2 (offline) | Qwen 2.5 7B via Ollama | Apache 2.0 | No internet required. Demo day contingency. |
| LLM — Zone 2 fallback 3 (offline) | IBM Granite 3.0 8B via Ollama | Apache 2.0 | Lightweight offline option for low-resource hardware. |
| Translation | DeepL API \+ Google Translate fallback | Commercial | Three-layer: keywords, titles, full document. |
| URL archiving | Wayback Machine API | Free | Snapshot every URL for citation preservation post-submission. |
| Output format | pandas \+ Python stdlib | BSD/PSF | 13-column CSV \+ JSON envelope. |
| Cost logging | cost\_logger.py (custom) | Apache 2.0 | Measures actual API costs per document. Required by rubric. |
| Project license | Apache 2.0 | Apache 2.0 | Required by hackathon submission rules. |

## **6.1 LLM Routing — Priority Order**

| Priority | Provider | Model | License | Use Case |
| :---- | :---- | :---- | :---- | :---- |
| 1 — Primary | Anthropic | Claude Sonnet 4 (claude-sonnet-4-20250514) | Commercial | Zone 2 extraction — highest verbatim accuracy |
| 2 — Cloud fallback | OpenAI | GPT-4o | Commercial | If Anthropic API fails or rate-limits |
| 3 — Free cloud | Groq | DeepSeek V3 / Qwen 2.5 72B | MIT / Apache 2.0 | Zone 1 relevance gate; cost reduction |
| 4 — Offline primary | Ollama | Qwen 2.5 7B | Apache 2.0 | No internet; demo day contingency |
| 5 — Offline lightweight | Ollama | IBM Granite 3.0 8B | Apache 2.0 | Low-resource hardware; Apache 2.0 compliant |

*Note: Llama 3.3 uses Meta's custom license — not Apache 2.0. For open-source compliance, use Qwen 2.5 or IBM Granite as the offline fallback.*

*Auto-cascade logic: the engine automatically tries the next provider if the active one fails (rate limit, API timeout, network error). Judges set ONE active provider in .env at setup — no manual switching needed during a run.*

**Auto-Cascade Code Design**

| \# src/llm/client.py — automatic fallback, no manual intervention PROVIDER\_CASCADE \= \[   ("anthropic", "claude-sonnet-4-20250514"),  \# Priority 1   ("openai",    "gpt-4o"),                    \# Priority 2   ("groq",      "deepseek-v3"),               \# Priority 3   ("ollama",    "qwen2.5:7b"),                \# Priority 4 — offline   ("ollama",    "granite3-dense:8b"),          \# Priority 5 — offline lightweight \] def call\_llm(prompt):     for provider, model in PROVIDER\_CASCADE:         try:             return call\_provider(provider, model, prompt)         except (RateLimitError, APIError, TimeoutError):             log(f'Provider {provider} failed, trying next...')     raise Exception('All LLM providers exhausted') |
| :---- |

## **6.2 Complete .env.example — All Variables**

This is the complete .env.example file shipped with the engine. Judges copy it, fill in their API keys, and run. No other configuration needed.

| \# \============================================================ \# RDTII Extraction Engine — .env.example \# Copy to .env and fill in your API keys before running \# \============================================================ \# ── LLM CONFIGURATION ─────────────────────────────────────── \# Set ONE active provider. All API keys are optional — \# the engine auto-cascades to the next available provider. \# Priority 1 (active by default): Claude Sonnet 4 LLM\_PROVIDER=anthropic LLM\_MODEL=claude-sonnet-4-20250514 ANTHROPIC\_API\_KEY=sk-ant-...        \# required if using anthropic \# Priority 2: OpenAI GPT-4o (auto-fallback if Anthropic fails) OPENAI\_API\_KEY=sk-...               \# optional — leave blank to skip \# Priority 3: DeepSeek V3 via Groq (free tier fallback) GROQ\_API\_KEY=gsk\_...               \# optional — leave blank to skip \# Priority 4+5: Offline models via Ollama (no internet required) \# Install Ollama \+ pull model: ollama pull qwen2.5:7b \# No API key needed for offline models \# ── OCR CONFIGURATION ─────────────────────────────────────── \# Global default OCR engine (Latin-script economies). \# Non-Latin economies (Thai, Lao, Chinese) auto-use PaddleOCR \# via economy YAML — no change needed here. OCR\_ENGINE=tesseract               \# default: Apache 2.0, free \# Stage 2 quality fallback API keys (triggered when CER \>= 5%) \# Leave blank to skip that fallback — engine flags row for review instead AZURE\_DI\_KEY=...                   \# optional: Azure Document Intelligence AZURE\_DI\_ENDPOINT=https://...      \# required only if AZURE\_DI\_KEY is set MISTRAL\_API\_KEY=...                \# optional: Mistral OCR (mixed-language) \# ── TRANSLATION CONFIGURATION ─────────────────────────────── DEEPL\_API\_KEY=...                  \# optional: DeepL translation (recommended) \# If blank, falls back to Google Translate (free, lower quality) \# ── OUTPUT CONFIGURATION ──────────────────────────────────── OUTPUT\_DIR=outputs/                \# where CSV \+ JSON files are saved LOG\_LEVEL=INFO                     \# DEBUG for verbose, INFO for normal |
| :---- |

*Auto-cascade behaviour: if ANTHROPIC\_API\_KEY is set, engine uses Claude Sonnet 4\. If that fails at runtime (rate limit, timeout), it automatically tries OpenAI (if OPENAI\_API\_KEY is set), then Groq (if GROQ\_API\_KEY is set), then Ollama offline. Judges never need to intervene mid-run.*

# **7\. Economy Adapter System**

Each economy is defined by a single YAML file. No code changes are required to add a new economy, add new portals, change translation settings, or configure special handling (e.g. Buddhist Era year conversion for Thailand).

| \# economies/sg.yaml — Latin script, English economy: Singapore language: en translation: false ocr\_engine: tesseract         \# Stage 1: Latin script — free crawl\_depth: 2 stop\_when: found\_and\_validated content\_types: \[html, pdf\] portals:   \- url: https://sso.agc.gov.sg     type: primary   \- url: https://statutes.agc.gov.sg     type: secondary   \# unlimited portals supported \# economies/th.yaml — Thai script, non-Latin economy: Thailand language: th translation: deepl ocr\_engine: paddleocr         \# Stage 1: Thai script — multilingual be\_year\_conversion: true crawl\_depth: 2 stop\_when: found\_and\_validated content\_types: \[html, pdf\] portals:   \- url: https://laws.go.th     type: primary \# NOTE: Stage 2 quality fallback (azure/mistral\_ocr) is automatic \# when CER \>= 5% — configured via AZURE\_DI\_KEY / MISTRAL\_API\_KEY in .env |
| :---- |

Portal relevance is determined at runtime via auto-probe: the engine sends indicator keywords to each portal search endpoint. Portals returning zero results are skipped without a full crawl. No manual pillar-to-portal tagging required.

## **7.1 Three-Layer Translation for Non-English Portals**

| Layer | What is Translated | When | Tool |
| :---- | :---- | :---- | :---- |
| 1 — Portal search | Indicator keywords translated to local language before sending search query | Before crawl | DeepL API; translations stored in economy YAML |
| 2 — Act titles | Act titles \+ 500-char descriptions translated after crawl, before ranking | After crawl | DeepL API; short strings only, minimal cost |
| 3 — Full document | Complete extracted text translated before chunking. Original stored in verbatim\_original. | After OCR/extraction | DeepL API; one call per document; Google Translate fallback |

| Final Round Economy | Language | Special Handling | YAML Config |
| :---- | :---- | :---- | :---- |
| Thailand | Thai | Buddhist Era year conversion (B.E. \- 543 \= CE) | translation: deepl; be\_year\_conversion: true |
| China | Mandarin | Multiple official portals; complex character set | translation: deepl; portals: \[multiple\] |
| India | Hindi \+ English | Multilingual single documents | translation: deepl; content\_types: \[pdf, html\] |
| Indonesia | Bahasa Indonesia | Multiple ministry portals | translation: deepl; portals: \[multiple ministries\] |
| Lao PDR | Lao script | Scanned-only; PaddleOCR recommended | translation: deepl; ocr\_engine: paddleocr |

# **8\. Output Schema**

## **8.1 CSV — Primary Output (13 Columns — Exact Template Match)**

| \# | Column | Required | Description |
| :---- | :---- | :---- | :---- |
| 1 | economy | Yes | Official UN country name |
| 2 | law\_name | Yes | Full official statute name and year. No abbreviations. |
| 3 | law\_number\_ref | Optional | Official act/law number |
| 4 | last\_amended | Yes | Year of most recent amendment |
| 5 | indicator\_id | Yes | Exact RDTII code: P6-I1, P6-I2 ... P7-I5 |
| 6 | article | Yes | Exact article and paragraph — e.g. Section 26(2) |
| 7 | discovery\_tag | Yes | NEW \= found independently; KNOWN \= matched sample kit |
| 8 | location\_reference | Optional | PDF: page number | HTML: URL anchor (e.g. \#s26) |
| 9 | verbatim\_snippet | Yes | Exact quoted text. No paraphrasing. No editing. |
| 10 | mapping\_rationale | Optional | Max 300 chars. This \[article\] \[prohibits/requires\] \[what\]. Maps to \[indicator\] because \[reason\]. |
| 11 | source\_url | Yes | Direct URL to law on official government portal. Live-validated. |
| 12 | confidence | Optional | Model certainty score 0.00-1.00 |
| 13 | notes | Optional | OCR issues, bilingual sources, translation notes, Wayback archive URL |

## **8.2 JSON Envelope — Supplementary Output**

| Field | Description |
| :---- | :---- |
| economy / law\_name / source\_url | Standard identification fields |
| archive\_url | Wayback Machine snapshot URL — citation preserved even if portal goes offline |
| ocr\_quality\_cer | Character Error Rate for scanned documents (target \< 0.05) |
| processing\_time\_seconds | Wall-clock time for this document |
| model\_version | Pinned string: e.g. claude-sonnet-4-20250514 \+ tesseract-5.3 |
| verbatim\_original | Original language text for non-English sources |
| raw\_context\_before | 2-3 sentences before verbatim snippet (anti-hallucination check) |
| raw\_context\_after | 2-3 sentences after verbatim snippet |
| provisions | Array of extracted provision objects matching CSV row structure |

# **9\. Quality Guardrails**

| Guardrail | Implementation | Prevents |
| :---- | :---- | :---- |
| Taxonomy startup validation | Load taxonomy.json; validate all IDs against template Indicator Reference sheet. Engine exits on mismatch. | Wrong indicator IDs; hallucinated indicators |
| Verbatim-only extraction | Extraction prompt forbids paraphrasing. Post-extraction assertion checks snippet in raw\_context. | Paraphrased snippets; judge point deductions |
| Live URL validation | HTTP GET before writing to output. 404/redirect/timeout \= auto-rejected. | Broken URLs in submission |
| Wayback archiving | archive.org/wayback API for every validated URL. Archive URL in Notes. | Links offline between submission (20 Jul) and demo (3 Aug) |
| OCR post-correction | Lightweight correction pass after Tesseract. Notes records OCR source. | Garbled verbatim text from scanned PDFs |
| Input normalisation | CLI accepts all country name formats. ISO lookup dict normalises. | Live demo crash on unexpected judge input |
| Two-row for non-consecutive sections | Non-adjacent related sections extracted as separate rows with same indicator ID. | Missing qualified provisions |
| Document segmentation | Consolidated volumes split by act boundary before RAG processing. | Cross-law chunk contamination |
| Malaysia two-pass | Pass 1: error-check existing Round 1 DB rows. Pass 2: new discovery. | Missing Malaysia-specific discrepancies worth 20 points |
| Confidence threshold flagging | confidence \< 0.80 on any row automatically adds to notes: Recommend human review — OCR or translation source. Low-confidence rows never silently pass to output. | Silent quality degradation from OCR errors or imprecise translations reaching judges without warning |
| Singapore PDPA-first gate | Phase 1 does not expand to other acts or indicators until Singapore PDPA (P7) end-to-end run is verified against Round 1 Database. | Building on an unverified foundation; cascading errors across all economies |

# **10\. Evaluation Metrics & Performance**

| Metric | Definition | Target |
| :---- | :---- | :---- |
| Field Accuracy | % of extracted fields matching Round 1 Database ground truth | \> 90% |
| Recall (KNOWN provisions) | % of Round 1 Database provisions successfully extracted by engine | \> 95% |
| Precision (all provisions) | % of engine-extracted provisions genuinely relevant to indicator | \> 85% |
| F1 Score | Harmonic mean of precision and recall | \> 90% |
| URL validity rate | % of source URLs returning HTTP 200 on live validation | 100% |
| OCR CER | Character Error Rate on scanned PDF test cases | \< 5% |
| Processing time | Wall-clock time per country \+ pillar run | \< 5 minutes |
| Cost per document | Measured actual API cost (from cost\_logger.py) | \< $0.10 / document |
| NEW discovery count | Valid NEW provisions found beyond Round 1 Database | Maximise |

| \# Evaluate accuracy against Round 1 Database python evaluate.py \--sample-kit data/sample\_kit/ \--economy Singapore \# Measure actual cost per document python tools/cost\_logger.py \--pdf data/benchmark/pdpa\_sg.pdf \--economy Singapore \--pillar 6 |
| :---- |

# **12\. Submission Deliverables**

| \# | Deliverable | Key Requirement | Phase |
| :---- | :---- | :---- | :---- |
| 1 | Functional Prototype | python run.py \--country SG \--pillar 6 in \< 5 min. Handles all document types. Switchable LLM \+ OCR. | 1-2 |
| 2 | Structured Output File | 13-column CSV \+ JSON envelope matching official template. Valid URLs. Verbatim snippets. Discovery Tags. | 1-2 |
| 3 | Technical Pitch Deck | 12 slides: Executive Summary, Problem, Objective, Architecture, Tech Stack, Backend Logic (x2), Evaluation, Demo, Innovation, Scalability, References. | 3 |
| 4 | Screen Recording | Max 10 min. Engine processes a scanned PDF. Correct citations with verbatim snippets and indicator mapping. | 3 |
| 5 | Live Demo (3 Aug) | Engine runs from terminal in real time. Handles reviewer-supplied country \+ pillar. No pre-loaded results. | 4 |

| Quick Start README — Mandatory Sections \# RDTII Extraction Engine\#\# Setup: git clone \+ pip install \-r requirements.txt \+ cp .env.example .env\#\# Run: python run.py \--country SG \--pillar 6\#\# Outputs: outputs/Singapore\_P6\_\[timestamp\].csv \+ .json \+ logs/run.log\#\# LLM Switching: LLM\_PROVIDER=groq LLM\_MODEL=deepseek-v3 (free open-source)\#\# OCR Switching: OCR\_ENGINE=azure | tesseract | paddleocr | mistral\_ocr\#\# Pinned versions: python==3.11, claude-sonnet-4-20250514, tesseract==5.3, crawl4ai==0.x |
| :---- |

# **13\. Innovation & Competitive Advantage**

| Differentiator | What We Do | Why It Matters |
| :---- | :---- | :---- |
| HTML as first-class source | BeautifulSoup DOM parser extracts article hierarchy and URL anchors (\#s26) from HTML portals. Every HTML row has a clickable location\_reference. | Judges explicitly called this out as a differentiator. Most teams handle PDFs. Few produce a clickable anchor landing on the exact provision. |
| Auto-probe multi-portal YAML | Portals probed dynamically at runtime. Relevant portals proceed; zero-result portals skipped. Unlimited portals per economy, zero manual tagging. | Adding economies \= one YAML file. Other teams hardcode portal logic and need code changes per country. |
| Two-pass NEW discovery | After extracting all KNOWN provisions from Round 1 Database, Zone 1 runs again with broader terms to find NEW provisions. | NEW provisions worth 20/40 accuracy points. Teams stopping at KNOWN cap at 50% accuracy score. |
| RAG on legal article structure | Chunked by article boundary. BM25 \+ dense hybrid \+ cross-encoder rerank. LLM sees only top-5 chunks. | \~90% token reduction vs full-document approach. Higher precision. Lower cost. Required architecture per hackathon spec. |
| Three-layer translation | Translates portal keywords, act titles, and full documents separately. Preserves original text in verbatim\_original. | Enables Final Round non-English economies with no code changes. Single-layer translation misses portal navigation. |
| Wayback Machine archiving | Every URL archived at extraction time. Archive URL in Notes. | Source links going offline between 20 July and 3 August \= unverifiable citations. Most teams won't archive. |

# **14\. Scalability & Future Development**

## **14.1 Adding New Economies — Zero Code Changes**

Every scalability decision is data-driven. Adding Thailand, China, India, Indonesia, or any other economy requires only a new YAML file in economies/. The engine reads all portal logic, language settings, and translation config from the YAML at runtime.

| Final Round Economy | Language | Key Challenge | YAML Config |
| :---- | :---- | :---- | :---- |
| Thailand | Thai | Buddhist Era years (B.E. \- 543 \= CE) | be\_year\_conversion: true; translation: deepl |
| China | Mandarin | Multiple official portals | portals: \[multiple\]; translation: deepl |
| India | Hindi \+ English | Multilingual single documents | content\_types: \[pdf, html\]; translation: deepl |
| Indonesia | Bahasa Indonesia | Multiple ministry portals | portals: \[multiple\]; translation: deepl |
| Lao PDR | Lao script | Scanned-only documents | ocr\_engine: paddleocr; translation: deepl |
| Timor-Leste | Tetum / Portuguese | Limited online availability | translation: deepl; portals: \[government gazette\] |

## **14.2 Future Capabilities**

* Real-time monitoring: Schedule engine runs to detect new laws and amendments as published on official portals

* GraphRAG extension: Relationship graph between provisions for cross-reference traversal (S.26 qualified by S.31)

* Expanded pillar coverage: P2-P5, P8-P12 via taxonomy JSON extension — indicator definitions and keyword sets

* ESCAP API integration: Direct output to ESCAP RDTII database via API, bypassing CSV/JSON intermediary

* Human-in-the-loop dashboard: Web interface for policy researchers to review, validate, and approve provisions before database insertion

# **16\. References**

| Resource | Used For | URL |
| :---- | :---- | :---- |
| ESCAP RDTII 2.1 Guide | Indicator definitions, scoring criteria, policy scope rules | https://www.unescap.org/kp/2025/regional-digital-trade-integration-index-rdtii-21-guide |
| ESCAP RDTII 2.1 Internal Guide | Implicit rules, FAQs, P6/P7 scoping decisions | Hackathon Knowledge Portal |
| Round 1 Database (AU, SG, MY) | Ground truth for accuracy evaluation; seed URLs for Zone 1 | Hackathon Knowledge Portal |
| OUTPUT\_TEMPLATE\_31MAY.xlsx | Exact 13-column CSV schema; indicator reference; submission rules | Hackathon Knowledge Portal |
| README\_template.md | Entry point, .env config, cost logger, batch run, output naming | Hackathon Knowledge Portal |
| Hackathon Overview — Dr. Witada A. | Scoring rubric (40/30/30), discovery tag rules, live demo requirements | Workshop 1 June 2026 |
| Canvas Design Workshop — Nikita S. | Automation failure modes, worked examples, output field requirements | Workshop 1 June 2026 |
| Crawl4AI | Web crawling library | https://github.com/unclecode/crawl4ai |
| sentence-transformers | Embeddings for ranking and RAG | https://www.sbert.net |
| Tesseract OCR 5.3 | Scanned PDF and image extraction | https://github.com/tesseract-ocr/tesseract |
| DeepSeek V3 | Zone 1 relevance gate LLM | https://api.deepseek.com |
| Claude Sonnet 4 | Zone 2 extraction LLM (pinned) | https://www.anthropic.com |
| DeepL API | Translation pipeline | https://www.deepl.com/api |
| Wayback Machine API | URL archiving | https://archive.org/help/wayback\_api.php |
| pdfplumber | Text-native PDF extraction | https://github.com/jsvine/pdfplumber |
| rank\_bm25 | BM25 keyword retrieval | https://github.com/dorianbrown/rank\_bm25 |

*Document Version: 2.0  |  Date: June 2026  |  Status: Final Technical Plan*

*Prepared for: UN Global Hackathon on AI for Digital Trade Regulatory Analysis, UNESCAP & KMITL*