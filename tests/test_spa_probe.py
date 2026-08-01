"""
Tests for the shared SPA-vs-SSR probe (ADR-045 / unified-portal-strategy.md D6).

The classifier is calibrated against real ground truth (validated live):
  - AU FRL (Angular SPA): ~1770 chars of nav chrome + `ng-version` → SPA
  - SG SSO browse (SSR): ~7309 chars real content, no markers → SSR
Length alone is insufficient (AU's shell is well above any length threshold), so
these tests pin the marker-primary / length-secondary logic and its edge cases.
"""

from __future__ import annotations

from src.crawler.spa_probe import classify_render

_LONG = "Section 1. " + ("The Act provides for the protection of personal data. " * 40)


class TestClassifyRender:
    def test_angular_spa_detected_via_ng_version(self):
        """AU FRL case: chrome text is long, but ng-version marks it a SPA."""
        html = f"<html><body ng-version='17.0'><nav>{'Help Register Sign in ' * 30}</nav><app-root></app-root></body></html>"
        r = classify_render(html)
        assert r.mode == "spa"
        assert "ng-version" in r.spa_markers

    def test_react_empty_mount_detected(self):
        html = "<html><body><div id='root'></div><script src='/app.js'></script></body></html>"
        r = classify_render(html)
        assert r.mode == "spa"
        assert any("root" in m for m in r.spa_markers)

    def test_enable_javascript_marker_detected(self):
        html = "<html><body><noscript>You need to enable JavaScript to run this app.</noscript><div id='app'></div></body></html>"
        r = classify_render(html)
        assert r.mode == "spa"

    def test_bare_shell_short_body_is_spa(self):
        html = "<html><body><div id='main'>Loading…</div></body></html>"
        r = classify_render(html)
        assert r.mode == "spa"
        assert "bare shell" in r.reason

    def test_ssr_long_content_no_markers_is_ssr(self):
        """SG SSO case: substantive content in static markup, no framework markers."""
        html = f"<html><body><main>{_LONG}</main></body></html>"
        r = classify_render(html)
        assert r.mode == "ssr"
        assert r.spa_markers == []
        assert r.body_text_len >= 200

    def test_populated_app_root_without_ng_version_is_ssr(self):
        """A framework mount that IS server-populated (long text) is not a SPA shell."""
        html = f"<html><body><app-root>{_LONG}</app-root></body></html>"
        r = classify_render(html)
        assert r.mode == "ssr"

    def test_empty_html_is_spa(self):
        assert classify_render("").mode == "spa"
        assert classify_render("   ").mode == "spa"

    def test_min_text_chars_is_tunable(self):
        html = "<html><body><main>" + ("word " * 30) + "</main></body></html>"  # ~150 chars
        assert classify_render(html, min_text_chars=1000).mode == "spa"
        assert classify_render(html, min_text_chars=50).mode == "ssr"
