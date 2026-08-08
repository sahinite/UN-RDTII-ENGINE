"""
Run screen: economy/pillar pickers, the live pipeline visual, the completion
report, and the runtime logs. The pipeline itself runs as a `python main.py`
subprocess whose progress lines stream into the run state.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

import gradio as gr

from src.auth import db
from src.queue import eta, scheduler

from .pipeline_view import (
    ALL_STAGE_IDS,
    new_run_state,
    render_pipeline,
    update_run_state,
)
from .reports import (
    gather_report_data,
    persist_run_artifacts,
    render_cost_report,
    render_report_html,
)
from .settings_screen import missing_provider_key_message
from .utils import (
    PROJECT_ROOT,
    cost_report_path,
    format_elapsed,
    list_economies,
    list_pillars,
    list_runs,
    pillar_choices,
)

_ANSI_CODES = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_STEPS_OPEN = dict(open=True, label="Pipeline steps")
_STEPS_COLLAPSED = dict(open=False, label="Pipeline steps — completed (click to expand)")


def _empty_yield(message: str):
    return (
        render_pipeline(new_run_state()), message, gr.update(), gr.update(),
        gr.update(**_STEPS_OPEN), gr.update(visible=False), gr.update(visible=False)
    )


def _run_log_path(ctx, run_id: str) -> Path:
    return PROJECT_ROOT / "logs" / ctx.user_hash / run_id / "subprocess.log"


def _run_cost_source_path(ctx, run_id: str) -> Path:
    return PROJECT_ROOT / "logs" / ctx.user_hash / run_id / "cost_report.json"


def run_pipeline_streaming(economy: str, pillar: int, ctx=None):
    """Generator streaming: stage visual, logs, cost html, runs dropdown,
    steps-accordion state, completion report, View-Results button."""
    if ctx is None:
        yield _empty_yield("Sign in required.")
        return

    if not economy or pillar is None:
        yield _empty_yield("Select an economy and a pillar first.")
        return

    missing_key = missing_provider_key_message(ctx)
    if missing_key:
        yield _empty_yield(missing_key)
        return

    state = new_run_state(economy, int(pillar))
    state["phase"] = "running"
    log_lines: list[str] = [
        f"$ queue main.py --economy {economy} --pillar {int(pillar)}",
        f"# started {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
    ]
    started_at = time.monotonic()

    conn = db.get_connection(db.DEFAULT_DB_PATH)
    try:
        run_id, enqueue_error = scheduler.enqueue(ctx.email, economy, int(pillar), conn)
    finally:
        conn.close()

    if enqueue_error or run_id is None:
        yield _empty_yield(enqueue_error or "Could not enqueue run.")
        return

    log_lines.append(f"# run id {run_id}")
    yield (render_pipeline(state), "\n".join(log_lines), gr.update(), gr.update(),
           gr.update(**_STEPS_OPEN), gr.update(visible=False, value=""),
           gr.update(visible=False))

    last_log_size = 0
    terminal_row = None
    while True:
        conn = db.get_connection(db.DEFAULT_DB_PATH)
        try:
            row = scheduler.get_run(run_id, conn)
            position = scheduler.get_queue_position(run_id, conn)
            eta_s = (
                eta.compute_eta_seconds(economy, int(pillar), position, conn)
                if position is not None else None
            )
        finally:
            conn.close()

        if row is None:
            state["phase"] = "failed"
            log_lines.append("# run disappeared from queue")
            break

        status = row["status"]
        if status == "queued":
            eta_text = "ETA unavailable" if eta_s is None else f"estimated start ~{format_elapsed(eta_s)}"
            queue_line = f"# queued — position {position or '?'}; {eta_text}"
            if not log_lines or log_lines[-1] != queue_line:
                log_lines.append(queue_line)
            state["elapsed"] = format_elapsed(time.monotonic() - started_at)
            yield (render_pipeline(state), "\n".join(log_lines), gr.update(), gr.update(),
                   gr.update(), gr.update(), gr.update())
            time.sleep(5)
            continue

        if status == "running":
            state["phase"] = "running"
            log_path = _run_log_path(ctx, run_id)
            if log_path.exists():
                text = log_path.read_text(encoding="utf-8", errors="replace")
                chunk = text[last_log_size:]
                last_log_size = len(text)
                for raw_line in chunk.splitlines():
                    line = _ANSI_CODES.sub("", raw_line).rstrip("\n")
                    if not line.strip():
                        continue
                    log_lines.append(line)
                    update_run_state(line, state)
            state["elapsed"] = format_elapsed(time.monotonic() - started_at)
            yield (render_pipeline(state), "\n".join(log_lines), gr.update(), gr.update(),
                   gr.update(), gr.update(), gr.update())
            time.sleep(1)
            continue

        terminal_row = row
        break

    # Drain any final log lines written after the last poll.
    log_path = _run_log_path(ctx, run_id)
    if log_path.exists():
        text = log_path.read_text(encoding="utf-8", errors="replace")
        chunk = text[last_log_size:]
        for raw_line in chunk.splitlines():
            line = _ANSI_CODES.sub("", raw_line).rstrip("\n")
            if line.strip():
                log_lines.append(line)
                update_run_state(line, state)

    terminal_status = terminal_row["status"] if terminal_row else "failed"
    state["elapsed"] = format_elapsed(time.monotonic() - started_at)
    if terminal_status == "completed":
        state["phase"] = "complete"
        for stage_id in ALL_STAGE_IDS:
            if state["stages"].get(stage_id) == "running":
                state["stages"][stage_id] = "done"
        for doc in state["docs"].values():
            if doc["status"] == "running":
                doc["status"] = "done"
        log_lines += ["", "# pipeline finished OK"]
    elif terminal_status == "cancelled":
        state["phase"] = "failed"
        log_lines += ["", "# pipeline cancelled"]
    else:
        state["phase"] = "failed"
        log_lines += ["", "# pipeline failed"]
        for stage_id in ALL_STAGE_IDS:
            if state["stages"].get(stage_id) == "running":
                state["stages"][stage_id] = "fail"
                break

    source_cost = _run_cost_source_path(ctx, run_id)
    if source_cost.exists():
        try:
            total = json.loads(source_cost.read_text(encoding="utf-8"))["total_cost_usd"]
            state["cost"] = f"${total:.4f}"
        except Exception:
            pass

    run_output_dir = PROJECT_ROOT / "outputs" / ctx.user_hash / run_id
    new_runs = sorted(run_output_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    runs = list_runs(ctx.user_hash)
    state["csv_name"] = new_runs[0].name if new_runs else (runs[0] if runs else "")
    succeeded = state["phase"] == "complete"

    report_html = ""
    cost_html = render_cost_report(None)
    if succeeded and state["csv_name"]:
        # Save this run's own cost snapshot + markdown report, then render from them.
        persist_run_artifacts(
            state["csv_name"],
            state,
            user_hash=ctx.user_hash,
            source_cost_path=source_cost,
        )
        report_html = render_report_html(
            gather_report_data(state["csv_name"], state, user_hash=ctx.user_hash)
        )
        cost_html = render_cost_report(cost_report_path(state["csv_name"], ctx.user_hash))

    yield (
        render_pipeline(state),
        "\n".join(log_lines),
        cost_html,
        gr.update(choices=runs, value=state["csv_name"] if state["csv_name"] else None),
        # Collapse the live step visual once there is a report to read instead.
        gr.update(**(_STEPS_COLLAPSED if succeeded else _STEPS_OPEN)),
        gr.update(visible=succeeded, value=report_html),
        gr.update(visible=succeeded),
    )


def build_run_screen() -> dict:
    """Lay out the Run tab; returns the components the app wires together."""
    economies = list_economies()
    pillars = list_pillars()

    with gr.Tab("Run"):
        with gr.Row(equal_height=True):
            economy_dropdown = gr.Dropdown(
                economies, label="Economy",
                value=economies[0] if economies else None, scale=2)
            pillar_dropdown = gr.Dropdown(
                pillar_choices(), label="Pillar",
                value=pillars[0] if pillars else None, scale=2)
            run_button = gr.Button("Run pipeline", variant="primary", scale=1)

        with gr.Accordion("Pipeline steps", open=True) as steps_accordion:
            pipeline_html = gr.HTML(render_pipeline(new_run_state()))
        report_html = gr.HTML(visible=False)
        view_results_button = gr.Button("View Results →", variant="primary", visible=False)
        with gr.Tabs():
            with gr.Tab("Logs"):
                logs_box = gr.Textbox(value="", lines=22, max_lines=22, label="Runtime logs",
                                      interactive=False, autoscroll=True)
            with gr.Tab("Cost"):
                run_cost_html = gr.HTML(render_cost_report(None))

    return {
        "economy_dropdown": economy_dropdown,
        "pillar_dropdown": pillar_dropdown,
        "run_button": run_button,
        "steps_accordion": steps_accordion,
        "pipeline_html": pipeline_html,
        "report_html": report_html,
        "view_results_button": view_results_button,
        "logs_box": logs_box,
        "run_cost_html": run_cost_html,
    }
