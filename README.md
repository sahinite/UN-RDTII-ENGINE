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
| `LLM_PROVIDER` | Which AI reads the law | `openai` **(recommended — gpt-4o)** |
| `LLM_API_KEY` | Your key for that AI | get it from platform.openai.com |
| `LLM_MODEL` | *(Optional)* Pick a specific model — leave blank to use the provider's default | `gpt-4o` |
| `MISTRAL_API_KEY` | Reading **scanned** PDFs cheaply (~$0.10 per 100 pages) | console.mistral.ai |

> Prefer a different AI? Set `LLM_PROVIDER` to `anthropic`, `deepseek`, `groq`, `qwen`,
> or `ollama` and put its key in `LLM_API_KEY`. (See [Switch the AI](#common-questions).)

> No cloud keys at all? The engine still works fully offline using free local tools
> (Ollama for the AI, Tesseract for scans). It's slower but needs no accounts.

---

## Setup (one time)

```bash
# 1. Get the code ready
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Install the helper tools (Tesseract, models). Answer the prompts.
python setup.py

# 3. Add your keys
cp .env.example .env
```

Now open `.env` in any text editor and fill in these lines, for example:

```bash
LLM_PROVIDER=openai               # which AI to use (recommended: OpenAI gpt-4o)
LLM_API_KEY=sk-...                # your key for that AI
# LLM_MODEL=gpt-4o                # optional: leave off to use the provider's default
MISTRAL_API_KEY=...               # optional: cheap OCR for scanned PDFs
```

That's it — you're ready to run.

---

## Run it

**Run on a law you already have (a PDF on your computer):**

```bash
python main.py --economy Malaysia --pillar 6 --pdf path/to/law.pdf
```

**Or let it find the law online for an economy:**

```bash
python main.py --economy Singapore --pillar 7
```

- `--economy` — the country/economy (Singapore, Australia, Malaysia).
- `--pillar` — which topic to check (6 = cross-border data, 7 = data protection).
- `--pdf` — optional; point it at a PDF to skip the online search.

While it runs you'll see a live checklist (finding → reading → understanding → writing).
Scanned PDFs are read automatically — no extra steps.

**When it finishes**, look in the `outputs/` folder for two files:

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
ollama pull qwen2.5:7b        # download a free local AI (~5 GB, one time)
export LLM_PROVIDER=ollama    # tell the engine to use it
python main.py --economy Singapore --pillar 7
```

Everything runs on your machine, so there are trade-offs to expect:

- **Slower.** The local AI is the bottleneck — a run can take **several minutes to tens of
  minutes** vs seconds-to-minutes on a cloud key, and it's much slower on a laptop without a GPU.
- **A little less accurate.** Small local models miss more than gpt-4o/Claude, so expect a few
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

**How much does a run cost?** The AI does most of the work; OCR is tiny (~$0.10 for a
100-page scan). Every run writes a real cost breakdown to `logs/cost_report.json`.

**Switch the AI?** Change `LLM_PROVIDER` in `.env` (and put its key in `LLM_API_KEY`):
`openai` (recommended, gpt-4o) · `anthropic` · `deepseek` · `groq` · `qwen` · `ollama` (offline).
Each provider uses its own default model; set `LLM_MODEL` only if you want a specific one.

**Run the tests?**

```bash
pytest
```

---

## Output columns (the CSV)

| Column | Meaning |
|--------|---------|
| economy | Country/economy name |
| law_name | Title of the act |
| indicator_id | Which checkpoint it maps to (P6-I1 … P7-I5) |
| article | Section number in the law |
| discovery_tag | KNOWN (in the reference set) or NEW (newly found) |
| verbatim_snippet | The exact text from the law |
| mapping_rationale | Why the AI mapped it there |
| source_url | Where the law came from |
| confidence | AI confidence, 0–1 |
| notes | Flags for human review |

(Full 13-column schema and the JSON structure: see `data/output_schema_sample.json`.)

---

## Add a new economy

No coding — just one YAML file, then run:

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
- **LLM cascade** (`src/mapping/llm_client.py`): one provider pinned per run via
  `LLM_PROVIDER`; order is Anthropic → OpenAI → DeepSeek → Groq → Qwen → Ollama.
  Model names are pinned (no `latest` tags). Llama 3.3 is excluded (license).
- **Retrieval** picks English vs multilingual models per economy; non-English economies
  search the original text and skip full-document translation (Malaysia P7: ~50 min → ~7 min).
- **Run profiles** (`RUN_PROFILE`): `gate` (KNOWN-only, default) · `submit` (KNOWN + 3 NEW,
  use for submissions) · `explore` (aggressive NEW). Printed at run start.
- **Quality gate** (`QUALITY_GATE_MODE`): `off` (production default) · `warn` · `fail`;
  optional generic economy/pillar validation. `QUALITY_GATE_MIN_CONFIDENCE`
  controls the default `0.80` confidence threshold.

More detail lives in `CLAUDE.md` and `CONTEXT.md`.

---

## Known limitations

- Configured/validated for Pillars 6 & 7 and economies SG / AU / MY only.
- Anti-bot government sites can fail intermittently; the online search isn't 100%
  deterministic run-to-run (handing it a `--pdf` avoids this).
- KNOWN vs NEW tagging matches on act title + section; a garbled title falls back to NEW.
- One AI provider is pinned per run — a sustained outage aborts extraction (no mid-run switch).
- Without any OCR key, very poor scans fall to local Tesseract and may read less accurately.
- **Offline mode (Ollama)** is noticeably slower (minutes → tens of minutes, worse without a
  GPU), a bit less accurate, and needs a one-time ~5 GB model download — use a cloud key when
  speed or accuracy matters.

---

## License

Apache License 2.0 — see `LICENSE`. The offline models in the cascade (`qwen2.5:7b`,
`granite3-8b`) are Apache 2.0 licensed.
