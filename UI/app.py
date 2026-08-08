"""
Assembles the RDTII UI: builds every screen, wires the cross-screen events, and
exposes `build_app()` / `launch()`.

Layout lives in each *_screen.py module; only wiring that crosses screens (e.g.
the Run screen's "View Results" jumping into the Results screen) lives here.
"""

from __future__ import annotations

import os

import gradio as gr
from dotenv import load_dotenv

load_dotenv()

from src.auth import db, dev_bypass
from src.queue import scheduler
from .auth_modal import (
    build_auth_modal,
    gis_head_html,
    handle_sign_in,
    handle_sign_out,
    js_init_gis_button,
    js_clear_token_on_signout,
    js_persist_token_on_success,
    js_restore_token_on_load,
    render_full_header_html,
    render_overlay_html,
)
from .configure_screen import build_configure_screen, save_economy, save_pillar
from .header import RESTORE_THEME_JS
from .reports import backfill_all_runs
from .results_screen import (
    build_results_screen,
    cancel_selected_run,
    list_active_runs,
    load_run,
    refresh_runs,
)
from .run_screen import build_run_screen, run_pipeline_streaming
from .settings_screen import build_settings_screen, save_settings, settings_values
from .styles import APP_CSS
from .utils import PROJECT_ROOT

# JS that fronts the Results top-level tab (Gradio has no server-side tab switch
# that plays well with streaming outputs, so the click happens client-side).
_OPEN_RESULTS_TAB_JS = (
    "() => { const tab = [...document.querySelectorAll('button[role=tab]')]"
    ".find(el => el.textContent.trim() === 'Results'); if (tab) tab.click(); }"
)


def _empty_results():
    import pandas as pd
    from .reports import render_cost_report

    empty = pd.DataFrame()
    return empty, {}, None, empty, empty, "Sign in to view your runs.", render_cost_report(None), "_Sign in to view your runs._"


def _auth_required_update(message: str = "Sign in to continue."):
    return gr.update(value=render_overlay_html(message), visible=True)


def _open_auth_modal_if_needed(ctx):
    if ctx is None:
        return _auth_required_update()
    return gr.update()


def _run_pipeline_with_auth_modal(economy, pillar, ctx):
    if ctx is None:
        for payload in run_pipeline_streaming(economy, pillar, ctx):
            yield (*payload, _auth_required_update("Sign in to run the pipeline."))
        return

    for payload in run_pipeline_streaming(economy, pillar, ctx):
        yield (*payload, gr.update())


def _save_economy_admin(*args):
    *payload, ctx = args
    if ctx is None or not getattr(ctx, "is_admin", False):
        return "Admin access required.", gr.update()
    return save_economy(*payload)


def _save_pillar_admin(pillar_number, indicators_table, ctx):
    if ctx is None or not getattr(ctx, "is_admin", False):
        return "Admin access required.", gr.update()
    return save_pillar(pillar_number, indicators_table)


def build_app() -> gr.Blocks:
    db.init_db(db.DEFAULT_DB_PATH)
    max_concurrent = int(os.environ.get("MAX_CONCURRENT_RUNS", "5") or "5")
    scheduler.start_worker(
        db.DEFAULT_DB_PATH,
        max_concurrent=max_concurrent,
        project_root=PROJECT_ROOT,
        outputs_root=str(PROJECT_ROOT / "outputs"),
        logs_root=str(PROJECT_ROOT / "logs"),
    )
    backfill_all_runs()   # older runs get their per-run cost + report files

    with gr.Blocks(title="RDTII Extraction Engine") as app:
        auth_state = gr.State(None)
        auth_header = gr.HTML(render_full_header_html(None), elem_id="rdtii_app_header")
        auth = build_auth_modal()

        with gr.Tabs() as main_tabs:
            run = build_run_screen()
            results = build_results_screen()
            settings = build_settings_screen(visible=False)
            configure = build_configure_screen(visible=False)

        # ── Run screen ───────────────────────────────────────────────────────
        run["run_button"].click(
            _run_pipeline_with_auth_modal,
            inputs=[run["economy_dropdown"], run["pillar_dropdown"], auth_state],
            outputs=[run["pipeline_html"], run["logs_box"], run["run_cost_html"],
                     results["run_dropdown"], run["steps_accordion"],
                     run["report_html"], run["view_results_button"],
                     auth["overlay_html"]],
        )

        # ── Results screen ───────────────────────────────────────────────────
        results_outputs = [results["csv_table"], results["json_view"],
                           results["download_files"], results["comparison_table"],
                           results["round1_table"], results["summary_note"],
                           results["cost_html"], results["report_markdown"]]

        results_refresh_outputs = [results["run_dropdown"], *results_outputs]
        results["refresh_button"].click(
            refresh_runs,
            inputs=auth_state,
            outputs=results_refresh_outputs,
        ).then(list_active_runs, inputs=auth_state, outputs=results["active_runs_table"])
        results["run_dropdown"].change(load_run, inputs=[results["run_dropdown"], auth_state],
                                       outputs=results_outputs)
        app.load(_empty_results, outputs=results_outputs)
        results["cancel_button"].click(
            cancel_selected_run,
            inputs=[results["cancel_run_id"], auth_state],
            outputs=[results["cancel_status"], results["active_runs_table"]],
        )

        # "View Results" → load the just-finished run into Results, then jump there.
        def open_results_for_run(csv_name, ctx):
            return (gr.update(value=csv_name), *load_run(csv_name, ctx))

        run["view_results_button"].click(
            open_results_for_run,
            inputs=[results["run_dropdown"], auth_state],
            outputs=[results["run_dropdown"], *results_outputs],
        ).then(None, None, None, js=_OPEN_RESULTS_TAB_JS)

        # ── Configure screen ─────────────────────────────────────────────────
        configure["save_economy_button"].click(
            _save_economy_admin,
            inputs=[*configure["economy_inputs"], auth_state],
            outputs=[configure["economy_status"], run["economy_dropdown"]],
        )
        configure["save_pillar_button"].click(
            _save_pillar_admin,
            inputs=[configure["pillar_number"], configure["indicators_table"], auth_state],
            outputs=[configure["pillar_status"], run["pillar_dropdown"]],
        )

        # ── Auth + Settings ──────────────────────────────────────────────────
        auth_outputs = [
            auth_state,
            auth_header,
            auth["overlay_html"],
            settings["tab"],
            configure["tab"],
        ]
        auth["sign_in_trigger_button"].click(
            handle_sign_in,
            inputs=[auth["token_input"], auth_state],
            outputs=auth_outputs,
            js=js_persist_token_on_success(),
        ).then(None, None, None, js=js_init_gis_button()
        ).then(settings_values, inputs=auth_state, outputs=settings["value_outputs"]
        ).then(refresh_runs, inputs=auth_state, outputs=results_refresh_outputs
        ).then(list_active_runs, inputs=auth_state, outputs=results["active_runs_table"])

        if dev_bypass.is_bypass_enabled():
            app.load(
                lambda ctx: handle_sign_in("", ctx),
                inputs=auth_state,
                outputs=auth_outputs,
            ).then(settings_values, inputs=auth_state, outputs=settings["value_outputs"]
            ).then(refresh_runs, inputs=auth_state, outputs=results_refresh_outputs
            ).then(list_active_runs, inputs=auth_state, outputs=results["active_runs_table"])

        settings["save_button"].click(
            save_settings,
            inputs=[settings["contact"], settings["provider"], *settings["key_components"], auth_state],
            outputs=[auth_state, *settings["value_outputs"]],
        )
        settings["sign_out_button"].click(
            handle_sign_out,
            inputs=auth_state,
            outputs=auth_outputs,
            js=js_clear_token_on_signout(),
        ).then(None, None, None, js=js_init_gis_button()
        ).then(settings_values, inputs=auth_state, outputs=settings["value_outputs"]
        ).then(_empty_results, outputs=results_outputs
        ).then(lambda: gr.update(choices=[], value=None), outputs=results["run_dropdown"]
        ).then(list_active_runs, inputs=auth_state, outputs=results["active_runs_table"])

        app.load(None, None, None, js=js_init_gis_button())
        app.load(None, None, None, js=js_restore_token_on_load())
        main_tabs.select(
            _open_auth_modal_if_needed,
            inputs=auth_state,
            outputs=auth["overlay_html"],
            show_progress="hidden",
        ).then(None, None, None, js=js_init_gis_button())

        # Restore the viewer's saved light/dark choice and sync the toggle label.
        app.load(None, None, None, js=RESTORE_THEME_JS)

    return app


def launch() -> None:
    # Use localhost for GIS local development. Google documents localhost as
    # the supported loopback origin for browser-based sign-in.
    bind = "localhost"
    warning = dev_bypass.check_bypass_safety(bind)
    if warning:
        print(f"\033[91m{warning}\033[0m")
    build_app().launch(
        server_name=bind,
        server_port=7860,
        show_error=True,
        css=APP_CSS,
        head=gis_head_html(),
        theme=gr.themes.Soft(
            primary_hue=gr.themes.colors.blue,
            secondary_hue=gr.themes.colors.emerald,
            neutral_hue=gr.themes.colors.slate,
        ),
    )
