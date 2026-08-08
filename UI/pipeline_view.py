"""
Live pipeline visual for the Run screen.

Two halves:
  * a run-state machine — `new_run_state()` + `update_run_state()` fold the
    progress lines main.py prints into a dict describing every stage/document;
  * a renderer — `render_pipeline()` turns that state into the three-phase HTML
    visual (Zone 1 prep → per-document loop frame → finalise).
"""

from __future__ import annotations

import re
from html import escape as esc

from .styles import PIPELINE_CSS
from .utils import shorten_document_label

# ── Stage definitions ─────────────────────────────────────────────────────────
# Three phases: two run once, the middle one repeats for every discovered document.

PREP_STAGES = [
    ("config",    "Economy config",   "Load YAML + taxonomy"),
    ("llm_init",  "LLM provider",     "Pin provider + smoke check"),
    ("seed",      "Round 1 seed",     "KNOWN acts and provisions"),
    ("discover",  "Zone 1 discovery", "Portal strategy → act list"),
]
LOOP_STAGES = [
    ("fetch",     "Fetch",     "Download + OCR"),
    ("translate", "Translate", "To English"),
    ("rag",       "Retrieve",  "BM25 + dense + rerank"),
    ("extract",   "Map",       "LLM → indicators"),
    ("validate",  "Validate",  "Verbatim + archive"),
]
FINAL_STAGES = [
    ("write",     "Write outputs", "CSV + JSON + cost report"),
]
LOOP_STAGE_IDS = [stage_id for stage_id, _, _ in LOOP_STAGES]
ALL_STAGE_IDS = [stage_id for stage_id, _, _ in PREP_STAGES + LOOP_STAGES + FINAL_STAGES]

# keyword → stage id (matched against progress lines from main.py)
STAGE_KEYWORDS = [
    ("Loading economy config", "config"), ("Economy config", "config"),
    ("Connecting to LLM", "llm_init"), ("Verifying LLM", "llm_init"),
    ("LLM ready", "llm_init"), ("LLM provider", "llm_init"),
    ("Loading Round 1 seed data", "seed"), ("Seed data —", "seed"),
    ("Zone 1 —", "discover"), ("Loading PDF", "discover"), ("PDF loaded", "discover"),
    ("Fetching —", "fetch"), ("Fetched —", "fetch"), ("Fetch failed", "fetch"),
    ("Translating", "translate"), ("Translation", "translate"),
    ("RAG retrieval", "rag"), ("RAG failed", "rag"),
    ("LLM extraction", "extract"),
    ("Validating output", "validate"), ("Validation", "validate"),
    ("Writing outputs", "write"), ("Outputs written", "write"),
]

_ELAPSED_SUFFIX = re.compile(r"\s(\d+(?:\.\d+)?s|\d+m\s*\d+s)\s*$")
_DOC_COUNTER = re.compile(r"\[(\d+)/(\d+)\]")


# ── Run state ─────────────────────────────────────────────────────────────────

def new_run_state(economy: str = "", pillar: int | None = None) -> dict:
    return {
        "economy": economy, "pillar": pillar,
        "phase": "idle",                 # idle | running | complete | failed
        "stages": {}, "times": {}, "notes": {},
        "docs": {},                      # index → {title, stage, status}
        "total_docs": 0, "current_doc": 0,
        "records": None, "cost": None, "elapsed": "",
        "acts_total": None, "acts_known": None, "acts_new": None,
        "provider": "", "csv_name": "",
    }


def update_run_state(line: str, state: dict) -> None:
    """Fold one ANSI-stripped progress line from main.py into the run state."""
    matched = next((sid for keyword, sid in STAGE_KEYWORDS if keyword in line), None)

    # Document count is announced before the loop starts.
    m_total = re.search(r"Zone 1 complete — (\d+) document", line)
    if m_total:
        state["total_docs"] = int(m_total.group(1))
    m_records = re.search(r"Outputs written — (\d+) records", line)
    if m_records:
        state["records"] = int(m_records.group(1))
    m_acts = re.search(r"Discovery complete — (\d+) act\(s\) \((\d+) KNOWN, (\d+) NEW\)", line)
    if m_acts:
        state["acts_total"], state["acts_known"], state["acts_new"] = \
            (int(g) for g in m_acts.groups())
    m_provider = re.search(r"LLM (?:provider|ready) — ([\w.\-]+/[\w.\-:]+)", line)
    if m_provider:
        state["provider"] = m_provider.group(1)

    if matched is None:
        return

    body = line.strip()
    kind = ("start" if body.startswith("→") else
            "fail" if body.startswith("✗") else
            "warn" if body.startswith("⚠") else
            "done" if body.startswith("✓") else "info")

    # Elapsed suffix ("1.4s" / "2m 3s") that Progress appends to done/warn/fail lines.
    m_elapsed = _ELAPSED_SUFFIX.search(body)
    if m_elapsed:
        state["times"][matched] = m_elapsed.group(1)
        body = body[: m_elapsed.start()].rstrip()

    m_doc = _DOC_COUNTER.search(body)
    doc_index = int(m_doc.group(1)) if m_doc else None
    if m_doc:
        state["total_docs"] = max(state["total_docs"], int(m_doc.group(2)))
        body = body.replace(m_doc.group(0), "").strip()

    # Detail text after the em dash ("Fetched — Privacy Act 1988" → "Privacy Act 1988").
    detail = body.split(" — ", 1)[1].strip() if " — " in body else ""
    if detail:
        state["notes"][matched] = detail[:60]

    status = {"start": "running", "done": "done", "warn": "warn",
              "fail": "fail", "info": "running"}[kind]

    if matched in LOOP_STAGE_IDS:
        state["phase"] = "running"
        stage_index = LOOP_STAGE_IDS.index(matched)
        if doc_index is not None:
            # A new document restarts the loop row — this is what makes the
            # iteration visible rather than a row that only ever fills up once.
            if doc_index != state["current_doc"]:
                for sid in LOOP_STAGE_IDS:
                    state["stages"][sid] = "pending"
                    state["times"].pop(sid, None)
                    state["notes"].pop(sid, None)
                for prev_index, prev_doc in state["docs"].items():
                    if prev_index < doc_index and prev_doc["status"] == "running":
                        prev_doc["status"] = "done"
                state["current_doc"] = doc_index
            doc = state["docs"].setdefault(
                doc_index, {"title": "", "stage": matched, "status": "running"})
            doc["stage"] = matched
            # Only a successful fetch names the document — on failure the detail
            # is the error text ("timeout"), which must not become the label.
            if matched == "fetch" and detail and kind in ("start", "done"):
                doc["title"] = shorten_document_label(detail)
            if kind == "fail":
                doc["status"] = "warn"      # the loop continues to the next document
            elif matched == "validate" and kind == "done":
                doc["status"] = "done"
        # Earlier loop stages for THIS document are finished.
        for prev in LOOP_STAGE_IDS[:stage_index]:
            if state["stages"].get(prev) in (None, "pending", "running"):
                state["stages"][prev] = "done"
        # A per-document failure is a warning for the stage, not a dead run.
        state["stages"][matched] = "warn" if kind == "fail" else status
        return

    # Once-only stages: everything before them is complete by definition.
    state["phase"] = "running"
    stage_index = ALL_STAGE_IDS.index(matched)
    for prev in ALL_STAGE_IDS[:stage_index]:
        if state["stages"].get(prev) == "running":
            state["stages"][prev] = "done"
    if kind == "warn" and state["stages"].get(matched) == "done":
        return
    state["stages"][matched] = status


# ── Rendering ─────────────────────────────────────────────────────────────────

_STATUS_ICONS = {"pending": "", "running": "", "done": "✓", "warn": "!", "fail": "✕"}


def _stage_node(stage_id: str, name: str, hint: str, state: dict) -> str:
    status = state["stages"].get(stage_id, "pending")
    note = state["notes"].get(stage_id) or hint
    time_text = state["times"].get(stage_id, "")
    return (
        f'<div class="rdp-node {status}">'
        f'<div class="top"><span class="rdp-dot">{_STATUS_ICONS[status]}</span>'
        f'<span class="rdp-t">{time_text}</span></div>'
        f'<div class="rdp-name">{esc(name)}</div>'
        f'<div class="rdp-note" title="{esc(note)}">{esc(note)}</div></div>'
    )


def _stage_row(stages: list[tuple[str, str, str]], state: dict) -> str:
    parts = []
    for i, (stage_id, name, hint) in enumerate(stages):
        if i:
            prev_done = state["stages"].get(stages[i - 1][0]) in ("done", "warn")
            parts.append(f'<div class="rdp-conn{" on" if prev_done else ""}"></div>')
        parts.append(_stage_node(stage_id, name, hint, state))
    return f'<div class="rdp-row">{"".join(parts)}</div>'


def _hero_bar(state: dict) -> str:
    total = state["total_docs"]
    done_docs = sum(1 for d in state["docs"].values() if d["status"] in ("done", "warn"))
    phase = state["phase"]
    phase_label = {"idle": "Idle", "running": "Running",
                   "complete": "Complete", "failed": "Failed"}[phase]
    where = f"{state['economy']} · Pillar {state['pillar']}" if state["economy"] else "No run yet"

    meta = ""
    if total:
        meta += f"<div>Documents<b>{done_docs} / {total}</b></div>"
    if state["records"] is not None:
        meta += f"<div>Records<b>{state['records']}</b></div>"
    if state["cost"]:
        meta += f"<div>Cost<b>{state['cost']}</b></div>"
    if state["elapsed"]:
        meta += f"<div>Elapsed<b>{state['elapsed']}</b></div>"

    return (
        f'<div class="rdp-hero"><span class="rdp-pill {phase}"><i></i>{phase_label}</span>'
        f'<span class="rdp-title">{esc(where)}</span>'
        f'<div class="rdp-meta">{meta}</div></div>'
    )


def _document_chips(state: dict) -> str:
    if not state["docs"]:
        return ""
    stage_names = {stage_id: name for stage_id, name, _ in LOOP_STAGES}
    items = []
    for i in sorted(state["docs"]):
        doc = state["docs"][i]
        label = doc["title"] or f"Document {i}"
        status = doc["status"]
        tail = (stage_names.get(doc["stage"], "") if status == "running"
                else "done" if status == "done" else "issue")
        items.append(
            f'<div class="rdp-chip {status}"><span class="n">{i}</span>'
            f'<span class="tt" title="{esc(label)}">{esc(label)}</span>'
            f'<span class="st">{esc(tail)}</span></div>'
        )
    for i in range(len(state["docs"]) + 1, state["total_docs"] + 1):
        items.append(f'<div class="rdp-chip"><span class="n">{i}</span>'
                     f'<span class="st">queued</span></div>')
    return f'<div class="rdp-docs">{"".join(items)}</div>'


def _loop_frame(state: dict) -> str:
    total = state["total_docs"]
    done_docs = sum(1 for d in state["docs"].values() if d["status"] in ("done", "warn"))
    progress_pct = int(done_docs / total * 100) if total else 0
    loop_active = any(state["stages"].get(s) == "running" for s in LOOP_STAGE_IDS)

    counter = (
        f'<span class="rdp-counter">Document <b>{state["current_doc"] or 0}</b> '
        f'of <b>{total}</b></span>'
        if total else '<span class="rdp-counter">awaiting document list</span>')

    return (
        f'<div class="rdp-loop{" active" if loop_active else ""}">'
        f'<div class="rdp-loopbar"><span class="rdp-badge"><b>↻</b> Repeats per document</span>'
        f'{counter}<div class="rdp-track">'
        f'<div class="rdp-fill" style="width:{progress_pct}%"></div></div></div>'
        f'{_stage_row(LOOP_STAGES, state)}{_document_chips(state)}</div>'
    )


def render_pipeline(state: dict) -> str:
    """The full three-phase pipeline visual as themed HTML."""
    def section(title: str, body: str) -> str:
        return (f'<div class="rdp-sec"><div class="rdp-lbl"><span>{title}</span></div>'
                f'{body}</div>')

    return (
        PIPELINE_CSS + '<div class="rdp">' + _hero_bar(state)
        + section("Zone 1 · Evidence discovery", _stage_row(PREP_STAGES, state))
        + section("Zone 2 · Intelligent mapping — loop", _loop_frame(state))
        + section("Finalise", _stage_row(FINAL_STAGES, state))
        + '</div>'
    )
