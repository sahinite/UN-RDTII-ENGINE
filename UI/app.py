"""
Assembles the RDTII UI: builds every screen, wires the cross-screen events, and
exposes `build_app()` / `launch()`.

Layout lives in each *_screen.py module; only wiring that crosses screens (e.g.
the Run screen's "View Results" jumping into the Results screen) lives here.
"""

from __future__ import annotations

import gradio as gr

from .configure_screen import build_configure_screen, save_economy, save_pillar
from .environment_screen import build_environment_screen, reload_env, save_env
from .header import APP_HEADER, RESTORE_THEME_JS
from .reports import backfill_all_runs
from .results_screen import build_results_screen, load_run
from .run_screen import build_run_screen, run_pipeline_streaming
from .styles import APP_CSS
from .utils import list_runs

# JS that fronts the Results top-level tab (Gradio has no server-side tab switch
# that plays well with streaming outputs, so the click happens client-side).
_OPEN_RESULTS_TAB_JS = (
    "() => { const tab = [...document.querySelectorAll('button[role=tab]')]"
    ".find(el => el.textContent.trim() === 'Results'); if (tab) tab.click(); }"
)


def build_app() -> gr.Blocks:
    backfill_all_runs()   # older runs get their per-run cost + report files

    with gr.Blocks(title="RDTII Extraction Engine") as app:
        gr.HTML(APP_HEADER)

        run = build_run_screen()
        results = build_results_screen()
        configure = build_configure_screen()
        environment = build_environment_screen()

        # ── Run screen ───────────────────────────────────────────────────────
        run["run_button"].click(
            run_pipeline_streaming,
            inputs=[run["economy_dropdown"], run["pillar_dropdown"]],
            outputs=[run["pipeline_html"], run["logs_box"], run["run_cost_html"],
                     results["run_dropdown"], run["steps_accordion"],
                     run["report_html"], run["view_results_button"]],
        )

        # ── Results screen ───────────────────────────────────────────────────
        results_outputs = [results["csv_table"], results["json_view"],
                           results["download_files"], results["comparison_table"],
                           results["round1_table"], results["summary_note"],
                           results["cost_html"], results["report_markdown"]]

        results["refresh_button"].click(lambda: gr.update(choices=list_runs()),
                                        outputs=results["run_dropdown"])
        results["run_dropdown"].change(load_run, inputs=results["run_dropdown"],
                                       outputs=results_outputs)
        app.load(load_run, inputs=results["run_dropdown"], outputs=results_outputs)

        # "View Results" → load the just-finished run into Results, then jump there.
        def open_results_for_run(csv_name):
            return (gr.update(value=csv_name), *load_run(csv_name))

        run["view_results_button"].click(
            open_results_for_run,
            inputs=[results["run_dropdown"]],
            outputs=[results["run_dropdown"], *results_outputs],
        ).then(None, None, None, js=_OPEN_RESULTS_TAB_JS)

        # ── Configure screen ─────────────────────────────────────────────────
        configure["save_economy_button"].click(
            save_economy,
            inputs=configure["economy_inputs"],
            outputs=[configure["economy_status"], run["economy_dropdown"]],
        )
        configure["save_pillar_button"].click(
            save_pillar,
            inputs=[configure["pillar_number"], configure["indicators_table"]],
            outputs=[configure["pillar_status"], run["pillar_dropdown"]],
        )

        # ── Environment screen ───────────────────────────────────────────────
        environment["save_button"].click(save_env,
                                         inputs=environment["field_components"],
                                         outputs=environment["status"])
        environment["reload_button"].click(reload_env,
                                           outputs=environment["field_components"])

        # Restore the viewer's saved light/dark choice and sync the toggle label.
        app.load(None, None, None, js=RESTORE_THEME_JS)

    return app


def launch() -> None:
    build_app().launch(
        server_name="127.0.0.1",
        server_port=7860,
        show_error=True,
        css=APP_CSS,
        theme=gr.themes.Soft(
            primary_hue=gr.themes.colors.blue,
            secondary_hue=gr.themes.colors.emerald,
            neutral_hue=gr.themes.colors.slate,
        ),
    )
