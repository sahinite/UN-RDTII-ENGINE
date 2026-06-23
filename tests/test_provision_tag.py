"""
Tests for resolve_provision_tag() and infer_article_anchor(). [86ey13cyh Seam 2]
"""

from __future__ import annotations

from src.crawler.crawler import _normalise_url as normalise_url
from src.mapping.provision_tag import infer_article_anchor, resolve_provision_tag

BASE_URL = "https://sso.agc.gov.sg/Act/PDPA2012"
KNOWN = {normalise_url(BASE_URL + "#pr26-")}


class TestResolveProvisionTag:
    def test_known_doc_matching_anchor_returns_known(self):
        tag, unresolvable = resolve_provision_tag(BASE_URL, "#pr26-", "KNOWN", KNOWN)
        assert tag == "KNOWN"
        assert unresolvable is False

    def test_known_doc_non_matching_anchor_returns_new(self):
        tag, unresolvable = resolve_provision_tag(BASE_URL, "#pr99-", "KNOWN", KNOWN)
        assert tag == "NEW"
        assert unresolvable is False

    def test_new_doc_always_returns_new(self):
        tag, unresolvable = resolve_provision_tag(BASE_URL, "#pr26-", "NEW", KNOWN)
        assert tag == "NEW"
        assert unresolvable is False

    def test_unresolvable_anchor_falls_back_to_doc_level_and_flags(self):
        tag, unresolvable = resolve_provision_tag(BASE_URL, None, "KNOWN", KNOWN)
        assert tag == "KNOWN"  # falls back to doc-level
        assert unresolvable is True

    def test_empty_known_provisions_returns_new_for_known_doc(self):
        tag, unresolvable = resolve_provision_tag(BASE_URL, "#pr26-", "KNOWN", set())
        assert tag == "NEW"
        assert unresolvable is False


class TestInferArticleAnchor:
    def test_section_number(self):
        assert infer_article_anchor("Section 26") == "#pr26-"

    def test_art_with_subparagraph(self):
        assert infer_article_anchor("Art. 26(2)") == "#pr26-"

    def test_reg_with_subparagraph(self):
        assert infer_article_anchor("Regulation 4(1)(b)") == "#pr4-"

    def test_section_with_alpha_suffix(self):
        assert infer_article_anchor("Section 12A") == "#pr12a-"

    def test_no_number_returns_none(self):
        assert infer_article_anchor("Preamble") is None

    def test_bare_s_notation(self):
        assert infer_article_anchor("s. 31") == "#pr31-"
