"""
App header: brand bar plus the light/dark theme toggle.

Gradio scopes custom CSS under .gradio-container, so a `body.dark` CSS selector in
APP_CSS would never match — the toggle button's label is therefore driven from JS.
The toggle also sets `documentElement.style.colorScheme` so the page area outside
Gradio's container (and native form controls) follows the chosen theme, and the
choice is persisted in localStorage.
"""

_THEME_TOGGLE_JS = (
    "var d=document.body.classList.toggle('dark');"
    "document.documentElement.style.colorScheme=d?'dark':'light';"
    "try{localStorage.setItem('rdtii-theme',d?'dark':'light');}catch(e){}"
    "this.textContent=d?'\\u2600 Light mode':'\\u263E Dark mode';"
)

def render_app_header(auth_html: str = "") -> str:
    """Render the brand bar and its optional authenticated-user control."""
    return f"""
<header class="rd-top">
  <div class="rd-brand">
    <div class="rd-mark">RD</div>
    <div class="rd-brand-copy">
      <h1>RDTII Extraction Engine</h1>
      <p>Regulatory evidence discovery and indicator mapping for digital trade</p>
    </div>
  </div>
  <div class="rd-tags">
    <span class="rd-tag">Zone 1 · Discovery</span>
    <span class="rd-tag">Zone 2 · Mapping</span>
    <button class="rd-theme" title="Switch between light and dark" onclick="{_THEME_TOGGLE_JS}">
      &#9790; Dark mode
    </button>
  </div>
  {auth_html}
</header>
"""


# Kept as the anonymous default for callers that render the shell without an
# auth component. The app replaces it with its live user-aware header.
APP_HEADER = render_app_header()

# Runs once on page load: restore the saved theme and sync the toggle label.
RESTORE_THEME_JS = """
() => {
    try {
        const t = localStorage.getItem('rdtii-theme');
        if (t === 'dark') document.body.classList.add('dark');
        if (t === 'light') document.body.classList.remove('dark');
    } catch (e) {}
    const dark = document.body.classList.contains('dark');
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
    setTimeout(() => {
        const b = document.querySelector('.rd-theme');
        if (b) b.textContent = dark ? '\\u2600 Light mode' : '\\u263E Dark mode';
    }, 60);
}
"""
