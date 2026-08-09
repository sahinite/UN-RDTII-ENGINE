"""
Non-dismissible Google Sign-In modal for the RDTII UI.

The modal is a fixed HTML overlay that hosts a Google Identity Services (GIS)
button. The GIS callback runs in the browser, receives a JWT id_token, and has
to hand it to Gradio's server-side handler. Gradio doesn't ship a "call a
Python function with this string" bridge, so this module fakes one: JS writes
the token into a hidden Textbox, dispatches an 'input' event so Gradio's
change-detection notices it, then programmatically clicks a hidden Button that
has a .click handler bound to `handle_sign_in`.

Wave 5A owns the actual wiring of these components into `app.py`.
"""
from __future__ import annotations

import os
from typing import Any

import gradio as gr

from src.auth import db, dev_bypass, session
from .header import render_app_header

TAGLINE = "Law/Regulations and Provisions Extraction engine on UN RDTII Framework"

# CLIENT_ID is resolved once at import so a misconfigured deployment fails loud
# rather than crashing on first sign-in attempt. Dev bypass sidesteps GIS
# entirely, so it's allowed to run without a real client id.
_CLIENT_ID: str
_raw_client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
if _raw_client_id:
    _CLIENT_ID = _raw_client_id
elif dev_bypass.is_bypass_enabled():
    _CLIENT_ID = "dev-bypass"
else:
    raise RuntimeError("GOOGLE_OAUTH_CLIENT_ID is required")


# Inline SVG silhouette — used when Google returns no picture URL so the header
# never shows a broken-image icon.
_DEFAULT_AVATAR = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'>"
    "<circle cx='12' cy='8' r='4' fill='%23999'/>"
    "<path d='M4 22c0-4 4-7 8-7s8 3 8 7z' fill='%23999'/>"
    "</svg>"
)


def profile_picture_src(ctx: Any) -> str:
    return str(getattr(ctx, "picture_url", "") or _DEFAULT_AVATAR)


def render_overlay_html(error_message: str = "") -> str:
    error_block = (
        f'<div class="auth-error">{_html_escape(error_message)}</div>'
        if error_message
        else ""
    )
    return f"""
<style>
  #rdtii-auth-backdrop {{
    position: fixed; inset: 0; background: rgba(0,0,0,.55);
    z-index: 9999; display: flex; align-items: center; justify-content: center;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }}
  #rdtii-auth-card {{
    width: 380px; max-width: 92vw; background: #fff !important; color: #1f2937 !important;
    border-radius: 12px; padding: 28px 24px 24px; text-align: center;
    box-shadow: 0 20px 60px rgba(0,0,0,.35);
  }}
  #rdtii-auth-card h2 {{ margin: 0 0 6px; font-size: 20px; color: #111827 !important; }}
  #rdtii-auth-card .tagline {{ margin: 0 0 20px; font-size: 13px; color: #4b5563 !important; }}
  #rdtii-auth-card #g_id_signin {{ display: flex; justify-content: center; margin: 8px 0; }}
  #rdtii-auth-card iframe {{ color-scheme: light; filter: none !important; opacity: 1 !important; }}
  #rdtii-auth-card .auth-error {{
    margin-top: 14px; padding: 8px 10px; border-radius: 6px;
    background: #fdecea; color: #b3261e; font-size: 13px;
  }}
</style>
<div id="rdtii-auth-backdrop">
  <div id="rdtii-auth-card">
    <h2>RDTII Extraction Engine</h2>
    <p class="tagline">{_html_escape(TAGLINE)}</p>
    <div id="g_id_signin"></div>
    {error_block}
  </div>
</div>
""".strip()


def gis_head_html() -> str:
    return '<script src="https://accounts.google.com/gsi/client" async defer></script>'


def js_init_gis_button() -> str:
    # The GIS callback fires in the browser after the user picks an account.
    # From there we bridge into Gradio by writing the token into a hidden Textbox
    # and clicking a hidden Button with the Python-side handler.
    return f"""
() => {{
  function showAuthError(message) {{
    var card = document.getElementById('rdtii-auth-card');
    if (!card) return;
    var existing = card.querySelector('.auth-error');
    if (!existing) {{
      existing = document.createElement('div');
      existing.className = 'auth-error';
      card.appendChild(existing);
    }}
    existing.textContent = message;
  }}
  if (!window.__rdtiiConsoleWatched) {{
    window.__rdtiiConsoleWatched = true;
    var originalError = console.error.bind(console);
    console.error = function() {{
      var text = Array.prototype.slice.call(arguments).map(String).join(' ');
      if (text.indexOf('origin is not allowed') !== -1 && text.indexOf('client ID') !== -1) {{
        showAuthError('Google sign-in is not configured for ' + window.location.origin + '.');
      }}
      originalError.apply(console, arguments);
    }};
  }}
  window.__rdtiiHandleCredential = function(resp) {{
    try {{
      var token = resp && resp.credential;
      if (!token) return;
      var tokenWrap = document.getElementById('rdtii_auth_token');
      if (!tokenWrap) return;
      var input = tokenWrap.querySelector('input, textarea') || tokenWrap;
      input.value = token;
      input.dispatchEvent(new Event('input', {{ bubbles: true }}));
      var trigWrap = document.getElementById('rdtii_auth_trigger');
      var btn = trigWrap ? (trigWrap.querySelector('button') || trigWrap) : null;
      setTimeout(function() {{ if (btn) btn.click(); }}, 50);
    }} catch (e) {{ console.error('rdtii sign-in bridge failed', e); }}
  }};
  function initGIS() {{
    if (!(window.google && window.google.accounts && window.google.accounts.id)) {{
      return setTimeout(initGIS, 100);
    }}
    var mount = document.getElementById('g_id_signin');
    if (!mount || mount.dataset.rdtiiRendered === '1') return;
    google.accounts.id.initialize({{
      client_id: '{_CLIENT_ID}',
      callback: window.__rdtiiHandleCredential,
    }});
    google.accounts.id.renderButton(mount, {{
      theme: 'filled_blue',
      size: 'large',
      type: 'standard',
      text: 'signin_with',
      shape: 'rectangular'
    }});
    mount.dataset.rdtiiRendered = '1';
  }}
  // Gradio replaces the HTML inside the overlay whenever it is opened. Watch
  // for that replacement so a freshly-created GIS mount is initialized even
  // when a server event (such as Run Pipeline) opens the modal.
  if (!window.__rdtiiGISMountWatcher) {{
    window.__rdtiiGISMountWatcher = true;
    var observer = new MutationObserver(function() {{ initGIS(); }});
    observer.observe(document.body, {{ childList: true, subtree: true }});
  }}
  initGIS();
}}
""".strip()


def render_header_html(ctx: Any) -> str:
    if ctx is None:
        return '<div class="rd-auth-header"><span class="rd-auth-anon">Not signed in</span></div>'
    picture = profile_picture_src(ctx)
    return (
        '<div class="rd-auth-header">'
        f'<img src="{_html_escape(picture)}" class="rd-avatar"/>'
        f'<span class="rd-user-name">{_html_escape(ctx.name)}</span>'
        f'<span class="rd-user-email">{_html_escape(ctx.email)}</span>'
        '</div>'
    )


def render_full_header_html(ctx: Any) -> str:
    """Keep the user control within the same layout as the app brand bar."""
    return render_app_header(render_header_html(ctx))


def build_auth_modal() -> dict:
    # elem_id is what the browser JS uses to locate these components — do NOT
    # rename them here without updating render_overlay_html().
    overlay_html = gr.HTML(
        render_overlay_html(), elem_id="rdtii_auth_overlay", visible=False
    )
    token_input = gr.Textbox(
        elem_id="rdtii_auth_token", visible=False, label="__rdtii_token"
    )
    sign_in_trigger_button = gr.Button(
        "sign-in", elem_id="rdtii_auth_trigger", visible=False
    )
    sign_out_button = gr.Button(
        "sign-out", elem_id="rdtii_auth_signout", visible=False
    )
    return {
        "overlay_html": overlay_html,
        "token_input": token_input,
        "sign_in_trigger_button": sign_in_trigger_button,
        "sign_out_button": sign_out_button,
        # Wave 5A supplies the full 5-tuple (auth_state, header_html,
        # overlay_html, settings_tab, configure_tab). Only overlay_html lives
        # here; the rest are exposed by build_app() and wired at that layer.
        "auth_state_output_signals": (overlay_html,),
    }


def handle_sign_in(token: str, current_ctx: Any):
    admin_raw = os.environ.get("ADMIN_EMAILS", "")
    admin_emails = [e.strip() for e in admin_raw.split(",") if e.strip()]

    if dev_bypass.is_bypass_enabled():
        google_user = dev_bypass.get_dev_user()
    else:
        # Lazy import so this module can be imported (and its render helpers
        # exercised) in dev-bypass mode without a real OAuth client id, since
        # src.auth.google_oauth raises at its own import time when it's missing.
        from src.auth import google_oauth

        google_user = google_oauth.verify_id_token(token)
        if google_user is None:
            return (
                None,
                render_full_header_html(None),
                gr.update(
                    value=render_overlay_html("Sign-in failed. Please try again."),
                    visible=True,
                ),
                gr.update(visible=False),
                gr.update(visible=False),
            )

    db.init_db(db.DEFAULT_DB_PATH)
    conn = db.get_connection(db.DEFAULT_DB_PATH)
    try:
        session.upsert_user_from_google(google_user, conn)
        ctx = session.load_run_context(google_user.email, conn, admin_emails)
    finally:
        conn.close()

    is_admin = bool(ctx and ctx.is_admin)
    return (
        ctx,
        render_full_header_html(ctx),
        gr.update(visible=False),
        gr.update(visible=True),
        gr.update(visible=is_admin),
    )


def handle_sign_out(current_ctx: Any):
    return (
        None,
        render_full_header_html(None),
        gr.update(value=render_overlay_html(), visible=True),
        gr.update(visible=False),
        gr.update(visible=False),
    )


def js_persist_token_on_success() -> str:
    # Runs after handle_sign_in returns; reads the token from the same hidden
    # input the GIS bridge wrote into so we don't have to pipe it through
    # Gradio's return payload.
    return (
        "() => { try { "
        "const w = document.getElementById('rdtii_auth_token'); "
        "const t = w && (w.querySelector('input, textarea') || w).value; "
        "if (t) localStorage.setItem('rdtii_id_token', t); "
        "} catch(e) {} }"
    )


def js_restore_token_on_load() -> str:
    # Runs on app.load. If a token is cached, replay the same JS-to-Python
    # handoff the GIS callback uses so the server re-verifies it and rebuilds
    # the RunContext without asking the user to click again.
    return (
        "() => { try { "
        "const t = localStorage.getItem('rdtii_id_token'); "
        "if (!t) return; "
        "const w = document.getElementById('rdtii_auth_token'); "
        "if (!w) return; "
        "const input = w.querySelector('input, textarea') || w; "
        "input.value = t; "
        "input.dispatchEvent(new Event('input', { bubbles: true })); "
        "const trigWrap = document.getElementById('rdtii_auth_trigger'); "
        "const btn = trigWrap && (trigWrap.querySelector('button') || trigWrap); "
        "setTimeout(() => { if (btn) btn.click(); }, 50); "
        "} catch(e) {} }"
    )


def js_clear_token_on_signout() -> str:
    return "() => { try { localStorage.removeItem('rdtii_id_token'); } catch(e) {} }"


def _html_escape(text: str) -> str:
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
