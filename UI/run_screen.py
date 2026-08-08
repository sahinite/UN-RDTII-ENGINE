"""
Run screen: economy/pillar pickers, the live pipeline visual, the completion
report, and the runtime logs. The pipeline itself runs as a `python main.py`
subprocess whose progress lines stream into the run state.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from datetime import datetime

import gradio as gr

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
from .utils import (
    PROJECT_ROOT,
    SHARED_COST_REPORT,
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


def run_pipeline_streaming(economy: str, pillar: int):
    """Generator streaming: stage visual, logs, cost html, runs dropdown,
    steps-accordion state, completion report, View-Results button."""
    if not economy or pillar is None:
        yield (render_pipeline(new_run_state()), "Select an economy and a pillar first.",
               gr.update(), gr.update(), gr.update(**_STEPS_OPEN),
               gr.update(visible=False), gr.update(visible=False))
        return

    state = new_run_state(economy, int(pillar))
    state["phase"] = "running"
    log_lines: list[str] = [
        f"$ python main.py --economy {economy} --pillar {int(pillar)}",
        f"# started {datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
    ]
    started_at = time.monotonic()
    # A new run reopens the steps and clears the previous report + View button.
    yield (render_pipeline(state), "\n".join(log_lines), gr.update(), gr.update(),
           gr.update(**_STEPS_OPEN), gr.update(visible=False, value=""),
           gr.update(visible=False))

    process = subprocess.Popen(
        [sys.executable, "main.py", "--economy", economy, "--pillar", str(int(pillar))],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = _ANSI_CODES.sub("", raw_line).rstrip("\n")
            if not line.strip():
                continue
            log_lines.append(line)
            update_run_state(line, state)
            state["elapsed"] = format_elapsed(time.monotonic() - started_at)
            yield (render_pipeline(state), "\n".join(log_lines), gr.update(), gr.update(),
                   gr.update(), gr.update(), gr.update())
    finally:
        process.wait()

    state["elapsed"] = format_elapsed(time.monotonic() - started_at)
    if process.returncode == 0:
        state["phase"] = "complete"
        for stage_id in ALL_STAGE_IDS:
            if state["stages"].get(stage_id) == "running":
                state["stages"][stage_id] = "done"
        for doc in state["docs"].values():
            if doc["status"] == "running":
                doc["status"] = "done"
        log_lines += ["", "# pipeline finished OK"]
    else:
        state["phase"] = "failed"
        log_lines += ["", f"# pipeline exited with code {process.returncode}"]
        for stage_id in ALL_STAGE_IDS:
            if state["stages"].get(stage_id) == "running":
                state["stages"][stage_id] = "fail"
                break

    if SHARED_COST_REPORT.exists():
        try:
            total = json.loads(SHARED_COST_REPORT.read_text())["total_cost_usd"]
            state["cost"] = f"${total:.4f}"
        except Exception:
            pass

    runs = list_runs()
    state["csv_name"] = runs[0] if runs else ""
    succeeded = state["phase"] == "complete"

    report_html = ""
    cost_html = render_cost_report(None)
    if succeeded and state["csv_name"]:
        # Save this run's own cost snapshot + markdown report, then render from them.
        persist_run_artifacts(state["csv_name"], state)
        report_html = render_report_html(gather_report_data(state["csv_name"], state))
        cost_html = render_cost_report(cost_report_path(state["csv_name"]))

    yield (
        render_pipeline(state),
        "\n".join(log_lines),
        cost_html,
        gr.update(choices=runs, value=runs[0] if runs else None),
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
