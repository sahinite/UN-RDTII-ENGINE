# RDTII Extraction Engine

**UN Global Hackathon on AI for Digital Trade Regulatory Analysis**
Team: Galaxefi · Round 1 · Submission: 20 July 2026 · Demo: 3 August 2026

## What it does?

Reading trade laws by hand is slow. This tool does it for you:

1. **Finds** the right laws on a government's legal website (or you hand it a PDF).
2. **Reads** them — even scanned image PDFs — and turns them into clean text.
3. **Understands** them with AI and pins each relevant rule to a specific trade-policy
   checkpoint (an "RDTII indicator", Pillars 6 & 7 — cross-border data and data protection).
4. **Writes** the results to a spreadsheet (CSV) and a JSON file you can hand to reviewers.

---

## Before you start

You need **two** things:

1. **Python 3.10+** on your computer.
2. **One AI key** — either a cloud key (recommended) *or* nothing at all if you run fully
   offline (see [Run without any keys](#run-without-any-keys)).

You give the engine your AI key with **two simple settings**: `LLM_PROVIDER` (which AI)
and `LLM_API_KEY` (its key). That's all — one key for whichever provider you pick.

| Setting | What it's for | Example |
|---------|---------------|---------|
| `LLM_PROVIDER` | Which AI reads the law | `anthropic`, `openai`, `gemini`, `deepseek`, `groq`, `qwen`, or `ollama` |
| `LLM_API_KEY` | Your key for the selected AI | provider's API console |
| `LLM_MODEL` | *(Optional)* Pick a specific model — leave blank to use the provider's default | `gpt-5` |
| `MISTRAL_API_KEY` | Reading **scanned** PDFs cheaply (~$0.10 per 100 pages) | console.mistral.ai |

> Prefer a different AI? Set `LLM_PROVIDER` to `anthropic`, `gemini`, `deepseek`, `groq`, `qwen`,
> or `ollama` and put its key in `LLM_API_KEY`. (See [Switch the AI](#common-questions).)

> No cloud keys at all? The engine still works fully offline using free local tools
> (Ollama for the AI, Tesseract for scans). It's slower but needs no accounts.

---

## Setup (one time)

```bash
# 1. Get the code ready
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Install the helper tools (Tesseract and Playwright Chromium). Answer the prompts
#    if you want the optional embedding model or Ollama offline models pre-downloaded.
python setup.py

# 3. Add your keys
cp .env.example .env
```

Now open `.env` in any text editor and fill in these lines, for example:

```bash
LLM_PROVIDER=openai               # which AI to use (default: OpenAI gpt-5)
LLM_API_KEY=sk-...                # your key for that AI
# LLM_MODEL=gpt-5                 # optional: leave off to use the provider's default
MISTRAL_API_KEY=...               # optional: cheap OCR for scanned PDFs
```

That's it — you're ready to run.

---

## Run it

### Option A — the web app (easiest)

The UI is protected by Google Sign-In. For local development, you can skip Google
OAuth with the dev bypass:

```bash
# one time, if SECRET_ENCRYPTION_KEY is still blank
openssl rand -base64 32
```

Put the generated value in `.env`, then use either normal Google sign-in or the
local bypass.

**Local development without Google OAuth:**

```bash
AUTH_DEV_BYPASS=1
DEV_USER_EMAIL=you@example.com
SECRET_ENCRYPTION_KEY=<the value from openssl rand -base64 32>
```

**Normal Google sign-in:**

```bash
GOOGLE_OAUTH_CLIENT_ID=<your Google OAuth web client id>
SECRET_ENCRYPTION_KEY=<the value from openssl rand -base64 32>
ADMIN_EMAILS=you@example.com          # optional; shows the Configure tab
```

In Google Cloud Console, the OAuth client must be a **Web application** and its
**Authorized JavaScript origins** must include the exact URL you open in the
browser, for example `http://localhost` and `http://localhost:7860`.

Then start the UI:

```bash
python app.py
```

Open http://localhost:7860. From there you can:

- **Run** — pick an economy and a pillar, hit Run, and watch each pipeline step light
  up live. The Zone 2 steps sit inside a loop frame that shows which document
  (1 of 5, 2 of 5, …) is being processed and how far each one got. When the run
  finishes the steps collapse and a **run report** appears below (cost breakdown,
  KNOWN vs NEW acts, documents fetched/used) — it's also saved as
  `outputs/<user_hash>/<run_id>/<economy>_P<pillar>_<datetime>_runReport.md`. A
  **View Results** button jumps straight to that run's outputs. Runs are queued
  globally and use your saved provider/API key settings.
- **Results** — everything for one selected run, in sub-tabs: the generated CSV and
  JSON; a **Compare vs Round 1** table that highlights mismatched indicators (rows
  where the engine missed a Round 1 act, or found something Round 1 doesn't list);
  the saved **Run report**; and the **Cost** breakdown (LLM / OCR / embedding /
  crawling) — each signed-in user only sees their own runs. You can also cancel
  your own queued or running jobs here.
- **My Settings** — view your Google profile, save contact info, choose your LLM
  provider, and store provider API keys. Keys are encrypted in `data/users.db`.
- **Configure** — add a new economy or a new pillar without editing files by hand.
  This tab is visible only when your email is listed in `ADMIN_EMAILS`.

The header has a light/dark toggle; your choice is remembered in the browser.

### Option B — the command line

**Run on a law you already have (a PDF on your computer):**

```bash
python main.py --economy Malaysia --pillar 6 --pdf path/to/law.pdf
```

**Or let it find relevant laws online:**

```bash
python main.py --economy Singapore --pillar 7
```

- `--economy` — the country/economy (Singapore, Australia, Malaysia).
- `--pillar` — which topic to check (6 = cross-border data, 7 = data protection).
- `--pdf` — optional; point it at a PDF to skip the online search.
- `--output-dir` — optional output directory (default: `outputs/`).
- `--format` — `csv`, `json`, or `both` (default: `both`).

While it runs you'll see a live checklist (finding → reading → understanding → writing).
Scanned PDFs are read automatically — no extra steps.

**When it finishes**, look in the output directory for the requested file(s):

```
outputs/Malaysia_P6_<timestamp>.csv    ← open in Excel / Google Sheets
outputs/Malaysia_P6_<timestamp>.json   ← same data, for developers
```

Each row is one legal rule, showing the exact wording, which indicator it maps to,
a confidence score, and the source. Rows marked in the **notes** column are worth a
quick human check.

---

## Run without any keys

Fully offline, no accounts, no cost:

```bash
ollama serve &                    # keep the local server running
ollama pull qwen2.5:7b            # download a free local AI (~5 GB, one time)
export LLM_PROVIDER=ollama    # tell the engine to use it
python main.py --economy Singapore --pillar 7
```

Everything runs on your machine, so there are trade-offs to expect:

- **Slower.** The local AI is the bottleneck — a run can take **several minutes to tens of
  minutes** vs seconds-to-minutes on a cloud key, and it's much slower on a laptop without a GPU.
- **A little less accurate.** Small local models miss more than GPT-5/Claude, so expect a few
  more rows flagged for review.
- **First run downloads models** (~5 GB for the AI, plus OCR/embedding models) — one-time, but
  it needs disk space and a decent connection.
- **Scanned PDFs read with local Tesseract** (no Mistral key), which is weaker on poor scans.
- **Keep Ollama running** (`ollama serve`) in the background while the engine runs.

---

## Common questions

**Which economies work?** Singapore, Australia, and Malaysia are configured and tested.
Adding another is just a small YAML file — see [Add a new economy](#add-a-new-economy).

**How does it read scanned documents?** OCR (turning images into text) is cloud-first
and cheap: it uses **Mistral** in a single call for a whole PDF, falls back to Azure or a
vision AI if needed, and finally to free local **Tesseract** if you have no keys. You don't
choose — it picks the best available automatically.

**How much does a run cost?** The AI does most of the work; OCR is tiny (about $0.10 for a
100-page scan, depending on provider). Every run writes a cost breakdown to
`logs/cost_report.json` (or the directory selected by `RDTII_LOG_DIR`).

**Switch the AI?** Change `LLM_PROVIDER` in `.env` (and put its key in `LLM_API_KEY`):
`anthropic` · `openai` · `gemini` · `deepseek` · `groq` · `qwen` · `ollama` (offline).
Each provider uses its own default model; set `LLM_MODEL` only if you want a specific one.

Provider-specific variables such as `OPENAI_API_KEY` still override the shared
`LLM_API_KEY`. See `.env.example` for all supported settings.

**Run the tests?**

```bash
pytest
```

**Run multiple economies or pillars?** `batch_run.py` uses one isolated process lane per
economy by default, which avoids cross-run model state collisions:

```bash
python batch_run.py --economies Singapore Australia Malaysia --pillar 6 7
python batch_run.py --economies Singapore Australia Malaysia --pillar 6 7 --max-parallel 2
```

**Compare output with the Round 1 reference data?** The evaluator accepts the sample-kit
directory and can restrict the comparison to one pillar:

```bash
python evaluate.py --sample-kit data/sample_kit/ --economy Singapore
python evaluate.py --sample-kit data/sample_kit/ --economy Singapore --pillar 7
```

---

## Output columns (the CSV)

| Column | Meaning |
|--------|---------|
| economy | Country/economy name |
| law_name | Title of the act |
| law_number_ref | Official act/law number, when available |
| last_amended | Last amendment date, when available |
| indicator_id | Which checkpoint it maps to (P6-I1 … P7-I5) |
| article | Section number in the law |
| discovery_tag | KNOWN (in the reference set) or NEW (newly found) |
| location_reference | Page/section location in the source |
| verbatim_snippet | The exact text from the law |
| mapping_rationale | Why the AI mapped it there |
| source_url | Where the law came from |
| confidence | AI confidence, 0–1 |
| notes | Flags for human review |

The CSV has exactly 13 columns and is validated before writing. The JSON output also
contains document metadata, OCR quality, processing time, retrieval method, archive URL,
and surrounding context. See `data/output_schema_sample.json` for the full structure.

---

## Add a new economy

No Python changes are normally needed — add one schema-valid YAML file, then run:

```yaml
# economies/vietnam.yaml
economy_name: Vietnam
iso_code: VN
un_name: Viet Nam
script_type: latin        # "asian" for Thai/Chinese scripts
languages: [vi, en]
portals:
  - name: Ministry of Justice
    url: https://vbpl.vn
    type: primary
    anti_bot: none
    discovery: auto
    fetch: auto
```

```bash
python main.py --economy Vietnam --pillar 7
```

---

## Under the hood (for developers)

- **Zone 1 — discovery** (`src/crawler/`): finds acts via a per-portal strategy
  (`index` / `api` / `seed_only` / `auto`) declared in `economies/*.yaml`.
- **Zone 2 — mapping** (`src/fetcher/`, `src/ocr/`, `src/retrieval/`, `src/mapping/`):
  fetch → OCR/translate → hybrid RAG retrieval → LLM maps passages to indicators →
  `src/output/` writes CSV/JSON and validates URLs.
- **OCR cascade** (`src/ocr/processor.py`): Mistral (whole-doc → per-page) → Azure DI
  (if configured) → LLM-vision (the configured provider) → Tesseract/PaddleOCR floor.
- **LLM cascade** (`src/mapping/llm_client.py`): one provider is selected per run via
  `LLM_PROVIDER` (or auto-detected); order is Anthropic → OpenAI → Gemini → DeepSeek →
  Groq → Qwen → Ollama. A startup smoke check verifies that the selected path can return
  parseable JSON before extraction begins.
  Model names are pinned (no `latest` tags). Llama 3.3 is excluded (license).
- **Retrieval** picks English vs multilingual models per economy; non-English economies
  search the original text and skip full-document translation (Malaysia P7: ~50 min → ~7 min).
- **Run profiles** (`RUN_PROFILE`): `gate` (KNOWN-only, default) · `submit` (KNOWN + 3 NEW,
  use for submissions) · `explore` (aggressive NEW). Printed at run start.
- **Quality gate** (`QUALITY_GATE_MODE`): `off` (production default) · `warn` · `fail`;
  optional generic economy/pillar validation. `QUALITY_GATE_MIN_CONFIDENCE`
  controls the default `0.80` confidence threshold.
- **Output controls**: `--format csv|json|both` selects the written formats; `--output-dir`
  changes their destination. `RDTII_LOG_DIR` separates logs and cost reports for batch
  lanes.

More detail lives in `CLAUDE.md` and `CONTEXT.md`.

---

## Known limitations

- Configured/validated for Pillars 6 & 7 and economies SG / AU / MY only.
- Anti-bot government sites can fail intermittently; the online search isn't 100%
  deterministic run-to-run (handing it a `--pdf` avoids this).
- KNOWN vs NEW tagging matches on act title + section; a garbled title falls back to NEW.
- One provider is selected at startup, with the cascade available for call-level failures;
  a sustained outage can still abort extraction.
- Without any OCR key, very poor scans fall to local Tesseract and may read less accurately.
- **Offline mode (Ollama)** is noticeably slower (minutes → tens of minutes, worse without a
  GPU), a bit less accurate, and needs a one-time ~5 GB model download — use a cloud key when
  speed or accuracy matters.

---

## License

Apache License 2.0 — see `LICENSE`. The offline models in the cascade (`qwen2.5:7b`,
`granite3-dense:8b`) are Apache 2.0 licensed.
