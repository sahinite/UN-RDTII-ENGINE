"""
Environment screen: a grouped .env editor.

Only variables the current code actually reads are exposed — the old
probe/crawl/currency env vars in .env.example are dead (that pipeline was removed)
and would mislead if shown. Field kinds:
    "text"   — free text; blank means "leave the code default alone"
    "secret" — password input; the stored value is never surfaced back
    "select" — dropdown; the selection is always written on save
"""

from __future__ import annotations

import gradio as gr
from dotenv import set_key

from .utils import ENV_PATH, read_env_file

# Each field: (name, kind, default, help text, choices-for-select).
ENV_GROUPS: list[tuple[str, bool, list[tuple]]] = [
    ("LLM & providers", False, [
        ("LLM_PROVIDER", "select", "openai", "Provider the run pins",
         ["anthropic", "openai", "gemini", "deepseek", "groq", "qwen", "ollama"]),
        ("LLM_MODEL", "text", "gpt-5", "Model id (blank = provider default)", None),
        ("LLM_API_KEY", "secret", "", "Generic key for the pinned provider "
         "(a provider-specific key below overrides it)", None),
        ("ANTHROPIC_API_KEY", "secret", "", "Claude — tier 1", None),
        ("OPENAI_API_KEY", "secret", "", "OpenAI — tier 2", None),
        ("GEMINI_API_KEY", "secret", "", "Gemini — tier 3", None),
        ("GOOGLE_API_KEY", "secret", "", "Alternate Gemini key", None),
        ("DEEPSEEK_API_KEY", "secret", "", "DeepSeek — tier 4", None),
        ("GROQ_API_KEY", "secret", "", "Groq — tier 5 (free)", None),
        ("DASHSCOPE_API_KEY", "secret", "", "Qwen / DashScope — tier 6", None),
        ("OLLAMA_BASE_URL", "text", "http://localhost:11434", "Local Ollama server", None),
        ("OLLAMA_NUM_CTX", "text", "8192",
         "Context window — must fit the ~4.5k-token prompt", None),
        ("OLLAMA_TIMEOUT", "text", "", "Ollama request timeout (seconds)", None),
    ]),
    ("OCR", False, [
        ("OCR_ENGINE", "select", "tesseract",
         "Stage-1 OCR engine (usually auto per economy)",
         ["tesseract", "paddleocr", "azure", "mistral_ocr"]),
        ("AZURE_DI_KEY", "secret", "", "Azure Document Intelligence — Stage-2 fallback", None),
        ("AZURE_DI_ENDPOINT", "text", "", "Azure DI endpoint URL", None),
        ("MISTRAL_API_KEY", "secret", "", "Mistral OCR — Stage-2 fallback", None),
        ("MISTRAL_OCR_COST_PER_PAGE", "text", "", "Cost/page used in the cost log", None),
    ]),
    ("Translation", False, [
        ("DEEPL_API_KEY", "secret", "",
         "DeepL (Google Translate is the automatic fallback)", None),
        ("ARGOS_POOL_SIZE", "text", "", "Offline Argos translator worker pool", None),
        ("ARGOS_INTRA_THREADS", "text", "", "Threads per Argos worker", None),
        ("ARGOS_TIMEOUT", "text", "", "Argos translation timeout (s)", None),
    ]),
    ("Run control & quality gate", False, [
        ("RUN_PROFILE", "select", "gate",
         "gate = KNOWN-only · submit = + 3 NEW · explore = aggressive",
         ["gate", "submit", "explore"]),
        ("ZONE2_MAX_KNOWN_ACTS", "text", "12", "Ceiling on KNOWN seed acts per run", None),
        ("ZONE2_MAX_NEW_ACTS", "text", "",
         "Cap on speculative NEW acts (blank = from profile)", None),
        ("ALLOW_UNVERIFIED_SNIPPETS", "select", "false",
         "Keep rows whose snippet failed the verbatim check", ["false", "true"]),
        ("QUALITY_GATE_MODE", "select", "off", "Output quality gate", ["off", "warn", "fail"]),
        ("QUALITY_GATE_MIN_CONFIDENCE", "text", "0.80",
         "Min provision confidence for the gate (0–1)", None),
        ("DISCOVER_BUDGET_S", "text", "", "Zone 1 discovery time budget (seconds)", None),
    ]),
    ("Output, logging & archiving", False, [
        ("RDTII_LOG_DIR", "text", "logs", "Run logs + diagnostics directory", None),
        ("WAYBACK_BEST_EFFORT", "select", "true", "Wayback archiving is non-blocking",
         ["true", "false"]),
        ("WAYBACK_MAX_TRIES", "text", "", "Wayback save attempts", None),
        ("WAYBACK_RETRY_WAIT_S", "text", "", "Wait between Wayback retries (s)", None),
        ("LOCAL_ARCHIVE_FALLBACK", "select", "true",
         "Save a local snapshot when Wayback fails", ["true", "false"]),
        ("LOCAL_ARCHIVE_DIR", "text", "", "Local snapshot directory", None),
    ]),
    ("Advanced — retrieval & extraction tuning", True, [
        ("EMBED_MODEL_EN", "text", "", "English dense embedder", None),
        ("EMBED_MODEL_ML", "text", "", "Multilingual dense embedder", None),
        ("RERANK_MODEL_EN", "text", "", "English cross-encoder reranker", None),
        ("RERANK_MODEL_ML", "text", "", "Multilingual reranker", None),
        ("BM25_TOP_K", "text", "", "BM25 candidates", None),
        ("DENSE_TOP_K", "text", "", "Dense candidates", None),
        ("FUSION_TOP_K", "text", "", "RRF fused candidates", None),
        ("RERANK_TOP_N", "text", "", "Passages kept after rerank", None),
        ("NEW_SCORE_THRESHOLD", "text", "", "Min score for a NEW act", None),
        ("CHUNK_TARGET_CHARS", "text", "", "Target chunk size (chars)", None),
        ("CHUNK_MAX_CHARS", "text", "", "Max chunk size (chars)", None),
        ("CHUNK_HIERARCHY_MIN_COVERAGE", "text", "", "Chunk hierarchy coverage guard", None),
        ("PDF_X_TOLERANCE", "text", "", "pdfplumber x_tolerance (word-merge fix)", None),
        ("INDEX_FETCH_TIMEOUT_S", "text", "", "Index page fetch timeout (s)", None),
        ("TESSDATA_PREFIX", "text", "", "Tesseract language-data path", None),
    ]),
]

# Flat list in the exact order components are created — drives the save/reload wiring.
ENV_FIELDS_FLAT: list[tuple] = [field for _title, _advanced, fields in ENV_GROUPS
                                for field in fields]


def field_current_value(name: str, kind: str, default: str, choices) -> str:
    current = read_env_file().get(name)
    if kind == "secret":
        return ""                                  # never surface a stored secret
    if kind == "select":
        return current if current in (choices or []) else default
    return current if current else ""              # text: current, else blank


def secret_placeholder(name: str) -> str:
    return ("•••••• currently set — leave blank to keep"
            if read_env_file().get(name) else "not set — paste a value to add")


def text_placeholder(default: str) -> str:
    return f"default: {default}" if default else "optional — leave blank for the code default"


def save_env(*values) -> str:
    """Write the form to .env. Blank fields are left untouched (so unset secrets are
    kept and numeric code-defaults aren't clobbered with empty strings); dropdowns
    always write their selection."""
    if not ENV_PATH.exists():
        ENV_PATH.touch()
    before = read_env_file()
    written, secrets_kept = 0, 0
    for (name, kind, _default, _help, _choices), value in zip(ENV_FIELDS_FLAT, values):
        text = ("" if value is None else str(value)).strip()
        if kind == "secret":
            if text:
                set_key(str(ENV_PATH), name, text, quote_mode="never")
                written += 1
            elif before.get(name):
                secrets_kept += 1
        elif kind == "select":
            set_key(str(ENV_PATH), name, text, quote_mode="never")
            written += 1
        elif text:                                 # text: only write non-empty
            set_key(str(ENV_PATH), name, text, quote_mode="never")
            written += 1
    message = f"✅ Saved to `.env` — {written} variable(s) written"
    if secrets_kept:
        message += f", {secrets_kept} existing secret(s) kept"
    return message + ". Applies to the next pipeline run."


def reload_env() -> list:
    """Re-read .env into every field (for external edits)."""
    updates = []
    for name, kind, default, _help, choices in ENV_FIELDS_FLAT:
        if kind == "secret":
            updates.append(gr.update(value="", placeholder=secret_placeholder(name)))
        else:
            updates.append(gr.update(value=field_current_value(name, kind, default, choices)))
    return updates


def build_environment_screen() -> dict:
    """Lay out the Environment tab; returns the components the app wires together."""
    with gr.Tab("Environment"):
        gr.Markdown(
            "Set the environment variables the pipeline reads. **Save** writes them to "
            "`.env`, overwriting the ones you fill in. Blank fields are left unchanged — so "
            "a secret you don't retype is kept, and numeric defaults aren't wiped. Secrets "
            "are never shown back; the placeholder says whether one is already stored."
        )
        field_components = []
        for group_title, is_advanced, fields in ENV_GROUPS:
            with gr.Accordion(group_title, open=not is_advanced):
                for i in range(0, len(fields), 2):
                    with gr.Row():
                        for name, kind, default, help_text, choices in fields[i:i + 2]:
                            if kind == "select":
                                component = gr.Dropdown(
                                    choices, label=name, info=help_text,
                                    value=field_current_value(name, kind, default, choices))
                            elif kind == "secret":
                                component = gr.Textbox(
                                    label=name, info=help_text, type="password",
                                    value="", placeholder=secret_placeholder(name))
                            else:
                                component = gr.Textbox(
                                    label=name, info=help_text,
                                    value=field_current_value(name, kind, default, choices),
                                    placeholder=text_placeholder(default))
                            field_components.append(component)
        with gr.Row():
            save_button = gr.Button("Save to .env", variant="primary", scale=2)
            reload_button = gr.Button("Reload from .env", scale=1)
        status = gr.Markdown("")

    return {
        "field_components": field_components,
        "save_button": save_button,
        "reload_button": reload_button,
        "status": status,
    }
