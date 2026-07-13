"""
Tests for resolve_provision_tag() and infer_article_anchor(). [86ey13cyh Seam 2]
"""

from __future__ import annotations

from src.crawler.crawler import _normalise_url as normalise_url
from src.crawler.seed_loader import match_known_act, normalise_title
from src.mapping.provision_tag import (
    infer_article_anchor,
    infer_section_token,
    resolve_provision_tag,
)

BASE_URL = "https://sso.agc.gov.sg/Act/PDPA2012"
KNOWN = {normalise_url(BASE_URL + "#pr26-")}
# Round 1 prose sections have no anchor URL — matched on (act title, section).
KNOWN_SECTIONS = {"personal data protection act": {"11", "25"}}


class TestResolveProvisionTag:
    def test_known_doc_matching_anchor_returns_known(self):
        tag, unresolvable = resolve_provision_tag(BASE_URL, "#pr26-", "KNOWN", KNOWN)
        assert tag == "KNOWN"
        assert unresolvable is False

    def test_known_doc_non_matching_anchor_returns_new(self):
        tag, unresolvable = resolve_provision_tag(BASE_URL, "#pr99-", "KNOWN", KNOWN)
        assert tag == "NEW"
        assert unresolvable is False

    def test_new_doc_with_round1_anchor_returns_known(self):
        # KNOWN is an identity judgement: a provision whose act-URL+anchor is in
        # Round 1 is KNOWN even when the DOCUMENT was discovered as a NEW act
        # (same act reachable via a different URL/portal).
        tag, unresolvable = resolve_provision_tag(BASE_URL, "#pr26-", "NEW", KNOWN)
        assert tag == "KNOWN"
        assert unresolvable is False

    def test_new_doc_unknown_anchor_returns_new(self):
        tag, unresolvable = resolve_provision_tag(BASE_URL, "#pr99-", "NEW", KNOWN)
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

    def test_pdf_view_suffix_does_not_break_known_match(self):
        """The ?ViewType=Pdf render artefact must not prevent a KNOWN match."""
        tag, unresolvable = resolve_provision_tag(
            BASE_URL + "?ViewType=Pdf", "#pr26-", "KNOWN", KNOWN
        )
        assert tag == "KNOWN"
        assert unresolvable is False


class TestResolveProvisionTagBySection:
    """KNOWN via Round 1 prose section citations (no anchor URL)."""

    def test_prose_section_match_returns_known(self):
        # Round 1 cited PDPA "Section 11(3)" in prose → KNOWN even with no anchor.
        tag, unresolvable = resolve_provision_tag(
            BASE_URL + "?ViewType=Pdf", None, "KNOWN", set(),
            law_name="Personal Data Protection Act 2012",
            article="Section 11(3)",
            known_sections=KNOWN_SECTIONS,
        )
        assert tag == "KNOWN"
        assert unresolvable is False

    def test_section_not_in_round1_returns_new(self):
        # PDPA s.22A was not a Round 1 known provision → a valid NEW discovery.
        tag, unresolvable = resolve_provision_tag(
            BASE_URL + "?ViewType=Pdf", infer_article_anchor("Section 22A(1)"),
            "KNOWN", set(),
            law_name="Personal Data Protection Act 2012",
            article="Section 22A(1)",
            known_sections=KNOWN_SECTIONS,
        )
        assert tag == "NEW"
        assert unresolvable is False

    def test_section_match_is_act_scoped(self):
        # Section 11 is known for the PDPA, not for a different act.
        tag, _ = resolve_provision_tag(
            "https://sso.agc.gov.sg/Act/CoA1967", None, "KNOWN", set(),
            law_name="Companies Act 1967",
            article="Section 11",
            known_sections=KNOWN_SECTIONS,
        )
        assert tag == "NEW"

    def test_new_doc_section_match_returns_known(self):
        # Act+section identity wins regardless of the document's discovery tag:
        # a NEW-discovered document whose (act, section) is in Round 1 is KNOWN.
        tag, _ = resolve_provision_tag(
            BASE_URL, None, "NEW", set(),
            law_name="Personal Data Protection Act 2012",
            article="Section 11(3)",
            known_sections=KNOWN_SECTIONS,
        )
        assert tag == "KNOWN"

    def test_new_doc_untestable_falls_back_to_new(self):
        # No anchor and no section token → cannot test identity → fall back to the
        # document's NEW tag, flagged unresolvable.
        tag, unresolvable = resolve_provision_tag(
            BASE_URL, None, "NEW", set(),
            law_name="Personal Data Protection Act 2012",
            article="",
            known_sections=KNOWN_SECTIONS,
        )
        assert tag == "NEW"
        assert unresolvable is True


class TestFuzzyActTitleMatch:
    """KNOWN act+section match tolerates title-form differences (acronym, comma,
    dropped words) so a genuinely-known act is not mis-tagged NEW."""

    def _tag(self, law_name):
        tag, _ = resolve_provision_tag(
            BASE_URL, None, "KNOWN", set(),
            law_name=law_name, article="Section 11(3)",
            known_sections=KNOWN_SECTIONS,
        )
        return tag

    def test_acronym_matches(self):
        assert self._tag("PDPA") == "KNOWN"

    def test_comma_before_year_matches(self):
        assert self._tag("Personal Data Protection Act, 2012") == "KNOWN"

    def test_dropped_leading_word_matches(self):
        assert self._tag("Data Protection Act 2012") == "KNOWN"

    def test_unrelated_guidance_heading_stays_new(self):
        # A regulator guidance page heading is not the statute → must NOT match.
        assert self._tag("DATA-PROTECTION-OBLIGATIONS") == "NEW"

    def test_single_generic_token_does_not_overmatch(self):
        # "Companies Act" is in Round 1 sections only via KNOWN_SECTIONS? no —
        # ensure a lone generic token can't match a longer unrelated act.
        from src.mapping.provision_tag import resolve_provision_tag as r
        ks = {"my health records act": {"77"}}
        tag, _ = r(BASE_URL, None, "KNOWN", set(),
                   law_name="Health Act", article="Section 77", known_sections=ks)
        assert tag == "NEW"


_MATCH_KEYS = {
    normalise_title(t) for t in [
        "Personal Data Protection Act 2012", "Companies Act 1967",
        "My Health Records Act 2012", "Privacy Act 1988",
    ]
}


class TestMatchKnownAct:
    def test_exact(self):
        assert match_known_act("Companies Act 1967", _MATCH_KEYS) == "companies act"

    def test_acronym_without_act(self):
        assert match_known_act("MHR", _MATCH_KEYS) == "my health records act"

    def test_no_match_returns_none(self):
        assert match_known_act("Telecommunications Act 1997", _MATCH_KEYS) is None

    def test_empty_returns_none(self):
        assert match_known_act("", _MATCH_KEYS) is None

    def test_act_number_matches_full_title(self):
        # An LLM that outputs only the act number ("Act 709") still resolves to
        # Round 1's full title via the shared "(Act 709)" designation.
        keys = {normalise_title("Personal Data Protection Act (Act 709) 2010"),
                normalise_title("Income Tax Act (Act 53) 1967")}
        assert match_known_act("Act 709", keys) == "personal data protection act (act 709)"
        assert match_known_act("Act 53", keys) == "income tax act (act 53)"

    def test_act_number_no_false_match(self):
        keys = {normalise_title("Personal Data Protection Act (Act 709) 2010")}
        assert match_known_act("Act 999", keys) is None


class TestSplitActTitles:
    def test_splits_on_semicolon_and_blank_line(self):
        from src.crawler.seed_loader import split_act_titles
        got = split_act_titles("Companies Act 2016;\n\nPrivacy Act 1988")
        assert got == ["Companies Act 2016", "Privacy Act 1988"]

    def test_single_newline_wrap_is_joined_not_fragmented(self):
        # Regression: a wrapped title must NOT shred into "personal data protection"
        # + "(amendment) bill ...".
        from src.crawler.seed_loader import split_act_titles
        cell = "Personal Data Protection Act (Act 709) 2010;\n\nPersonal Data Protection\n(Amendment) Bill (Act A1727) 2024"
        got = split_act_titles(cell)
        assert got == [
            "Personal Data Protection Act (Act 709) 2010",
            "Personal Data Protection (Amendment) Bill (Act A1727) 2024",
        ]

    def test_empty_cell(self):
        from src.crawler.seed_loader import split_act_titles
        assert split_act_titles("") == []


class TestInferSectionToken:
    def test_drops_subparagraph(self):
        assert infer_section_token("Section 11(3)") == "11"

    def test_alpha_suffix_lowercased(self):
        assert infer_section_token("s. 22A(1)") == "22a"

    def test_no_number_returns_none(self):
        assert infer_section_token("Preamble") is None


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
