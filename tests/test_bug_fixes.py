"""
Tests for the 10 bug fixes in PRD 86ey16a3t.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_fetched_doc(**overrides):
    from src.fetcher.models import FetchedDocument, CostLogEntry
    defaults = dict(
        source_url="https://example.com/law.pdf",
        resolved_url="https://example.com/law.pdf",
        economy="MY",
        act_title="Personal Data Protection Act 2010",
        discovery_tag="NEW",
        archive_url="",
        doc_type="TEXT_PDF",
        extraction_method="pdfplumber",
        page_count=10,
        raw_text="Section 1. This Act applies to personal data.",
        section_hierarchy=[],
        cost_log_entry=CostLogEntry(
            engine="pdfplumber", pages=10, cost_usd=0.0, processing_time_ms=100.0
        ),
    )
    defaults.update(overrides)
    return FetchedDocument(**defaults)


def _make_translated_doc(fetched=None, **overrides):
    from src.fetcher.models import TranslatedDocument, TranslationCostEntry
    if fetched is None:
        fetched = _make_fetched_doc()
    defaults = dict(
        fetched=fetched,
        source_language="ms",
        translated_text="Section 1. This Act applies to personal data.",
        act_title_translated="Personal Data Protection Act 2010",
        keywords_translated=[],
        verbatim_original="Seksyen 1. Akta ini terpakai kepada data peribadi.",
        translation_provider="deepl",
        translation_cost_entry=TranslationCostEntry(
            source_language="ms", provider="deepl",
            chars_translated=50, cost_usd=0.001
        ),
        be_year_conversions=[],
    )
    defaults.update(overrides)
    return TranslatedDocument(**defaults)


# ── Bug 1: TranslatedDocument proxy attributes ─────────────────────────────────

class TestTranslatedDocumentProxies:
    def test_discovery_tag_proxied(self):
        fetched = _make_fetched_doc(discovery_tag="NEW")
        tdoc = _make_translated_doc(fetched=fetched)
        assert tdoc.discovery_tag == "NEW"

    def test_source_url_proxied(self):
        fetched = _make_fetched_doc(source_url="https://test.gov.my/pdpa.pdf")
        tdoc = _make_translated_doc(fetched=fetched)
        assert tdoc.source_url == "https://test.gov.my/pdpa.pdf"

    def test_economy_proxied(self):
        fetched = _make_fetched_doc(economy="MY")
        tdoc = _make_translated_doc(fetched=fetched)
        assert tdoc.economy == "MY"

    def test_act_title_proxied(self):
        fetched = _make_fetched_doc(act_title="PDPA 2010")
        tdoc = _make_translated_doc(fetched=fetched)
        assert tdoc.act_title == "PDPA 2010"

    def test_raw_text_proxied(self):
        fetched = _make_fetched_doc(raw_text="original text in Bahasa")
        tdoc = _make_translated_doc(fetched=fetched)
        assert tdoc.raw_text == "original text in Bahasa"

    def test_law_number_ref_returns_none(self):
        tdoc = _make_translated_doc()
        assert tdoc.law_number_ref is None

    def test_last_amended_year_returns_none(self):
        tdoc = _make_translated_doc()
        assert tdoc.last_amended_year is None

    def test_citation_metadata_is_enriched_on_shared_fetched_document(self):
        fetched = _make_fetched_doc(
            act_title="Service Tax Act 2018",
            raw_text=(
                "LAWS OF MALAYSIA\nAct 807\nSERVICE TAX ACT 2018\n"
                "Latest amendment made by Act A1672 which came into operation "
                "on 1 January 2023"
            ),
        )
        assert fetched.law_number_ref == "Act 807"
        assert fetched.last_amended == "2023"
        assert fetched.last_amended_year == "2023"

        tdoc = _make_translated_doc(fetched=fetched)
        assert tdoc.law_number_ref == "Act 807"
        assert tdoc.last_amended_year == "2023"

    def test_source_pdf_path_returns_none(self):
        tdoc = _make_translated_doc()
        assert tdoc.source_pdf_path is None

    def test_getattr_works_like_mapper(self):
        fetched = _make_fetched_doc(
            economy="MY",
            act_title="PDPA 2010",
            source_url="https://agc.gov.my/pdpa.pdf",
            discovery_tag="NEW",
        )
        tdoc = _make_translated_doc(fetched=fetched)
        # Simulate what _build_doc_metadata does
        assert getattr(tdoc, "economy", "") == "MY"
        assert getattr(tdoc, "act_title", "") == "PDPA 2010"
        assert getattr(tdoc, "source_url", "") == "https://agc.gov.my/pdpa.pdf"
        assert getattr(tdoc, "discovery_tag", "KNOWN") == "NEW"
        assert getattr(tdoc, "law_number_ref", None) is None
        assert getattr(tdoc, "last_amended_year", None) is None


# ── Bug 3: Whitespace CER=0.0 ─────────────────────────────────────────────────

class TestEstimateCER:
    @pytest.fixture(autouse=True)
    def _import(self):
        from src.ocr.processor import _estimate_cer_from_text
        self._estimate = _estimate_cer_from_text

    @pytest.mark.parametrize("text", ["", "   \n", "\t  ", " \n \r\n "])
    def test_blank_returns_1(self, text):
        assert self._estimate(text) == 1.0

    def test_clean_english_text_low_cer(self):
        cer = self._estimate("Section 1. This Act applies to personal data protection.")
        assert cer == 0.0

    def test_garbage_heavy_text_high_cer(self):
        # Non-printable control chars
        garbage = "\x00\x01\x02\x03\x04" * 20
        cer = self._estimate(garbage)
        assert cer > 0.5


# ── Bug 2: PDPA gate check_pdpa_gate ──────────────────────────────────────────

class TestPDPAGate:
    def test_raises_when_no_p7_results(self):
        from src.mapping.mapper import check_pdpa_gate
        from src.mapping.exceptions import PDPAGateError

        with pytest.raises(PDPAGateError):
            check_pdpa_gate("SG", [])

    def test_raises_when_p7_confidence_below_threshold(self):
        from src.mapping.mapper import check_pdpa_gate
        from src.mapping.exceptions import PDPAGateError

        low_conf = MagicMock()
        low_conf.indicator_id = "P7-I1"
        low_conf.confidence = 0.70

        with pytest.raises(PDPAGateError):
            check_pdpa_gate("SG", [low_conf])

    def test_passes_when_p7_confidence_meets_threshold(self):
        from src.mapping.mapper import check_pdpa_gate

        good = MagicMock()
        good.indicator_id = "P7-I1"
        good.confidence = 0.90

        check_pdpa_gate("SG", [good])  # must not raise

    def test_non_sg_economy_skipped(self):
        from src.mapping.mapper import check_pdpa_gate

        # No P7 results, but MY → should not raise
        check_pdpa_gate("MY", [])


# ── Bug 8: Unguarded get_indicator() aborts batch ─────────────────────────────

class TestRetrieveBatchUnknownIndicator:
    def test_unknown_indicator_skipped_others_processed(self):
        from src.retrieval.rag import retrieve_batch

        fetched = _make_fetched_doc(
            raw_text="Section 1. Cross-border data transfer requires adequacy."
        )
        fake_chunk = MagicMock()
        fake_chunk.text = "Section 1. Cross-border data transfer requires adequacy."

        fake_retrieved = MagicMock()

        with (
            patch("src.retrieval.rag.chunk_document", return_value=[fake_chunk]),
            patch("src.retrieval.rag.build_index") as mock_idx,
            patch("src.retrieval.rag.build_bm25") as mock_bm25,
            patch("src.retrieval.rag.rrf_fusion", return_value=[]),
            patch("src.retrieval.rag.rerank", return_value=[fake_retrieved]),
            patch("src.retrieval.config.get_indicator") as mock_get_ind,
        ):
            # "UNKNOWN-X" raises KeyError; "P7-I1" returns a valid indicator
            valid_ind = MagicMock()
            valid_ind.legal_question = "What is the legal basis?"
            valid_ind.probe_keywords = ["personal data", "lawful basis"]

            def _side_effect(iid):
                if iid == "UNKNOWN-X":
                    raise KeyError(iid)
                return valid_ind

            mock_get_ind.side_effect = _side_effect
            mock_idx.return_value.dense_search.return_value = []
            mock_bm25.return_value.search.return_value = []

            results = retrieve_batch(["UNKNOWN-X", "P7-I1"], fetched)

        assert results["UNKNOWN-X"] == []
        assert results["P7-I1"] == [fake_retrieved]


# ── Bug 6 & 7: Ollama model tag + Groq fallback ───────────────────────────────

class TestOllamaModelTag:
    def test_priority7_model_is_granite3_8b(self):
        # Clear LLM_MODEL so we assert the tier DEFAULT, not a .env override.
        from src.mapping.providers.ollama_provider import OllamaProvider
        with patch.dict("os.environ", {"LLM_MODEL": ""}):
            assert OllamaProvider(7).model == "granite3-8b"

    def test_priority6_model_is_qwen25(self):
        from src.mapping.providers.ollama_provider import OllamaProvider
        with patch.dict("os.environ", {"LLM_MODEL": ""}):
            assert OllamaProvider(6).model == "qwen2.5:7b"

    def test_llm_model_env_overrides_default(self):
        """The active model comes from LLM_MODEL — not hardcoded in code."""
        from src.mapping.providers.ollama_provider import OllamaProvider
        with patch.dict("os.environ", {"LLM_MODEL": "deepseek-r1:8b"}):
            assert OllamaProvider(6).model == "deepseek-r1:8b"
            assert OllamaProvider(7).model == "deepseek-r1:8b"


class TestGroqRateLimitFallback:
    def test_rate_limit_attempts_fallback_before_raising(self):
        from src.mapping.providers.groq_provider import GroqProvider, GROQ_MODEL_FALLBACK
        from src.mapping.exceptions import ProviderRateLimitError

        provider = GroqProvider()

        rate_limit_exc = Exception("429 rate limit exceeded")

        call_log: list[str] = []

        def mock_create(**kwargs):
            call_log.append(kwargs["model"])
            raise rate_limit_exc

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = mock_create

        with (
            patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}),
            patch("src.mapping.providers.groq_provider.Groq", return_value=mock_client),
        ):
            with pytest.raises(ProviderRateLimitError):
                provider.complete("sys", "user", max_tokens=10)

        # Fallback model must have been attempted
        assert GROQ_MODEL_FALLBACK in call_log

    def test_rate_limit_on_fallback_raises_provider_rate_limit_error(self):
        from src.mapping.providers.groq_provider import GroqProvider
        from src.mapping.exceptions import ProviderRateLimitError

        provider = GroqProvider()
        rate_exc = Exception("429 Too Many Requests")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = rate_exc

        with (
            patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}),
            patch("src.mapping.providers.groq_provider.Groq", return_value=mock_client),
        ):
            with pytest.raises(ProviderRateLimitError):
                provider.complete("sys", "user", max_tokens=10)
