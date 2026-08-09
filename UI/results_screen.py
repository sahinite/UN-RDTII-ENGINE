"""
Results screen: everything about one selected run — generated CSV/JSON, the
per-indicator comparison against the Round 1 database (mismatched rows
highlighted), the saved run report, the per-run cost visual, and downloads.
"""

from __future__ import annotations

from html import escape

import gradio as gr
import pandas as pd

from .reports import render_cost_report, run_report_markdown_text
from .utils import (
    ROUND1_DB,
    cost_report_path,
    find_run_csv,
    list_runs,
    load_taxonomy,
    parse_run_name,
    read_json,
    run_report_path,
)
from src.auth import db
from src.queue import scheduler


def render_results_empty_state(message: str = "No results available yet.") -> str:
    return (
        '<div class="rd-results-empty" role="status">'
        '<div class="rd-results-empty-box">'
        '<div class="rd-results-empty-icon" aria-hidden="true"></div>'
        '<h3>No results yet</h3>'
        f'<p>{escape(message)}</p>'
        '</div></div>'
    )


def empty_results_payload(message: str = "No results available yet.") -> tuple:
    """Outputs shared by initial load, sign-out, and an empty run list."""
    empty = pd.DataFrame()
    return (
        empty,
        {},
        None,
        empty,
        empty,
        "",
        render_cost_report(None),
        "",
        gr.update(value=render_results_empty_state(message), visible=True),
        gr.update(visible=False),
    )


def load_run(csv_name: str, ctx=None):
    """Everything the Results tabs render for one run:
    data outputs followed by empty-state and content visibility updates."""
    empty = pd.DataFrame()
    if ctx is None:
        return empty_results_payload("Sign in to view your runs.")
    if not csv_name:
        return empty_results_payload()
    user_hash = getattr(ctx, "user_hash", None)
    csv_path = find_run_csv(csv_name, user_hash)
    json_path = csv_path.with_suffix(".json")
    if not csv_path.exists():
        return empty_results_payload("The selected result is no longer available.")

    output_df = pd.read_csv(csv_path)
    payload = {}
    files = [str(csv_path)]
    if json_path.exists():
        files.append(str(json_path))
        payload = read_json(json_path, default={"error": "could not parse JSON output"})
    for extra in (run_report_path(csv_name, user_hash), cost_report_path(csv_name, user_hash)):
        if extra.exists():
            files.append(str(extra))

    comparison, round1_rows, note = build_round1_comparison(csv_name, output_df)
    cost_html = render_cost_report(cost_report_path(csv_name, user_hash))
    report_md = run_report_markdown_text(csv_name, user_hash)
    return (
        output_df,
        payload,
        files,
        comparison,
        round1_rows,
        note,
        cost_html,
        report_md,
        gr.update(visible=False),
        gr.update(visible=True),
    )


def refresh_runs(ctx=None):
    if ctx is None:
        return gr.update(choices=[], value=None), *empty_results_payload(
            "Sign in to view your runs."
        )
    runs = list_runs(ctx.user_hash)
    selected = runs[0] if runs else None
    return gr.update(choices=runs, value=selected), *load_run(selected, ctx)


def list_active_runs(ctx=None):
    if ctx is None:
        return pd.DataFrame(columns=["run_id", "economy", "pillar", "status"])
    conn = db.get_connection(db.DEFAULT_DB_PATH)
    try:
        rows = conn.execute(
            "SELECT run_id, economy, pillar, status, enqueued_at, started_at "
            "FROM runs WHERE email=? AND status IN ('queued','running') "
            "ORDER BY enqueued_at DESC",
            (ctx.email,),
        ).fetchall()
    finally:
        conn.close()
    return pd.DataFrame([dict(r) for r in rows])


def active_runs_view(ctx=None):
    """Return active rows and show their controls only when rows exist."""
    rows = list_active_runs(ctx)
    has_active_runs = not rows.empty
    return rows, gr.update(visible=has_active_runs, open=has_active_runs)


def cancel_selected_run(run_id: str, ctx=None):
    if ctx is None:
        return "Sign in required.", *active_runs_view(None)
    if not run_id:
        return "Select a queued or running run ID to cancel.", *active_runs_view(ctx)
    conn = db.get_connection(db.DEFAULT_DB_PATH)
    try:
        ok, err = scheduler.cancel_run(str(run_id).strip(), ctx.email, conn)
    finally:
        conn.close()
    message = "Cancelled." if ok else f"Could not cancel: {err or 'unknown error'}"
    return message, *active_runs_view(ctx)


def build_round1_comparison(csv_name: str, output_df: pd.DataFrame):
    """Per-indicator comparison of a run's output against the Round 1 ground truth.
    Returns (styled comparison table, raw Round 1 rows, summary note)."""
    empty = pd.DataFrame()
    economy, pillar = parse_run_name(csv_name)
    if not economy or not pillar:
        return empty, empty, "Could not parse economy/pillar from the file name."
    if not ROUND1_DB.exists():
        return empty, empty, "Round 1 database not found under data/database/."

    try:
        round1 = pd.read_excel(ROUND1_DB, sheet_name=economy)
    except ValueError:
        return empty, empty, f"No '{economy}' sheet in the Round 1 database."

    round1 = round1[round1["Indicator_ID"].astype(str).str.match(rf"^{pillar}\.\d")].copy()
    keep = [c for c in ["Indicator_ID", "Raw Score", "Act and/or practice", "Coverage",
                        "Timeframe", "References"] if c in round1.columns]
    round1 = round1[keep].reset_index(drop=True)

    taxonomy = load_taxonomy()
    ref_to_id = {e.get("rdtii_ref"): e.get("indicator_id") for e in taxonomy}
    id_to_name = {e.get("indicator_id"): e.get("name") for e in taxonomy}

    def has_act(value) -> bool:
        s = str(value).strip().lower()
        return bool(s) and s not in {"nan", "none", "—", "-", "n/a"}

    def is_null_row(row) -> bool:
        # The engine emits a placeholder "no barrier" row for every assessed
        # indicator; those are not a real finding for comparison purposes.
        article = str(row.get("article", "")).strip().upper()
        snippet = str(row.get("verbatim_snippet", "")).strip()
        return article in {"N/A", ""} or snippet.startswith("[No qualifying")

    rows = []
    for _, r1_row in round1.iterrows():
        ref = str(r1_row["Indicator_ID"])
        indicator_id = ref_to_id.get(ref, "")
        all_matches = (output_df[output_df["indicator_id"] == indicator_id]
                       if "indicator_id" in output_df else pd.DataFrame())
        # Real (non-null) provisions only — a lone "no barrier" placeholder = a miss.
        matches = (all_matches[~all_matches.apply(is_null_row, axis=1)]
                   if len(all_matches) else all_matches)
        engine_acts = ("; ".join(sorted(matches["law_name"].dropna().unique()))
                       if len(matches) else "—")
        round1_has_act = has_act(r1_row.get("Act and/or practice", ""))
        engine_has_act = len(matches) > 0
        # Mismatch = Round 1 and the engine disagree on whether this indicator has
        # an act: a Round 1 act the engine missed, or an engine finding Round 1 lacks.
        if round1_has_act == engine_has_act:
            verdict = "✓ match"
        elif round1_has_act:
            verdict = "✗ engine missed"
        else:
            verdict = "⚠ not in Round 1"
        rows.append({
            "RDTII Ref": ref,
            "Indicator": f"{indicator_id} — {id_to_name.get(indicator_id, '')}",
            "Round 1 Score": r1_row.get("Raw Score", ""),
            "Round 1 Act": str(r1_row.get("Act and/or practice", ""))[:160],
            "Engine Acts": engine_acts[:200],
            "Provisions": len(matches),
            "KNOWN": int((matches.get("discovery_tag") == "KNOWN").sum()) if len(matches) else 0,
            "NEW": int((matches.get("discovery_tag") == "NEW").sum()) if len(matches) else 0,
            "Match": verdict,
        })

    comparison = pd.DataFrame(rows)
    mismatches = sum(1 for r in rows if not r["Match"].startswith("✓"))
    note = (
        f"**{economy} — Pillar {pillar}** · Round 1 assessed {len(round1)} indicator(s); "
        f"engine output has {len(output_df)} provision row(s). "
        f"**{mismatches} mismatched row(s)** highlighted below "
        f"(✗ = Round 1 act the engine missed, ⚠ = engine finding not in Round 1)."
    )

    # Highlight whole mismatched rows so the gaps are obvious at a glance.
    def highlight_mismatch(row):
        if str(row["Match"]).startswith("✗"):
            background = "background-color: rgba(239,68,68,0.15)"
        elif str(row["Match"]).startswith("⚠"):
            background = "background-color: rgba(245,158,11,0.15)"
        else:
            background = ""
        return [background] * len(row)

    styled = comparison.style.apply(highlight_mismatch, axis=1) if len(comparison) else comparison
    return styled, round1, note


def build_results_screen() -> dict:
    """Lay out the Results tab; returns the components the app wires together."""
    # Run choices are always populated after authentication. Loading global
    # outputs here can briefly expose another user's files during app startup.
    runs: list[str] = []

    with gr.Tab("Results"):
        with gr.Row():
            run_dropdown = gr.Dropdown(runs, label="Run output (CSV)",
                                       value=runs[0] if runs else None, scale=3)
            refresh_button = gr.Button("Refresh", scale=1)
        with gr.Accordion(
            "Queued / running", open=False, visible=False
        ) as active_runs_panel:
            active_runs_table = gr.Dataframe(interactive=False, wrap=True)
            cancel_run_id = gr.Textbox(label="Run ID to cancel")
            cancel_button = gr.Button("Cancel run")
            cancel_status = gr.Markdown("")
        empty_state = gr.HTML(
            render_results_empty_state(), visible=not bool(runs)
        )
        with gr.Group(visible=bool(runs)) as results_content:
            summary_note = gr.Markdown("")
            with gr.Tabs():
                with gr.Tab("Generated CSV"):
                    csv_table = gr.Dataframe(interactive=False, wrap=True, max_height=480)
                with gr.Tab("Generated JSON"):
                    json_view = gr.JSON()
                with gr.Tab("Compare vs Round 1"):
                    gr.Markdown("Per-indicator comparison: Round 1 ground truth vs this run's "
                                "output. Mismatched rows are highlighted.")
                    comparison_table = gr.Dataframe(interactive=False, wrap=True, max_height=480)
                    gr.Markdown("Raw Round 1 rows for this economy + pillar:")
                    round1_table = gr.Dataframe(interactive=False, wrap=True, max_height=320)
                with gr.Tab("Run report"):
                    report_markdown = gr.Markdown("_Select a run to view its report._")
                with gr.Tab("Cost"):
                    cost_html = gr.HTML(render_cost_report(None))
                with gr.Tab("Download"):
                    download_files = gr.File(label="Output files", interactive=False,
                                             file_count="multiple")

    return {
        "run_dropdown": run_dropdown,
        "refresh_button": refresh_button,
        "active_runs_table": active_runs_table,
        "active_runs_panel": active_runs_panel,
        "cancel_run_id": cancel_run_id,
        "cancel_button": cancel_button,
        "cancel_status": cancel_status,
        "summary_note": summary_note,
        "csv_table": csv_table,
        "json_view": json_view,
        "comparison_table": comparison_table,
        "round1_table": round1_table,
        "report_markdown": report_markdown,
        "cost_html": cost_html,
        "download_files": download_files,
        "empty_state": empty_state,
        "results_content": results_content,
    }
