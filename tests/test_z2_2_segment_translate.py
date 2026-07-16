"""
Unit tests for Z2-2: Segment + Translate.

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
    CostLogEntry,
    FetchedDocument,
    TranslatedDocument,
    TranslationCostEntry,
)
from src.fetcher.translator import (
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
def my_config() -> EconomyConfig:
    return EconomyConfig.model_validate({
        "economy_name": "Malaysia",
        "iso_code": "MY",
        "script_type": "latin",
        "languages": ["ms", "en"],
        "translation_provider": "deepl",
        "portals": [{"name": "JPDP", "url": "https://www.pdp.gov.my", "type": "primary"}],
    })


def _make_doc(raw_text: str = "Sample legal text.", economy: str = "MY") -> FetchedDocument:
    cost = CostLogEntry(engine="pdfplumber", pages=1, cost_usd=0.0, processing_time_ms=10.0)
    return FetchedDocument(
        source_url="https://example.com/act.pdf",
        resolved_url="https://example.com/act.pdf",
        economy=economy,
        act_title="Akta Perlindungan Data Peribadi",
        discovery_tag="KNOWN",
        archive_url="https://web.archive.org/web/2024/https://example.com/act.pdf",
        doc_type="TEXT_PDF",
        extraction_method="pdfplumber",
        page_count=1,
        raw_text=raw_text,
        section_hierarchy=[],
        cost_log_entry=cost,
    )


# ── Law title normalisation ───────────────────────────────────────────────

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


# ── translate_text ────────────────────────────────────────────────────────

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

    # NOTE: Argos is now the primary translator, so these DeepL/Google-cascade tests
    # disable it (_argos_translate → None) to isolate the fallback path.
    def test_deepl_success(self):
        with (
            patch("src.fetcher.translator._argos_translate", return_value=None),
            patch("src.fetcher.translator._deepl_translate", return_value="translated text"),
        ):
            text, provider, cost = translate_text("ข้อความ", "th")
        assert text == "translated text"
        assert provider == "deepl"
        assert cost > 0.0

    def test_deepl_fail_google_fallback(self):
        with (
            patch("src.fetcher.translator._argos_translate", return_value=None),
            patch("src.fetcher.translator._deepl_translate", return_value=None),
            patch("src.fetcher.translator._google_translate", return_value="google result"),
        ):
            text, provider, cost = translate_text("ข้อความ", "th")
        assert text == "google result"
        assert provider == "google"
        assert cost == 0.0

    def test_both_providers_fail(self):
        with (
            patch("src.fetcher.translator._argos_translate", return_value=None),
            patch("src.fetcher.translator._deepl_translate", return_value=None),
            patch("src.fetcher.translator._google_translate", return_value=None),
        ):
            text, provider, cost = translate_text("ข้อความ", "th")
        assert text == "ข้อความ"  # returns original
        assert provider == "failed"
        assert cost == 0.0

    def test_force_google_skips_deepl(self):
        with (
            patch("src.fetcher.translator._argos_translate", return_value=None),
            patch("src.fetcher.translator._deepl_translate") as mock_deepl,
            patch("src.fetcher.translator._google_translate", return_value="google result"),
        ):
            text, provider, _ = translate_text("ข้อความ", "th", provider="google")
        mock_deepl.assert_not_called()
        assert provider == "google"


# ── Layer 1 — Keywords ────────────────────────────────────────────────────

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


# ── Layer 2 — Act title ───────────────────────────────────────────────────

class TestTranslateActTitle:
    def test_english_passthrough(self):
        title, provider, cost = translate_act_title("PDPA 2012", "en")
        assert title == "PDPA 2012"
        assert provider == "none"
        assert cost == 0.0

    def test_non_english_translated(self):
        with patch("src.fetcher.translator.translate_text", return_value=("Personal Data Act", "deepl", 0.0005)):
            title, provider, cost = translate_act_title("พระราชบัญญัติข้อมูล", "th")
        assert title == "Personal Data Act"
        assert provider == "deepl"          # actual provider is now surfaced
        assert cost == pytest.approx(0.0005)


# ── translate_document ────────────────────────────────────────────────────

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

    def test_non_english_translates_layer3(self, my_config):
        doc = _make_doc(raw_text="teks undang-undang Bahasa Melayu", economy="MY")
        with (
            patch("src.fetcher.translator.translate_text", return_value=("Malay legal text", "deepl", 0.01)),
            patch("src.fetcher.translator.translate_act_title", return_value=("Personal Data Act", "deepl", 0.001)),
            patch("src.fetcher.translator.translate_keywords", return_value=([], 0.0)),
        ):
            result = translate_document(doc, my_config)

        assert result.verbatim_original == "teks undang-undang Bahasa Melayu"
        assert result.translated_text == "Malay legal text"

    def test_verbatim_original_always_preserved(self, my_config):
        original = "data peribadi"
        doc = _make_doc(raw_text=original, economy="MY")
        with patch("src.fetcher.translator.translate_text", return_value=("personal data", "google", 0.0)):
            result = translate_document(doc, my_config)

        assert result.verbatim_original == original

    def test_long_text_chunked(self, my_config):
        long_text = "data " * 60_000  # 300K chars → many _CHUNK_SIZE (25K) chunks
        doc = _make_doc(raw_text=long_text, economy="MY")
        doc.act_title = "Akta Data"

        captured = {}

        def fake_many(texts, src):
            captured["chunks"] = len(texts)
            return ["chunk translated"] * len(texts)

        with (
            patch("src.fetcher.translator._argos_translate_many", side_effect=fake_many),
            patch("src.fetcher.translator.translate_act_title", return_value=("Data Act", "argos", 0.0)),
            patch("src.fetcher.translator.translate_keywords", return_value=([], 0.0)),
        ):
            result = translate_document(doc, my_config)

        # 300K chars split into >= 2 chunks, all translated in one parallel batch
        assert captured["chunks"] >= 2
        assert "chunk translated" in result.translated_text

    def test_chunk_falls_back_per_chunk_when_argos_fails(self, my_config):
        """When Argos returns None for some chunks, only those fall back to
        DeepL/Google — successful chunks keep the Argos result."""
        long_text = "data " * 60_000  # → several chunks
        doc = _make_doc(raw_text=long_text, economy="MY")

        def some_fail(texts, src):
            # first chunk ok, rest fail
            return ["argos ok"] + [None] * (len(texts) - 1)

        with (
            patch("src.fetcher.translator._argos_translate_many", side_effect=some_fail),
            patch("src.fetcher.translator.translate_text",
                  return_value=("fallback chunk", "deepl", 0.01)) as tt,
            patch("src.fetcher.translator.translate_act_title", return_value=("Data Act", "argos", 0.0)),
            patch("src.fetcher.translator.translate_keywords", return_value=([], 0.0)),
        ):
            result = translate_document(doc, my_config)

        assert "argos ok" in result.translated_text          # kept the good chunk
        assert "fallback chunk" in result.translated_text     # fell back for failures
        assert tt.call_count >= 1                             # fallback was used

    def test_translation_cost_entry_populated(self, my_config):
        doc = _make_doc(raw_text="teks Melayu", economy="MY")
        with patch("src.fetcher.translator.translate_text", return_value=("text", "deepl", 0.002)):
            result = translate_document(doc, my_config)

        assert isinstance(result.translation_cost_entry, TranslationCostEntry)
        assert result.translation_cost_entry.source_language == "ms"
        assert result.translation_cost_entry.chars_translated > 0

    def test_keywords_layer1_translated(self, my_config):
        doc = _make_doc(economy="MY")
        with (
            patch("src.fetcher.translator.translate_text", return_value=("x", "deepl", 0.0)),
        ):
            result = translate_document(doc, my_config, taxonomy_keywords=["privasi"])

        assert len(result.keywords_translated) == 1

    def test_google_provider_forced(self, my_config):
        my_config.translation_provider = "google"
        doc = _make_doc(raw_text="teks Melayu", economy="MY")
        with (
            patch("src.fetcher.translator._argos_translate", return_value=None),
            patch("src.fetcher.translator._deepl_translate") as mock_deepl,
            patch("src.fetcher.translator._google_translate", return_value="malay text"),
        ):
            result = translate_document(doc, my_config)

        mock_deepl.assert_not_called()


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


class TestGoogleTranslateAsyncFallback:
    """googletrans >= 4.0 made translate() async — _google_translate must await the
    returned coroutine instead of crashing with 'coroutine has no attribute text'."""

    def test_awaits_coroutine_result(self):
        import sys
        import types
        from unittest.mock import MagicMock

        async def _fake_translate(text, src, dest):
            r = MagicMock()
            r.text = "translated english"
            return r

        fake_translator = MagicMock()
        fake_translator.translate = _fake_translate
        fake_mod = types.ModuleType("googletrans")
        fake_mod.Translator = MagicMock(return_value=fake_translator)

        with patch.dict(sys.modules, {"googletrans": fake_mod}):
            from src.fetcher.translator import _google_translate
            out = _google_translate("teks bahasa melayu", "ms")
        assert out == "translated english"

    def test_sync_result_still_supported(self):
        import sys
        import types
        from unittest.mock import MagicMock

        r = MagicMock()
        r.text = "sync english"
        fake_translator = MagicMock()
        fake_translator.translate = MagicMock(return_value=r)  # 3.x sync
        fake_mod = types.ModuleType("googletrans")
        fake_mod.Translator = MagicMock(return_value=fake_translator)

        with patch.dict(sys.modules, {"googletrans": fake_mod}):
            from src.fetcher.translator import _google_translate
            out = _google_translate("teks", "ms")
        assert out == "sync english"


class TestArgosPrimaryTranslation:
    """Argos Translate is the primary (offline, free); DeepL is the fallback."""

    def test_lang_root_normalisation(self):
        from src.fetcher.translator import _lang_root
        assert _lang_root("ms") == "ms"
        assert _lang_root("ms-MY") == "ms"
        assert _lang_root("MS_my") == "ms"

    def test_translate_text_uses_argos_first(self):
        import src.fetcher.translator as tr
        with (
            patch.object(tr, "_argos_translate", return_value="english text") as argos,
            patch.object(tr, "_deepl_translate") as deepl,
        ):
            out, provider, cost = tr.translate_text("teks melayu", "ms")
        assert (out, provider, cost) == ("english text", "argos", 0.0)
        argos.assert_called_once()
        deepl.assert_not_called()  # argos succeeded → DeepL never tried

    def test_argos_failure_falls_back_to_deepl(self):
        import src.fetcher.translator as tr
        with (
            patch.object(tr, "_argos_translate", return_value=None),      # argos fails
            patch.object(tr, "_deepl_translate", return_value="via deepl") as deepl,
        ):
            out, provider, _ = tr.translate_text("teks melayu", "ms")
        assert out == "via deepl" and provider == "deepl"
        deepl.assert_called_once()

    def test_argos_then_deepl_then_google(self):
        import src.fetcher.translator as tr
        with (
            patch.object(tr, "_argos_translate", return_value=None),
            patch.object(tr, "_deepl_translate", return_value=None),
            patch.object(tr, "_google_translate", return_value="via google") as g,
        ):
            out, provider, _ = tr.translate_text("teks", "ms")
        assert out == "via google" and provider == "google"
        g.assert_called_once()

    def test_english_source_skips_all_providers(self):
        import src.fetcher.translator as tr
        with patch.object(tr, "_argos_translate") as argos:
            out, provider, _ = tr.translate_text("already english", "en")
        assert provider == "none"
        argos.assert_not_called()


class TestArgosPoolRobustness:
    """The persistent Argos worker must never freeze the pipeline: a hung worker
    times out and returns None (→ DeepL/Google fallback); dead workers respawn."""

    def _fake_hanging_pool(self, timeout_s):
        import subprocess, sys
        import src.fetcher.translator as tr
        tr._ARGOS_TIMEOUT = timeout_s
        pool = tr._ArgosPool.__new__(tr._ArgosPool)
        pool._size = 1
        # Worker that consumes stdin but NEVER replies (simulates a hang).
        pool._procs = [subprocess.Popen(
            [sys.executable, "-c", "import sys\nfor _ in sys.stdin: pass"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)]
        return pool

    def test_hung_worker_times_out_returns_none(self):
        import time
        pool = self._fake_hanging_pool(timeout_s=2)
        t = time.time()
        out = pool.translate_many(["some malay text"], "ms")
        elapsed = time.time() - t
        assert out == [None]            # failed → caller falls back
        assert elapsed < 6              # bounded by timeout, did NOT freeze
        pool.close()

    def test_dead_worker_respawned_next_batch(self):
        import src.fetcher.translator as tr
        pool = tr._ArgosPool(1)
        pool._procs[0].kill()
        pool._procs[0].wait()           # reaped, as between real batches
        assert pool._procs[0].poll() is not None
        pool.translate_many(["x"], "ms")  # triggers respawn
        assert pool._procs[0].poll() is None  # a fresh, live worker
        pool.close()
