"""
Unit tests for Z2-2: Segment + Translate. [Z2-2 ST7]

Coverage target: ≥70% of src/fetcher/translator.py and the new
article-reference code in src/fetcher/segmenter.py.
Zero real API calls — all provider calls are mocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from src.config.economy_config import EconomyConfig
from src.fetcher.models import (
    ArticleReference,
    CostLogEntry,
    FetchedDocument,
    TranslatedDocument,
    TranslationCostEntry,
)
from src.fetcher.segmenter import extract_article_references
from src.fetcher.translator import (
    convert_be_years,
    normalise_law_reference,
    translate_act_title,
    translate_document,
    translate_keywords,
    translate_text,
)


# ── Shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def sg_config() -> EconomyConfig:
    return EconomyConfig.model_validate({
        "economy_name": "Singapore",
        "script_type": "latin",
        "languages": ["en"],
        "portals": [{"name": "SSO", "url": "https://sso.agc.gov.sg", "type": "primary"}],
    })


@pytest.fixture
def th_config() -> EconomyConfig:
    return EconomyConfig.model_validate({
        "economy_name": "Thailand",
        "script_type": "asian",
        "languages": ["th", "en"],
        "be_year_conversion": True,
        "translation_provider": "deepl",
        "portals": [{"name": "Ratchakitcha", "url": "https://ratchakitcha.soc.go.th", "type": "primary"}],
    })


@pytest.fixture
def lao_config() -> EconomyConfig:
    return EconomyConfig.model_validate({
        "economy_name": "LaoPDR",
        "script_type": "asian",
        "languages": ["lo", "en"],
        "translation_provider": "google",
        "portals": [{"name": "LaoPDR Gov", "url": "https://www.laogov.la", "type": "primary"}],
    })


def _make_doc(raw_text: str = "Sample legal text.", economy: str = "TH") -> FetchedDocument:
    cost = CostLogEntry(engine="pdfplumber", pages=1, cost_usd=0.0, processing_time_ms=10.0)
    return FetchedDocument(
        source_url="https://example.com/act.pdf",
        resolved_url="https://example.com/act.pdf",
        economy=economy,
        act_title="พระราชบัญญัติ ข้อมูลส่วนบุคคล",
        discovery_tag="KNOWN",
        archive_url="https://web.archive.org/web/2024/https://example.com/act.pdf",
        doc_type="TEXT_PDF",
        extraction_method="pdfplumber",
        page_count=1,
        raw_text=raw_text,
        section_hierarchy=[],
        cost_log_entry=cost,
    )


# ── ST5: Buddhist Era conversion ───────────────────────────────────────────────

class TestConvertBeYears:
    def test_converts_single_be_year(self):
        text, conversions = convert_be_years("กฎหมาย 2567")
        assert "2024" in text
        assert conversions == [("2567", 2024)]

    def test_converts_multiple_years(self):
        text, conversions = convert_be_years("2566 and 2567")
        assert "2023" in text
        assert "2024" in text
        assert len(conversions) == 2

    def test_no_be_year_unchanged(self):
        text, conversions = convert_be_years("No year here")
        assert text == "No year here"
        assert conversions == []

    def test_gregorian_year_not_converted(self):
        # CE 2024 is not in the 2400–2599 range → unchanged
        text, conversions = convert_be_years("Year 1990")
        assert text == "Year 1990"
        assert conversions == []


# ── ST5: Law title normalisation ───────────────────────────────────────────────

class TestNormaliseLawReference:
    def test_strips_be_suffix(self):
        assert normalise_law_reference("Data Act B.E.") == "Data Act"

    def test_strips_be_dot_suffix(self):
        assert normalise_law_reference("Trade Act B.E") == "Trade Act"

    def test_strips_thai_be_suffix(self):
        result = normalise_law_reference("พระราชบัญญัติ พ.ศ.")
        assert "พ.ศ." not in result

    def test_normalises_no_spacing(self):
        result = normalise_law_reference("Act No.123/2024")
        assert "No. 123" in result

    def test_collapses_whitespace(self):
        result = normalise_law_reference("Act   No.  5")
        assert "  " not in result

    def test_english_title_unchanged(self):
        result = normalise_law_reference("Personal Data Protection Act 2012")
        assert result == "Personal Data Protection Act 2012"


# ── ST3: translate_text ────────────────────────────────────────────────────────

class TestTranslateText:
    def test_english_passthrough(self):
        text, provider, cost = translate_text("hello", "en")
        assert text == "hello"
        assert provider == "none"
        assert cost == 0.0

    def test_empty_text_passthrough(self):
        text, provider, cost = translate_text("", "th")
        assert text == ""
        assert provider == "none"
        assert cost == 0.0

    def test_deepl_success(self):
        with patch("src.fetcher.translator._deepl_translate", return_value="translated text"):
            text, provider, cost = translate_text("ข้อความ", "th")
        assert text == "translated text"
        assert provider == "deepl"
        assert cost > 0.0

    def test_deepl_fail_google_fallback(self):
        with (
            patch("src.fetcher.translator._deepl_translate", return_value=None),
            patch("src.fetcher.translator._google_translate", return_value="google result"),
        ):
            text, provider, cost = translate_text("ข้อความ", "th")
        assert text == "google result"
        assert provider == "google"
        assert cost == 0.0

    def test_both_providers_fail(self):
        with (
            patch("src.fetcher.translator._deepl_translate", return_value=None),
            patch("src.fetcher.translator._google_translate", return_value=None),
        ):
            text, provider, cost = translate_text("ข้อความ", "th")
        assert text == "ข้อความ"  # returns original
        assert provider == "failed"
        assert cost == 0.0

    def test_force_google_skips_deepl(self):
        with (
            patch("src.fetcher.translator._deepl_translate") as mock_deepl,
            patch("src.fetcher.translator._google_translate", return_value="google result"),
        ):
            text, provider, _ = translate_text("ข้อความ", "th", provider="google")
        mock_deepl.assert_not_called()
        assert provider == "google"


# ── ST3: Layer 1 — Keywords ────────────────────────────────────────────────────

class TestTranslateKeywords:
    def test_english_passthrough(self):
        kws, cost = translate_keywords(["privacy", "data"], "en")
        assert kws == ["privacy", "data"]
        assert cost == 0.0

    def test_non_english_translates_each(self):
        with patch("src.fetcher.translator.translate_text", return_value=("en_kw", "deepl", 0.001)):
            kws, cost = translate_keywords(["ความเป็นส่วนตัว", "ข้อมูล"], "th")
        assert kws == ["en_kw", "en_kw"]
        assert cost == pytest.approx(0.002)

    def test_empty_list(self):
        kws, cost = translate_keywords([], "th")
        assert kws == []
        assert cost == 0.0


# ── ST3: Layer 2 — Act title ───────────────────────────────────────────────────

class TestTranslateActTitle:
    def test_english_passthrough(self):
        title, cost = translate_act_title("PDPA 2012", "en")
        assert title == "PDPA 2012"
        assert cost == 0.0

    def test_non_english_translated(self):
        with patch("src.fetcher.translator.translate_text", return_value=("Personal Data Act", "deepl", 0.0005)):
            title, cost = translate_act_title("พระราชบัญญัติข้อมูล", "th")
        assert title == "Personal Data Act"
        assert cost == pytest.approx(0.0005)


# ── ST4: translate_document ────────────────────────────────────────────────────

class TestTranslateDocument:
    def test_english_economy_no_translation(self, sg_config):
        doc = _make_doc(raw_text="English legal text.", economy="SG")
        doc.act_title = "PDPA 2012"
        result = translate_document(doc, sg_config)

        assert isinstance(result, TranslatedDocument)
        assert result.translated_text == "English legal text."
        assert result.verbatim_original == "English legal text."
        assert result.translation_provider == "none"
        assert result.translation_cost_entry.cost_usd == 0.0
        assert result.be_year_conversions == []

    def test_non_english_translates_layer3(self, th_config):
        doc = _make_doc(raw_text="ข้อความภาษาไทย", economy="TH")
        with (
            patch("src.fetcher.translator.translate_text", return_value=("Thai legal text", "deepl", 0.01)),
            patch("src.fetcher.translator.translate_act_title", return_value=("Personal Data Act", 0.001)),
            patch("src.fetcher.translator.translate_keywords", return_value=([], 0.0)),
        ):
            result = translate_document(doc, th_config)

        assert result.verbatim_original == "ข้อความภาษาไทย"
        assert result.translated_text == "Thai legal text"

    def test_be_year_conversion_applied(self, th_config):
        doc = _make_doc(raw_text="กฎหมาย พ.ศ. 2567 ฉบับที่ 1", economy="TH")
        with patch("src.fetcher.translator.translate_text", return_value=("Law 2024 No. 1", "deepl", 0.0)):
            result = translate_document(doc, th_config)

        assert len(result.be_year_conversions) > 0
        assert result.be_year_conversions[0] == ("2567", 2024)

    def test_verbatim_original_always_preserved(self, th_config):
        original = "ข้อมูลส่วนบุคคล"
        doc = _make_doc(raw_text=original, economy="TH")
        with patch("src.fetcher.translator.translate_text", return_value=("personal data", "google", 0.0)):
            result = translate_document(doc, th_config)

        assert result.verbatim_original == original

    def test_long_text_chunked(self, lao_config):
        long_text = "ຂໍ້ " * 60_000  # > 100_000 chars
        doc = _make_doc(raw_text=long_text, economy="LA")
        doc.act_title = "ກົດໝາຍ"

        call_count = 0

        def fake_translate(text, source_lang, provider=None):
            nonlocal call_count
            call_count += 1
            return ("chunk translated", "google", 0.0)

        with patch("src.fetcher.translator.translate_text", side_effect=fake_translate):
            result = translate_document(doc, lao_config)

        # Should have been called at least twice (text > 100_000 chars)
        assert call_count >= 2
        assert "chunk translated" in result.translated_text

    def test_translation_cost_entry_populated(self, th_config):
        doc = _make_doc(raw_text="ข้อความ", economy="TH")
        with patch("src.fetcher.translator.translate_text", return_value=("text", "deepl", 0.002)):
            result = translate_document(doc, th_config)

        assert isinstance(result.translation_cost_entry, TranslationCostEntry)
        assert result.translation_cost_entry.source_language == "th"
        assert result.translation_cost_entry.chars_translated > 0

    def test_keywords_layer1_translated(self, th_config):
        doc = _make_doc(economy="TH")
        with (
            patch("src.fetcher.translator.translate_text", return_value=("x", "deepl", 0.0)),
        ):
            result = translate_document(doc, th_config, taxonomy_keywords=["ความเป็นส่วนตัว"])

        assert len(result.keywords_translated) == 1

    def test_google_provider_forced(self, lao_config):
        doc = _make_doc(raw_text="ຂໍ້ຄວາມ", economy="LA")
        with (
            patch("src.fetcher.translator._deepl_translate") as mock_deepl,
            patch("src.fetcher.translator._google_translate", return_value="lao text"),
        ):
            result = translate_document(doc, lao_config)

        mock_deepl.assert_not_called()


# ── ST2: Article reference mapping ────────────────────────────────────────────

class TestExtractArticleReferences:
    def _hier(self, entries: list[dict]) -> list[dict]:
        return entries

    def test_extracts_numbered_sections(self):
        hierarchy = [
            {"level": 2, "title": "1. Short title", "text": "", "anchor": ""},
            {"level": 2, "title": "2. Interpretation", "text": "", "anchor": ""},
        ]
        refs = extract_article_references(hierarchy, "PDPA 2012")
        assert len(refs) == 2
        assert refs[0].article_number == "1"
        assert refs[1].article_number == "2"

    def test_tracks_part_context(self):
        hierarchy = [
            {"level": 1, "title": "PART I — PRELIMINARY", "text": "", "anchor": ""},
            {"level": 2, "title": "1. Short title", "text": "", "anchor": ""},
            {"level": 1, "title": "PART II — DATA PROTECTION", "text": "", "anchor": ""},
            {"level": 2, "title": "4. Obligations", "text": "", "anchor": ""},
        ]
        refs = extract_article_references(hierarchy, "PDPA 2012")
        assert refs[0].part == "PART I"
        assert refs[1].part == "PART II"

    def test_extracts_section_keyword_style(self):
        hierarchy = [
            {"level": 2, "title": "Section 12 — Data breach notification", "text": "", "anchor": ""},
        ]
        refs = extract_article_references(hierarchy, "PDPA 2012")
        assert len(refs) == 1
        assert refs[0].article_number == "12"

    def test_disambiguates_duplicate_article_numbers(self):
        hierarchy = [
            {"level": 1, "title": "PART I", "text": "", "anchor": ""},
            {"level": 2, "title": "1. Scope", "text": "", "anchor": ""},
            {"level": 1, "title": "PART II", "text": "", "anchor": ""},
            {"level": 2, "title": "1. Application", "text": "", "anchor": ""},
        ]
        refs = extract_article_references(hierarchy, "Act")
        numbers = [r.article_number for r in refs]
        # Second "1" should be disambiguated
        assert numbers[0] != numbers[1]

    def test_empty_hierarchy_returns_empty(self):
        refs = extract_article_references([], "Empty Act")
        assert refs == []

    def test_act_title_propagated(self):
        hierarchy = [{"level": 2, "title": "3. Definitions", "text": "", "anchor": ""}]
        refs = extract_article_references(hierarchy, "My Act")
        assert refs[0].act_title == "My Act"

    def test_anchor_preserved(self):
        hierarchy = [{"level": 2, "title": "5. Penalties", "text": "", "anchor": "section-5"}]
        refs = extract_article_references(hierarchy, "My Act")
        assert refs[0].text_anchor == "section-5"

    def test_heading_preserved(self):
        hierarchy = [{"level": 2, "title": "7A. Transitional provisions", "text": "", "anchor": ""}]
        refs = extract_article_references(hierarchy, "My Act")
        assert refs[0].heading == "7A. Transitional provisions"
        assert refs[0].article_number == "7A"

    def test_chapter_pattern_recognised(self):
        hierarchy = [
            {"level": 1, "title": "CHAPTER 3 — OBLIGATIONS", "text": "", "anchor": ""},
            {"level": 2, "title": "10. Duty of care", "text": "", "anchor": ""},
        ]
        refs = extract_article_references(hierarchy, "Act")
        assert refs[0].part == "CHAPTER 3"


# ── Dataclass integrity ────────────────────────────────────────────────────────

class TestDataclassIntegrity:
    def test_translation_cost_entry_fields(self):
        entry = TranslationCostEntry(
            source_language="th",
            provider="deepl",
            chars_translated=1000,
            cost_usd=0.02,
        )
        assert entry.source_language == "th"
        assert entry.provider == "deepl"
        assert entry.chars_translated == 1000
        assert entry.cost_usd == pytest.approx(0.02)

    def test_article_reference_fields(self):
        ref = ArticleReference(
            act_title="PDPA",
            part="PART I",
            article_number="12",
            heading="12. Data breach",
            text_anchor="s12",
        )
        assert ref.act_title == "PDPA"
        assert ref.part == "PART I"
        assert ref.article_number == "12"
