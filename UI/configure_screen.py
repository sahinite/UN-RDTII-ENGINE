"""
Configure screen: onboard a new economy (writes a validated economies/<name>.yaml)
or a new pillar (appends validated indicators to taxonomy.json).
"""

from __future__ import annotations

import json

import gradio as gr
import pandas as pd
import yaml

from .utils import (
    ECONOMY_DIR,
    PROJECT_ROOT,
    TAXONOMY_PATH,
    list_economies,
    list_pillars,
    load_taxonomy,
)

# Mirror the Literal choices in src/config/economy_config.py.
DISCOVERY_CHOICES = ["auto", "index", "api", "sitemap", "search", "search_js", "seed_only", "TBD"]
FETCH_CHOICES = ["auto", "pdf_endpoint", "api_versioned_pdf", "html", "html_wholedoc",
                 "html_js", "pdf_link", "TBD"]
ANTI_BOT_CHOICES = ["none", "header_spoof", "playwright_stealth"]

NEW_PILLAR_COLUMNS = ["indicator_id", "rdtii_ref", "name", "category",
                      "legal_question", "probe_keywords (; separated)"]


def save_economy(economy_name, iso_code, un_name, script_type, languages,
                 portal_name, portal_url, portal_type, discovery, fetch, anti_bot,
                 index_urls, index_link_pattern, pdf_view_suffix,
                 api_base, api_collection, pdf_path_suffix, playwright_fallback):
    """Build, validate (same Pydantic model the pipeline uses) and write the YAML."""
    if not economy_name or not portal_name or not portal_url:
        return "❌ Economy name, portal name and portal URL are required.", gr.update()

    portal: dict = {
        "name": portal_name.strip(),
        "url": portal_url.strip(),
        "type": portal_type,
        "anti_bot": anti_bot,
        "discovery": discovery,
        "fetch": fetch,
    }
    if index_urls.strip():
        portal["index_urls"] = [u.strip() for u in index_urls.splitlines() if u.strip()]
    if index_link_pattern.strip():
        portal["index_link_pattern"] = [p.strip() for p in index_link_pattern.split(",")
                                        if p.strip()]
    if pdf_view_suffix.strip():
        portal["pdf_view_suffix"] = pdf_view_suffix.strip()
    if api_base.strip():
        portal["api_base"] = api_base.strip()
    if api_collection.strip():
        portal["api_collection"] = api_collection.strip()
    if pdf_path_suffix.strip():
        portal["pdf_path_suffix"] = pdf_path_suffix.strip()
    if playwright_fallback:
        portal["transport_fallback"] = "playwright_stealth"

    config = {
        "economy_name": economy_name.strip(),
        "iso_code": iso_code.strip().upper(),
        "un_name": un_name.strip() or economy_name.strip(),
        "script_type": script_type,
        "languages": [lang.strip() for lang in languages.split(",") if lang.strip()] or ["en"],
        "portals": [portal],
    }

    try:
        from src.config.economy_config import EconomyConfig
        EconomyConfig(**config)
    except Exception as exc:
        return f"❌ Config invalid: {exc}", gr.update()

    yaml_path = ECONOMY_DIR / f"{economy_name.strip().lower().replace(' ', '_')}.yaml"
    if yaml_path.exists():
        return f"❌ {yaml_path.name} already exists — edit it directly in economies/.", gr.update()
    yaml_path.write_text(yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
                         encoding="utf-8")
    return (f"✅ Saved {yaml_path.relative_to(PROJECT_ROOT)} — {economy_name} is now "
            f"selectable on the Run screen.",
            gr.update(choices=list_economies(), value=economy_name.strip()))


def save_pillar(pillar_number, indicators_df: pd.DataFrame):
    """Append the filled indicator rows to taxonomy.json after validation."""
    if pillar_number is None:
        return "❌ Enter a pillar number.", gr.update()
    pillar_number = int(pillar_number)
    taxonomy = load_taxonomy()
    existing_ids = {entry["indicator_id"] for entry in taxonomy}

    new_entries = []
    for _, row in indicators_df.iterrows():
        indicator_id = str(row.get("indicator_id", "") or "").strip()
        if not indicator_id:
            continue
        if not indicator_id.startswith(f"P{pillar_number}-"):
            return (f"❌ Indicator id '{indicator_id}' must start with 'P{pillar_number}-' "
                    f"(e.g. P{pillar_number}-I1).", gr.update())
        if indicator_id in existing_ids:
            return f"❌ Indicator '{indicator_id}' already exists in taxonomy.json.", gr.update()
        keywords = [k.strip() for k in
                    str(row.get("probe_keywords (; separated)", "") or "").split(";")
                    if k.strip()]
        if not keywords:
            return f"❌ Indicator '{indicator_id}' needs at least one probe keyword.", gr.update()
        new_entries.append({
            "indicator_id": indicator_id,
            "rdtii_ref": str(row.get("rdtii_ref", "") or "").strip() or f"{pillar_number}.?",
            "name": str(row.get("name", "") or "").strip(),
            "category": str(row.get("category", "") or "").strip(),
            "legal_question": str(row.get("legal_question", "") or "").strip(),
            "probe_keywords": keywords,
            "in_scope": [],
            "out_of_scope": [],
            "negative_examples": [],
        })

    if not new_entries:
        return "❌ No indicator rows filled in.", gr.update()

    candidate = taxonomy + new_entries
    try:
        from src.crawler.probe import validate_taxonomy
        validate_taxonomy(candidate)
    except Exception as exc:
        return f"❌ taxonomy.json validation failed: {exc}", gr.update()

    TAXONOMY_PATH.write_text(json.dumps(candidate, indent=2, ensure_ascii=False),
                             encoding="utf-8")
    return (f"✅ Added {len(new_entries)} indicator(s) for Pillar {pillar_number} "
            f"to taxonomy.json.",
            gr.update(choices=list_pillars()))


def build_configure_screen() -> dict:
    """Lay out the Configure tab; returns the components the app wires together."""
    with gr.Tab("Configure"):
        with gr.Tabs():
            with gr.Tab("New economy"):
                gr.Markdown("Creates `economies/<name>.yaml`, validated with the same Pydantic "
                            "model the pipeline uses. Singapore (index/html_wholedoc) and "
                            "Australia (api/api_versioned_pdf) are good references.")
                with gr.Row():
                    economy_name = gr.Textbox(label="Economy name *", placeholder="Thailand")
                    iso_code = gr.Textbox(label="ISO code *", placeholder="TH", max_lines=1)
                    un_name = gr.Textbox(label="UN name", placeholder="Thailand")
                with gr.Row():
                    script_type = gr.Radio(["latin", "asian"], value="latin", label="Script type")
                    languages = gr.Textbox(label="Languages (comma separated)", value="en")
                gr.Markdown("**Primary portal**")
                with gr.Row():
                    portal_name = gr.Textbox(label="Portal name *",
                                             placeholder="Thailand Official Gazette")
                    portal_url = gr.Textbox(label="Portal URL *", placeholder="https://...")
                    portal_type = gr.Radio(["primary", "secondary"], value="primary",
                                           label="Type")
                with gr.Row():
                    discovery = gr.Dropdown(DISCOVERY_CHOICES, value="auto",
                                            label="Discovery strategy")
                    fetch = gr.Dropdown(FETCH_CHOICES, value="auto", label="Fetch strategy")
                    anti_bot = gr.Dropdown(ANTI_BOT_CHOICES, value="none", label="Anti-bot")
                with gr.Accordion("Strategy details (only fill what the strategy needs)",
                                  open=False):
                    index_urls = gr.Textbox(
                        label="index_urls (one per line — for discovery: index)", lines=3)
                    index_link_pattern = gr.Textbox(
                        label="index_link_pattern (comma separated, e.g. /Act/,/SL/)")
                    pdf_view_suffix = gr.Textbox(
                        label="pdf_view_suffix (for fetch: pdf_endpoint, e.g. ?ViewType=Pdf)")
                    api_base = gr.Textbox(label="api_base (for discovery: api)")
                    api_collection = gr.Textbox(label="api_collection (for discovery: api)")
                    pdf_path_suffix = gr.Textbox(
                        label="pdf_path_suffix (for fetch: api_versioned_pdf)")
                    playwright_fallback = gr.Checkbox(
                        label="transport_fallback: playwright_stealth")
                save_economy_button = gr.Button("Save economy", variant="primary")
                economy_status = gr.Markdown("")

            with gr.Tab("New pillar"):
                gr.Markdown("Adds indicators for a new pillar to `taxonomy.json`. "
                            "IDs follow `P<pillar>-I<n>`; probe keywords drive Zone 1 discovery. "
                            "Remember to also add the pillar's rows to the Round 1 database for "
                            "seed/KNOWN tagging.")
                pillar_number = gr.Number(label="Pillar number", precision=0, value=8)
                indicators_table = gr.Dataframe(
                    headers=NEW_PILLAR_COLUMNS,
                    value=pd.DataFrame([{
                        "indicator_id": "P8-I1", "rdtii_ref": "8.1", "name": "",
                        "category": "", "legal_question": "",
                        "probe_keywords (; separated)": "",
                    }]),
                    interactive=True, row_count=(1, "dynamic"), label="Indicators",
                )
                save_pillar_button = gr.Button("Save pillar indicators", variant="primary")
                pillar_status = gr.Markdown("")

    return {
        "economy_inputs": [economy_name, iso_code, un_name, script_type, languages,
                           portal_name, portal_url, portal_type, discovery, fetch, anti_bot,
                           index_urls, index_link_pattern, pdf_view_suffix,
                           api_base, api_collection, pdf_path_suffix, playwright_fallback],
        "save_economy_button": save_economy_button,
        "economy_status": economy_status,
        "pillar_number": pillar_number,
        "indicators_table": indicators_table,
        "save_pillar_button": save_pillar_button,
        "pillar_status": pillar_status,
    }
