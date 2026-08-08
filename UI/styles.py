"""
All CSS for the RDTII UI in one place.

Two kinds of styles live here:
  * APP_CSS       — page-level CSS handed to Gradio's launch(css=...).
  * *_CSS blocks  — inline <style> fragments embedded in HTML that render
                    functions return (pipeline visual, run report, cost report).

The inline blocks share THEME_VARIABLES: a scoped set of custom properties that
map Gradio's own theme variables (so everything tracks light/dark mode) plus the
four status colours. It was previously copy-pasted into every block.
"""

# Custom properties shared by every inline-styled component. The rule is applied
# per root class so each fragment stays self-contained when rendered alone.
_THEME_PROPERTIES = """--rd-bd:var(--border-color-primary,#e3e6ea);
     --rd-fg:var(--body-text-color,#111827);
     --rd-mut:var(--body-text-color-subdued,#6b7280);
     --rd-card:var(--background-fill-primary,#fff);
     --rd-sunk:var(--background-fill-secondary,#f7f8fa);
     --rd-blue:#3b82f6;--rd-green:#10b981;--rd-amber:#f59e0b;--rd-red:#ef4444;
     font-family:var(--font,ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif);
     color:var(--rd-fg);"""


def _theme_scope(root_class: str) -> str:
    """CSS rule installing the shared theme properties under one root class."""
    return f".{root_class}{{{_THEME_PROPERTIES}}}"


# ── Page-level CSS (Gradio launch) ────────────────────────────────────────────

APP_CSS = """
.gradio-container{max-width:1320px !important;
  /* Soft theme paints block labels in the primary hue — too loud next to the
     pipeline visual; make them quiet metadata instead. */
  --block-label-background-fill:transparent;
  --block-label-text-color:var(--body-text-color-subdued);
  --block-label-border-color:transparent;
  --block-title-background-fill:transparent;
  --block-title-text-color:var(--body-text-color-subdued);
  --block-title-border-color:transparent;}
footer{display:none !important;}
.rd-top{display:flex;align-items:center;gap:14px;padding:4px 2px 16px;flex-wrap:wrap;}
.rd-mark{width:38px;height:38px;border-radius:11px;flex:0 0 auto;display:flex;align-items:center;
  justify-content:center;font-size:15px;font-weight:800;letter-spacing:-.03em;color:#fff;
  background:linear-gradient(135deg,#3b82f6,#10b981);box-shadow:0 4px 14px rgba(59,130,246,.3);}
.rd-top h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.02em;line-height:1.2;}
.rd-top p{margin:2px 0 0;font-size:12.5px;color:var(--body-text-color-subdued,#6b7280);}
.rd-tags{margin-left:auto;display:flex;gap:6px;flex-wrap:wrap;}
.rd-tag{font-size:10.5px;font-weight:650;letter-spacing:.05em;text-transform:uppercase;
  padding:4px 10px;border-radius:999px;border:1px solid var(--border-color-primary,#e3e6ea);
  color:var(--body-text-color-subdued,#6b7280);}
.tabitem{padding-top:14px !important;}
.rd-theme{display:inline-flex;align-items:center;gap:6px;cursor:pointer;font:inherit;
  font-size:11px;font-weight:650;letter-spacing:.04em;text-transform:uppercase;
  padding:5px 12px;border-radius:999px;background:transparent;
  border:1px solid var(--border-color-primary,#e3e6ea);
  color:var(--body-text-color-subdued,#6b7280);transition:border-color .2s,color .2s;}
.rd-theme:hover{border-color:#3b82f6;color:#3b82f6;}
"""


# ── Pipeline visual (Run screen) ──────────────────────────────────────────────

PIPELINE_CSS = f"""
<style>
{_theme_scope("rdp")}
.rdp{{line-height:1.35;}}
.rdp *{{box-sizing:border-box;}}
.rdp-hero{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  padding:10px 14px;border:1px solid var(--rd-bd);border-radius:14px;
  background:var(--rd-sunk);margin-bottom:14px;}}
.rdp-pill{{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:700;
  letter-spacing:.04em;text-transform:uppercase;padding:4px 10px;border-radius:999px;
  border:1px solid transparent;}}
.rdp-pill i{{width:7px;height:7px;border-radius:50%;background:currentColor;font-style:normal;}}
.rdp-pill.idle{{color:var(--rd-mut);border-color:var(--rd-bd);}}
.rdp-pill.running{{color:var(--rd-blue);background:rgba(59,130,246,.12);border-color:rgba(59,130,246,.35);}}
.rdp-pill.running i{{animation:rd-blink 1s ease-in-out infinite;}}
.rdp-pill.complete{{color:var(--rd-green);background:rgba(16,185,129,.12);border-color:rgba(16,185,129,.35);}}
.rdp-pill.failed{{color:var(--rd-red);background:rgba(239,68,68,.12);border-color:rgba(239,68,68,.35);}}
.rdp-title{{font-size:14.5px;font-weight:650;letter-spacing:-.01em;}}
.rdp-meta{{margin-left:auto;display:flex;gap:16px;flex-wrap:wrap;}}
.rdp-meta div{{font-size:11px;color:var(--rd-mut);}}
.rdp-meta b{{display:block;font-size:14px;font-weight:650;color:var(--rd-fg);
  font-variant-numeric:tabular-nums;}}
.rdp-sec{{margin-bottom:16px;}}
.rdp-lbl{{display:flex;align-items:center;gap:10px;margin-bottom:9px;}}
.rdp-lbl span{{font-size:10.5px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  color:var(--rd-mut);white-space:nowrap;}}
.rdp-lbl:after{{content:"";flex:1;height:1px;background:var(--rd-bd);}}
.rdp-row{{display:flex;align-items:stretch;flex-wrap:wrap;gap:0;}}
.rdp-conn{{flex:0 0 20px;align-self:center;height:2px;border-radius:2px;
  background:var(--rd-bd);}}
.rdp-conn.on{{background:linear-gradient(90deg,var(--rd-green),var(--rd-blue));}}
.rdp-node{{flex:1 1 128px;min-width:128px;padding:10px 12px;border-radius:12px;
  border:1px solid var(--rd-bd);background:var(--rd-card);
  transition:border-color .25s,box-shadow .25s,transform .25s;}}
.rdp-node .top{{display:flex;align-items:center;justify-content:space-between;margin-bottom:7px;}}
.rdp-dot{{width:19px;height:19px;border-radius:50%;display:inline-flex;align-items:center;
  justify-content:center;font-size:11px;font-weight:700;border:1.5px solid var(--rd-bd);
  color:var(--rd-mut);position:relative;flex:0 0 auto;}}
.rdp-t{{font-size:10.5px;color:var(--rd-mut);font-variant-numeric:tabular-nums;}}
.rdp-name{{font-size:12.5px;font-weight:640;letter-spacing:-.01em;}}
.rdp-note{{font-size:10.5px;color:var(--rd-mut);margin-top:2px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;}}
.rdp-node.done .rdp-dot{{border-color:var(--rd-green);color:var(--rd-green);
  background:rgba(16,185,129,.12);}}
.rdp-node.running{{border-color:var(--rd-blue);box-shadow:0 0 0 3px rgba(59,130,246,.13);}}
.rdp-node.running .rdp-dot{{border-color:rgba(59,130,246,.3);color:var(--rd-blue);}}
.rdp-node.running .rdp-dot:after{{content:"";position:absolute;inset:-1.5px;border-radius:50%;
  border:1.5px solid transparent;border-top-color:var(--rd-blue);animation:rd-spin .75s linear infinite;}}
.rdp-node.warn .rdp-dot{{border-color:var(--rd-amber);color:var(--rd-amber);
  background:rgba(245,158,11,.12);}}
.rdp-node.warn{{border-color:rgba(245,158,11,.45);}}
.rdp-node.fail .rdp-dot{{border-color:var(--rd-red);color:var(--rd-red);background:rgba(239,68,68,.12);}}
.rdp-node.fail{{border-color:rgba(239,68,68,.5);}}
.rdp-node.pending{{opacity:.62;}}
.rdp-loop{{border:1.5px dashed var(--rd-bd);border-radius:16px;padding:12px;
  background:var(--rd-sunk);}}
.rdp-loop.active{{border-color:rgba(59,130,246,.45);}}
.rdp-loopbar{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:11px;}}
.rdp-badge{{display:inline-flex;align-items:center;gap:5px;font-size:10.5px;font-weight:700;
  letter-spacing:.06em;text-transform:uppercase;padding:3px 9px;border-radius:999px;
  color:var(--rd-blue);background:rgba(59,130,246,.12);border:1px solid rgba(59,130,246,.3);}}
.rdp-loop.active .rdp-badge b{{display:inline-block;animation:rd-spin 1.6s linear infinite;}}
.rdp-counter{{font-size:12px;color:var(--rd-mut);font-variant-numeric:tabular-nums;}}
.rdp-counter b{{color:var(--rd-fg);font-size:13.5px;}}
.rdp-track{{flex:1;min-width:110px;height:5px;border-radius:999px;background:var(--rd-bd);
  overflow:hidden;}}
.rdp-fill{{height:100%;border-radius:999px;
  background:linear-gradient(90deg,var(--rd-blue),var(--rd-green));transition:width .4s ease;}}
.rdp-docs{{display:flex;gap:7px;flex-wrap:wrap;margin-top:11px;padding-top:11px;
  border-top:1px solid var(--rd-bd);}}
.rdp-chip{{display:inline-flex;align-items:center;gap:7px;padding:5px 10px;border-radius:999px;
  border:1px solid var(--rd-bd);background:var(--rd-card);font-size:11.5px;max-width:100%;}}
.rdp-chip .n{{width:17px;height:17px;border-radius:50%;display:inline-flex;align-items:center;
  justify-content:center;font-size:10px;font-weight:700;background:var(--rd-bd);
  color:var(--rd-mut);flex:0 0 auto;}}
.rdp-chip .tt{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:190px;}}
.rdp-chip .st{{font-size:10px;color:var(--rd-mut);text-transform:uppercase;letter-spacing:.05em;}}
.rdp-chip.done{{border-color:rgba(16,185,129,.4);}}
.rdp-chip.done .n{{background:rgba(16,185,129,.15);color:var(--rd-green);}}
.rdp-chip.running{{border-color:var(--rd-blue);box-shadow:0 0 0 3px rgba(59,130,246,.12);}}
.rdp-chip.running .n{{background:rgba(59,130,246,.15);color:var(--rd-blue);}}
.rdp-chip.running .st{{color:var(--rd-blue);font-weight:650;}}
.rdp-chip.warn{{border-color:rgba(245,158,11,.45);}}
.rdp-chip.warn .n{{background:rgba(245,158,11,.15);color:var(--rd-amber);}}
@keyframes rd-spin{{to{{transform:rotate(360deg)}}}}
@keyframes rd-blink{{0%,100%{{opacity:1}}50%{{opacity:.25}}}}
</style>
"""


# ── Run report (document view) ────────────────────────────────────────────────

REPORT_CSS = f"""
<style>
{_theme_scope("rdr")}
.rdr-doc{{border:1px solid var(--rd-bd);border-radius:16px;background:var(--rd-card);
  padding:26px 30px;max-width:940px;box-shadow:0 1px 3px rgba(0,0,0,.06);}}
.rdr-eyebrow{{font-size:10.5px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;
  color:var(--rd-mut);}}
.rdr-doc h2{{margin:6px 0 3px;font-size:24px;font-weight:680;letter-spacing:-.02em;}}
.rdr-sub{{font-size:12.5px;color:var(--rd-mut);}}
.rdr-hr{{height:1px;background:var(--rd-bd);margin:20px 0;}}
.rdr-kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:10px;
  margin:18px 0 4px;}}
.rdr-kpi{{border:1px solid var(--rd-bd);border-radius:12px;padding:11px 13px;background:var(--rd-sunk);}}
.rdr-kpi .k{{font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:var(--rd-mut);margin-bottom:4px;}}
.rdr-kpi .v{{font-size:19px;font-weight:680;font-variant-numeric:tabular-nums;letter-spacing:-.02em;}}
.rdr-kpi .v small{{font-size:12px;font-weight:500;color:var(--rd-mut);}}
.rdr-sec{{margin-top:24px;}}
.rdr-sec h3{{margin:0 0 4px;font-size:13px;font-weight:700;letter-spacing:.03em;}}
.rdr-sec h3 em{{font-style:normal;color:var(--rd-mut);font-weight:600;margin-right:7px;}}
.rdr-lead{{font-size:12px;color:var(--rd-mut);margin:0 0 11px;}}
table.rdr-t{{width:100%;border-collapse:collapse;font-size:12.5px;}}
table.rdr-t th{{text-align:left;font-size:10px;font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;color:var(--rd-mut);padding:0 10px 7px 0;border-bottom:1px solid var(--rd-bd);}}
table.rdr-t td{{padding:8px 10px 8px 0;border-bottom:1px solid var(--rd-bd);vertical-align:top;}}
table.rdr-t tr:last-child td{{border-bottom:none;}}
table.rdr-t td.num{{text-align:right;font-variant-numeric:tabular-nums;padding-right:0;}}
table.rdr-t th.num{{text-align:right;padding-right:0;}}
.rdr-tag{{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.06em;
  padding:2px 8px;border-radius:999px;}}
.rdr-tag.known{{color:#10b981;background:rgba(16,185,129,.13);}}
.rdr-tag.new{{color:#3b82f6;background:rgba(59,130,246,.13);}}
.rdr-tag.none{{color:var(--rd-mut);background:var(--rd-sunk);}}
.rdr-total td{{font-weight:700;border-top:1.5px solid var(--rd-bd);border-bottom:none !important;
  padding-top:10px;}}
.rdr-note{{font-size:11px;color:var(--rd-mut);margin-top:14px;}}
</style>
"""


# ── Cost report ───────────────────────────────────────────────────────────────

COST_CSS = f"""
<style>
{_theme_scope("rdc")}
.rdc-tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px;
  margin-bottom:18px;}}
.rdc-tile{{border:1px solid var(--rd-bd);border-radius:14px;padding:13px 15px;background:var(--rd-card);}}
.rdc-tile.hero{{background:linear-gradient(135deg,rgba(59,130,246,.13),rgba(16,185,129,.10));
  border-color:rgba(59,130,246,.3);}}
.rdc-k{{font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:var(--rd-mut);margin-bottom:5px;}}
.rdc-v{{font-size:22px;font-weight:680;letter-spacing:-.02em;font-variant-numeric:tabular-nums;}}
.rdc-v small{{font-size:13px;font-weight:500;color:var(--rd-mut);}}
.rdc-panel{{border:1px solid var(--rd-bd);border-radius:14px;padding:14px 16px;
  background:var(--rd-card);max-width:720px;}}
.rdc-h{{font-size:11px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;
  color:var(--rd-mut);margin-bottom:12px;}}
.rdc-bar{{margin-bottom:13px;}}
.rdc-bar:last-child{{margin-bottom:0;}}
.rdc-bl{{display:flex;justify-content:space-between;align-items:baseline;font-size:12px;
  margin-bottom:5px;gap:10px;}}
.rdc-bl b{{font-size:12.5px;font-weight:650;}}
.rdc-bl span{{color:var(--rd-mut);}}
.rdc-bl .amt{{font-variant-numeric:tabular-nums;font-weight:650;color:var(--rd-fg);}}
.rdc-track{{height:7px;border-radius:999px;background:var(--rd-sunk);overflow:hidden;
  border:1px solid var(--rd-bd);}}
.rdc-fill{{height:100%;border-radius:999px;background:linear-gradient(90deg,#3b82f6,#10b981);}}
.rdc-foot{{font-size:10.5px;color:var(--rd-mut);margin-top:12px;}}
.rdc-empty{{border:1.5px dashed var(--rd-bd);border-radius:14px;padding:26px;text-align:center;
  color:var(--rd-mut);font-size:13px;}}
</style>
"""
