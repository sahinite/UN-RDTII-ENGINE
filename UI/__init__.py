"""
RDTII Extraction Engine — Gradio UI package.

Modules:
    app.py                 — assembles the screens, cross-screen wiring, launch()
    header.py              — brand bar + light/dark theme toggle
    styles.py              — all CSS (page-level + inline component styles)
    utils.py               — shared paths and helpers used by every screen
    pipeline_view.py       — live pipeline visual (run-state machine + renderer)
    reports.py             — run report + cost report builders, per-run artifacts
    run_screen.py          — Run tab (pickers, live visual, logs, streaming runner)
    results_screen.py      — Results tab (CSV/JSON, Round 1 comparison, report, cost)
    settings_screen.py     — My Settings tab (profile, provider, encrypted API keys)
    configure_screen.py    — Configure tab (new economy YAML / new pillar indicators)
    auth_modal.py          — Google Sign-In overlay and browser/Python token bridge
"""

from .app import build_app, launch

__all__ = ["build_app", "launch"]
