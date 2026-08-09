"""
Run report + cost report builders.

`gather_report_data()` computes everything both renderers need from a run's OWN
output files (CSV + JSON + per-run cost snapshot) — KNOWN/NEW act counts come from
the final CSV, never from a mid-run tally. The same data renders two ways:
  * `render_report_html()`     — styled document view shown live on the Run screen
  * `render_report_markdown()` — saved as <run>_runReport.md and shown in Results

`persist_run_artifacts()` snapshots the shared cost report + writes the markdown
next to the CSV after a run; `backfill_all_runs()` does the same for runs that
predate per-run artifacts.
"""

from __future__ import annotations

import json
from datetime import datetime
from html import escape as esc
from pathlib import Path

import pandas as pd

from .styles import COST_CSS, REPORT_CSS
from .utils import (
    OUTPUT_DIR,
    SHARED_COST_REPORT,
    cost_report_path,
    detect_fetch_kind,
    find_run_csv,
    list_runs,
    load_run_cost,
    parse_run_name,
    pillar_hint,
    placeholder_cost_report,
    read_json,
    run_report_path,
    shorten_document_label,
)


# ── Data gathering ────────────────────────────────────────────────────────────

def gather_report_data(
    csv_name: str,
    run_state: dict | None = None,
    user_hash: str | None = None,
) -> dict:
    """Everything both report renderers need. `run_state` (if present) only adds
    live-run extras the files can't provide (queued/attempted/failed, wall-clock)."""
    run_state = run_state or {}
    economy, pillar = parse_run_name(csv_name)
    csv_path = find_run_csv(csv_name, user_hash)
    json_path = csv_path.with_suffix(".json")

    rows: list[dict] = []
    if csv_path.exists():
        rows = pd.read_csv(csv_path).fillna("").to_dict("records")
    payload = read_json(json_path, default={}) or {}
    used_documents = payload.get("documents", []) or []

    # ── acts (from the final output file) ────────────────────────────────────
    act_tag: dict[str, str] = {}
    act_provisions: dict[str, int] = {}
    for row in rows:
        name = str(row.get("law_name", "")).strip()
        if not name:
            continue
        act_provisions[name] = act_provisions.get(name, 0) + 1
        tag = str(row.get("discovery_tag", "")).strip().upper()
        if act_tag.get(name) != "KNOWN":       # KNOWN wins if any provision is KNOWN
            act_tag[name] = tag or "NEW"
    known_acts = sorted(a for a, t in act_tag.items() if t == "KNOWN")
    new_acts = sorted(a for a, t in act_tag.items() if t != "KNOWN")
    known_provisions = sum(
        1 for r in rows if str(r.get("discovery_tag", "")).upper() == "KNOWN")

    # ── documents fetched / used ─────────────────────────────────────────────
    doc_rows = []
    for doc in used_documents:
        url = doc.get("source_url", "") or ""
        doc_rows.append({
            "name": doc.get("law_name") or shorten_document_label(url),
            "kind": detect_fetch_kind(url),
            "scanned": bool(doc.get("pdf_is_scanned")),
            "tag": (doc.get("discovery_tag") or "").upper(),
            "provisions": len(doc.get("provisions", []) or []),
        })

    # ── cost (per-run snapshot) ──────────────────────────────────────────────
    cost = load_run_cost(csv_name, user_hash)
    components = cost.get("components", {})

    return {
        "csv_name": csv_name, "economy": economy or run_state.get("economy", ""),
        "pillar": pillar if pillar is not None else run_state.get("pillar"),
        "records": len(rows),
        "act_tag": act_tag, "act_provisions": act_provisions,
        "known_acts": known_acts, "new_acts": new_acts,
        "prov_known": known_provisions, "prov_new": len(rows) - known_provisions,
        "doc_rows": doc_rows,
        "n_pdf": sum(1 for d in doc_rows if d["kind"] == "PDF"),
        "n_html": sum(1 for d in doc_rows if d["kind"] == "HTML"),
        "n_ocr": sum(1 for d in doc_rows if d["scanned"]),
        "discovered": run_state.get("total_docs") or None,
        "attempted": len(run_state.get("docs") or {}) or None,
        "failed": sum(1 for d in (run_state.get("docs") or {}).values()
                      if d.get("status") == "warn"),
        "cost": cost, "comps": components,
        "total_cost": cost.get("total_cost_usd", 0.0) or 0.0,
        "llm": components.get("llm", {}),
        "pages": components.get("ocr", {}).get("pages_processed", 0),
        "synthetic_cost": bool(cost.get("synthetic")),
        "elapsed": run_state.get("elapsed") or (
            f"{(cost.get('processing_time_seconds') or 0) / 60:.1f} min"
            if cost.get("processing_time_seconds") else ""),
        "provider": run_state.get("provider") or cost.get("model_version", ""),
    }


# ── Shared fragments (identical values in HTML and Markdown) ─────────────────

def _report_header_line(d: dict) -> tuple[str, str, str]:
    """(title, pillar hint, meta line) shared by both renderers."""
    where = f"{d['economy']} — Pillar {d['pillar']}"
    hint = pillar_hint(d["pillar"]) if d["pillar"] else ""
    meta = " · ".join(x for x in [
        f"Completed {datetime.now():%d %b %Y, %H:%M}",
        d["elapsed"], d["provider"], d["csv_name"]] if x)
    return where, hint, meta


def _queued_count(d: dict) -> int:
    return d["discovered"] or d["attempted"] or len(d["doc_rows"])


# ── HTML renderer ─────────────────────────────────────────────────────────────

def render_report_html(d: dict) -> str:
    """Document-style run report (styled HTML) — shown live on the Run screen."""
    def kpi(label: str, value: str) -> str:
        return (f'<div class="rdr-kpi"><div class="k">{esc(label)}</div>'
                f'<div class="v">{value}</div></div>')

    kpis = "".join([
        kpi("Total cost", f"${d['total_cost']:.4f}"),
        kpi("Records written", f"{d['records']}"),
        kpi("Acts used", f"{len(d['act_tag'])} "
                         f"<small>{len(d['known_acts'])}K / {len(d['new_acts'])}N</small>"),
        kpi("Documents used", f"{len(d['doc_rows'])}"
            + (f" <small>of {d['discovered']}</small>" if d['discovered'] else "")),
        kpi("Runtime", d["elapsed"] or "—"),
    ])

    if d["act_tag"]:
        act_body = "".join(
            f'<tr><td>{esc(act)}</td>'
            f'<td><span class="rdr-tag {"known" if d["act_tag"][act] == "KNOWN" else "new"}">'
            f'{esc(d["act_tag"][act] or "NEW")}</span></td>'
            f'<td class="num">{d["act_provisions"].get(act, 0)}</td></tr>'
            for act in sorted(d["act_tag"], key=lambda a: (d["act_tag"][a] != "KNOWN", a))
        )
        acts_table = ('<table class="rdr-t"><thead><tr><th>Act</th><th>Provision tag</th>'
                      '<th class="num">Provisions</th></tr></thead>'
                      f'<tbody>{act_body}</tbody></table>')
    else:
        acts_table = '<p class="rdr-lead">No acts produced provisions in this run.</p>'

    if d["doc_rows"]:
        doc_body = "".join(
            f'<tr><td>{esc(doc["name"])}</td><td>{esc(doc["kind"])}'
            f'{" · OCR" if doc["scanned"] else ""}</td>'
            f'<td><span class="rdr-tag '
            f'{"known" if doc["tag"] == "KNOWN" else "new" if doc["tag"] else "none"}">'
            f'{esc(doc["tag"] or "—")}</span></td>'
            f'<td class="num">{doc["provisions"]}</td></tr>'
            for doc in d["doc_rows"]
        )
        docs_table = ('<table class="rdr-t"><thead><tr><th>Document</th><th>Fetched as</th>'
                      '<th>Document tag</th><th class="num">Provisions</th></tr></thead>'
                      f'<tbody>{doc_body}</tbody></table>')
    else:
        docs_table = '<p class="rdr-lead">No documents contributed provisions.</p>'

    cost_body = "".join(
        f'<tr><td>{esc(name.upper())}</td><td>{comp.get("calls", 0)}</td>'
        f'<td>{esc(str(comp.get("pages_processed", "—")) if name == "ocr" else "—")}</td>'
        f'<td class="num">${comp.get("cost_usd", 0.0) or 0.0:.4f}</td></tr>'
        for name, comp in d["comps"].items()
    )
    cost_table = ('<table class="rdr-t"><thead><tr><th>Component</th><th>Calls</th><th>Pages</th>'
                  '<th class="num">Cost (USD)</th></tr></thead><tbody>'
                  f'{cost_body}<tr class="rdr-total"><td>Total</td><td></td><td></td>'
                  f'<td class="num">${d["total_cost"]:.4f}</td></tr></tbody></table>')

    where, hint, meta = _report_header_line(d)
    queued = _queued_count(d)

    return (
        REPORT_CSS + '<div class="rdr"><div class="rdr-doc">'
        '<div class="rdr-eyebrow">RDTII Extraction Engine · Run report</div>'
        f'<h2>{esc(where)}{f" · {esc(hint)}" if hint else ""}</h2>'
        f'<div class="rdr-sub">{esc(meta)}</div>'
        f'<div class="rdr-kpis">{kpis}</div><div class="rdr-hr"></div>'

        '<div class="rdr-sec"><h3><em>1</em>Acts found — KNOWN vs NEW</h3>'
        f'<p class="rdr-lead">The output carries <b>{d["prov_known"]}</b> KNOWN and '
        f'<b>{d["prov_new"]}</b> NEW provision(s) across <b>{len(d["act_tag"])}</b> act(s) — '
        f'<b>{len(d["known_acts"])}</b> with at least one KNOWN provision, '
        f'<b>{len(d["new_acts"])}</b> with only NEW ones. Counts are taken from the final '
        f'output file. KNOWN means the provision matches a Round 1 entry; NEW is engine-found.</p>'
        f'{acts_table}</div>'

        '<div class="rdr-sec"><h3><em>2</em>Documents fetched and used</h3>'
        f'<p class="rdr-lead">{queued} document(s)'
        f'{f", {d['attempted']} attempted" if d['attempted'] else ""}, '
        f'<b>{len(d["doc_rows"])}</b> produced provisions'
        f'{f" ({d['failed']} fetch issue(s))" if d['failed'] else ""} — '
        f'<b>{d["n_pdf"]}</b> PDF, <b>{d["n_html"]}</b> HTML'
        f'{f", {d['n_ocr']} scanned (image) document(s)" if d['n_ocr'] else ", none scanned"}. '
        f'A KNOWN document can still yield NEW provisions when those sections are not in Round 1.</p>'
        f'{docs_table}</div>'

        '<div class="rdr-sec"><h3><em>3</em>Cost report</h3>'
        f'<p class="rdr-lead">{"Synthetic placeholder — per-run cost was not recorded for this run. "
                              if d["synthetic_cost"] else "Measured spend for this run — "}'
        f'{d["llm"].get("input_tokens", 0):,} input and {d["llm"].get("output_tokens", 0):,} '
        f'output tokens'
        f'{f", {d['pages']} page(s) through text extraction / OCR" if d['pages'] else ""}.</p>'
        f'{cost_table}</div>'

        '<p class="rdr-note">Figures come from this run\'s own output files and '
        f'{esc(cost_report_path(d["csv_name"]).name)} — saved per run.</p>'
        '</div></div>'
    )


# ── Markdown renderer ─────────────────────────────────────────────────────────

def render_report_markdown(d: dict) -> str:
    """Same run report as Markdown — this is what gets saved to <run>_runReport.md
    and rendered in the Results › Run report tab."""
    where, hint, meta = _report_header_line(d)
    lines = [f"# Run report — {where}" + (f" · {hint}" if hint else ""), "",
             f"_{meta}_", ""]

    lines += [
        "| Metric | Value |", "|---|---|",
        f"| Total cost | ${d['total_cost']:.4f} |",
        f"| Records written | {d['records']} |",
        f"| Acts used | {len(d['act_tag'])} "
        f"({len(d['known_acts'])} KNOWN / {len(d['new_acts'])} NEW) |",
        f"| Documents used | {len(d['doc_rows'])}"
        + (f" of {d['discovered']}" if d['discovered'] else "") + " |",
        f"| Runtime | {d['elapsed'] or '—'} |",
        "",
    ]

    lines += ["## 1. Acts found — KNOWN vs NEW", ""]
    lines += [
        f"The output carries **{d['prov_known']}** KNOWN and **{d['prov_new']}** NEW "
        f"provision(s) across **{len(d['act_tag'])}** act(s) — "
        f"**{len(d['known_acts'])}** with at least one KNOWN provision, "
        f"**{len(d['new_acts'])}** with only NEW ones. Counts come from the final output file.",
        "",
    ]
    if d["act_tag"]:
        lines += ["| Act | Provision tag | Provisions |", "|---|---|---:|"]
        for act in sorted(d["act_tag"], key=lambda a: (d["act_tag"][a] != "KNOWN", a)):
            lines.append(f"| {act} | {d['act_tag'][act] or 'NEW'} | "
                         f"{d['act_provisions'].get(act, 0)} |")
    else:
        lines.append("_No acts produced provisions in this run._")
    lines.append("")

    lines += ["## 2. Documents fetched and used", ""]
    lines += [
        f"{_queued_count(d)} document(s)"
        + (f", {d['attempted']} attempted" if d['attempted'] else "")
        + f", **{len(d['doc_rows'])}** produced provisions"
        + (f" ({d['failed']} fetch issue(s))" if d['failed'] else "")
        + f" — **{d['n_pdf']}** PDF, **{d['n_html']}** HTML"
        + (f", {d['n_ocr']} scanned" if d['n_ocr'] else ", none scanned") + ".",
        "",
    ]
    if d["doc_rows"]:
        lines += ["| Document | Fetched as | Document tag | Provisions |", "|---|---|---|---:|"]
        for doc in d["doc_rows"]:
            kind = doc["kind"] + (" · OCR" if doc["scanned"] else "")
            lines.append(f"| {doc['name']} | {kind} | {doc['tag'] or '—'} | "
                         f"{doc['provisions']} |")
    else:
        lines.append("_No documents contributed provisions._")
    lines.append("")

    lines += ["## 3. Cost report", ""]
    lines += [
        ("Synthetic placeholder — per-run cost was not recorded for this run. "
         if d["synthetic_cost"] else "Measured spend for this run — ")
        + f"{d['llm'].get('input_tokens', 0):,} input and {d['llm'].get('output_tokens', 0):,} "
          "output tokens"
        + (f", {d['pages']} page(s) through text extraction / OCR" if d['pages'] else "") + ".",
        "",
        "| Component | Calls | Pages | Cost (USD) |", "|---|---:|---:|---:|",
    ]
    for name, comp in d["comps"].items():
        pages = comp.get("pages_processed", "—") if name == "ocr" else "—"
        lines.append(f"| {name.upper()} | {comp.get('calls', 0)} | {pages} | "
                     f"${comp.get('cost_usd', 0.0) or 0.0:.4f} |")
    lines.append(f"| **Total** | | | **${d['total_cost']:.4f}** |")
    lines += ["", f"_Figures from this run's own output files and "
                  f"{cost_report_path(d['csv_name']).name} — saved per run._"]
    return "\n".join(lines)


# ── Persistence & backfill ────────────────────────────────────────────────────

def persist_run_artifacts(
    csv_name: str,
    run_state: dict,
    user_hash: str | None = None,
    source_cost_path: Path | None = None,
) -> None:
    """On a completed run: snapshot the shared cost report to a per-run file, then
    write the run-report markdown alongside the CSV."""
    source_cost_path = source_cost_path or SHARED_COST_REPORT
    if source_cost_path.exists():
        try:
            cost_report_path(csv_name, user_hash).write_text(
                source_cost_path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
    try:
        run_report_path(csv_name, user_hash).write_text(
            render_report_markdown(gather_report_data(csv_name, run_state, user_hash)),
            encoding="utf-8")
    except Exception:
        pass


def ensure_run_artifacts(csv_name: str, use_shared_cost: bool = False) -> None:
    """Backfill the per-run cost JSON and run-report markdown for an existing run
    that predates per-run artifacts, so the Results tabs have something to show."""
    cost_path = cost_report_path(csv_name)
    if not cost_path.exists():
        cost = read_json(SHARED_COST_REPORT) if use_shared_cost else None
        cost_path.write_text(
            json.dumps(cost or placeholder_cost_report(csv_name), indent=2),
            encoding="utf-8")
    report_path = run_report_path(csv_name)
    if not report_path.exists():
        try:
            report_path.write_text(
                render_report_markdown(gather_report_data(csv_name)), encoding="utf-8")
        except Exception:
            pass


def backfill_all_runs() -> None:
    """Ensure every existing run has its per-run cost + report files. The shared
    logs/cost_report.json is attributed to the newest run it matches; the rest get
    a clearly-labelled synthetic placeholder."""
    runs = list_runs()
    shared_cost_owner = None
    shared = read_json(SHARED_COST_REPORT)
    if shared:
        shared_economy = str(shared.get("economy", "")).lower()
        shared_pillar = shared.get("pillar")
        for run in runs:  # newest first
            economy, pillar = parse_run_name(run)
            if economy and economy.lower() == shared_economy \
                    and shared_pillar is not None and int(pillar) == int(shared_pillar):
                shared_cost_owner = run
                break
    for run in runs:
        ensure_run_artifacts(run, use_shared_cost=(run == shared_cost_owner))


def run_report_markdown_text(csv_name: str, user_hash: str | None = None) -> str:
    """Saved run-report markdown for a run (generated on the fly if absent)."""
    if not csv_name:
        return "_Select a run to view its report._"
    report_path = run_report_path(csv_name, user_hash)
    if report_path.exists():
        return report_path.read_text(encoding="utf-8")
    try:
        return render_report_markdown(gather_report_data(csv_name, user_hash=user_hash))
    except Exception as exc:
        return f"_Could not build report: {exc}_"


# ── Cost report (visual) ──────────────────────────────────────────────────────

def render_cost_empty(message: str = "No cost report available yet.") -> str:
    return (
        COST_CSS
        + '<div class="rdc"><div class="rdc-empty">'
        + esc(message)
        + "</div></div>"
    )


def render_cost_report(cost_path: Path | str | None = None) -> str:
    """Tile + bar visual of one cost report (per-run file, or the shared one)."""
    path = Path(cost_path) if cost_path else SHARED_COST_REPORT
    if not path.exists():
        return render_cost_empty("No cost report for this run.")
    report = read_json(path)
    if report is None:
        return (COST_CSS + '<div class="rdc"><div class="rdc-empty">'
                f'Could not read cost report: {esc(path.name)}</div></div>')

    components = report.get("components", {})
    total = report.get("total_cost_usd", 0.0) or 0.0
    max_cost = max([c.get("cost_usd", 0.0) for c in components.values()] + [1e-9])
    llm = components.get("llm", {})
    minutes = (report.get("processing_time_seconds") or 0) / 60
    pages = components.get("ocr", {}).get("pages_processed", 0)
    per_page = f"${total / pages:.4f}" if pages else "—"

    def tile(label: str, value: str, hero: bool = False) -> str:
        return (f'<div class="rdc-tile{" hero" if hero else ""}">'
                f'<div class="rdc-k">{esc(label)}</div>'
                f'<div class="rdc-v">{value}</div></div>')

    tiles = "".join([
        tile("Total cost", f"${total:.4f}", hero=True),
        tile("Cost / page", per_page),
        tile("Economy · Pillar",
             f"{esc(str(report.get('economy', '?')).title())} "
             f"<small>P{report.get('pillar', '?')}</small>"),
        tile("Model", f"<span style='font-size:16px'>"
                      f"{esc(str(report.get('model_version', '?')))}</span>"),
        tile("Runtime", f"{minutes:.1f}<small> min</small>"),
        tile("LLM tokens", f"{llm.get('input_tokens', 0):,}<small> in · "
                           f"{llm.get('output_tokens', 0):,} out</small>"),
    ])

    bars = ""
    for name, comp in components.items():
        cost = comp.get("cost_usd", 0.0) or 0.0
        pct = max(int(cost / max_cost * 100), 1) if cost else 0
        detail = f"{comp.get('calls', 0)} calls"
        if name == "ocr":
            detail += f" · {comp.get('pages_processed', 0)} pages"
        if comp.get("latency_ms"):
            detail += f" · {comp['latency_ms'] / 1000:.1f}s"
        bars += (
            f'<div class="rdc-bar"><div class="rdc-bl">'
            f'<span><b>{esc(name.upper())}</b> &nbsp;{esc(detail)}</span>'
            f'<span class="amt">${cost:.4f}</span></div>'
            f'<div class="rdc-track"><div class="rdc-fill" style="width:{pct}%"></div></div></div>'
        )

    source = ("synthetic placeholder — per-run cost was not recorded"
              if report.get("synthetic")
              else f"source: {esc(path.name)} — actual measured spend, not an estimate")
    return (
        COST_CSS + '<div class="rdc">'
        f'<div class="rdc-tiles">{tiles}</div>'
        f'<div class="rdc-panel"><div class="rdc-h">Cost by component</div>{bars}'
        f'<div class="rdc-foot">Generated {esc(str(report.get("generated_at", "?")))} '
        f'· {source}.</div></div></div>'
    )
