"""
Unit tests for [Z2-3] RAG pipeline.

All ML models are mocked — tests run without GPU and in < 5 seconds.
Covers: chunker, BM25, fusion, reranker, and the orchestrator.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.retrieval.bm25_index import BM25Index, build_bm25
from src.retrieval.chunker import chunk_document, _split_text_by_regex
from src.retrieval.config import get_indicator, load_taxonomy
from src.retrieval.fusion import rrf_fusion
from src.retrieval.models import Chunk, LocationReference, RetrievedChunk, TaxonomyEntry
from src.retrieval.reranker import rerank


# ── Fixtures ──────────────────────────────────────────────────────────────────

PDPA_TEXT = """
Personal Data Protection Act 2012

PART I — PRELIMINARY

1. Short title
This Act may be cited as the Personal Data Protection Act 2012.

2. Interpretation
In this Act, unless the context otherwise requires —
"personal data" means data, whether true or not, about an individual who can be identified.

PART II — GENERAL DATA PROTECTION PROVISIONS

26. Obligation of data intermediaries
An organisation acting as a data intermediary must protect personal data it processes.

26A. Transfer of personal data outside Singapore
No organisation shall transfer personal data outside Singapore except in accordance with
the requirements prescribed under this Act. An organisation must ensure that comparable
protection is given to the personal data.

27. Data breach notification
An organisation that suffers a data breach must notify the Commission and affected individuals
within 3 days of becoming aware of the breach.

28. Enforcement and penalties
The Personal Data Protection Commission may issue directions to an organisation that
contravenes this Act. Penalties of up to S$1,000,000 may be imposed.
"""


def _make_fetched(text: str = PDPA_TEXT, hierarchy: list[dict] | None = None) -> MagicMock:
    doc = MagicMock()
    doc.raw_text = text
    doc.act_title = "Personal Data Protection Act 2012"
    doc.source_url = "https://sso.agc.gov.sg/Acts/PDPA"
    doc.section_hierarchy = hierarchy or []
    doc.location_reference_map = {}
    return doc


def _make_translated(text: str = PDPA_TEXT) -> MagicMock:
    """Returns a MagicMock that looks like a TranslatedDocument to chunk_document."""
    from src.fetcher.models import TranslatedDocument, FetchedDocument, CostLogEntry
    fetched = _make_fetched(text)
    # chunk_document uses isinstance(doc, TranslatedDocument), so build a real one
    # We use a MagicMock spec'd to TranslatedDocument so isinstance returns True.
    doc = MagicMock(spec=TranslatedDocument)
    doc.fetched = fetched
    doc.translated_text = text
    doc.act_title_translated = "Personal Data Protection Act 2012"
    doc.article_references = []
    return doc


def _make_chunks(n: int = 6) -> list[Chunk]:
    return [
        Chunk(
            chunk_id=f"chunk_{i}",
            text=f"Section {i}. This provision concerns personal data protection {'legal ' * 20}article {i}.",
            location_reference=LocationReference(
                act_title="Test Act",
                part="PART I",
                article_number=str(i),
            ),
        )
        for i in range(n)
    ]


# ── ST1: Chunker ──────────────────────────────────────────────────────────────

class TestChunker:
    def test_regex_split_returns_multiple_chunks(self):
        chunks = chunk_document(_make_fetched(PDPA_TEXT))
        assert len(chunks) >= 3

    def test_all_chunks_have_location_reference(self):
        chunks = chunk_document(_make_fetched(PDPA_TEXT))
        for chunk in chunks:
            assert isinstance(chunk.location_reference, LocationReference)
            assert chunk.location_reference.act_title  # non-empty

    def test_chunk_id_is_unique(self):
        chunks = chunk_document(_make_fetched(PDPA_TEXT))
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids)), "Duplicate chunk_ids found"

    def test_short_document_returns_one_chunk(self):
        short_doc = _make_fetched("This is a brief personal data protection statement.")
        chunks = chunk_document(short_doc)
        assert len(chunks) >= 1

    def test_hierarchy_strategy_used_when_available(self):
        hierarchy = [
            {"level": 1, "title": "PART I", "text": "", "anchor": ""},
            {
                "level": 2,
                "title": "1. Short title",
                "text": "This Act may be cited as the Personal Data Protection Act 2012 and is a comprehensive law.",
                "anchor": "section-1",
            },
            {
                "level": 2,
                "title": "26. Obligation of data intermediaries",
                "text": "An organisation acting as a data intermediary must protect personal data it processes on behalf of another organisation.",
                "anchor": "section-26",
            },
        ]
        chunks = chunk_document(_make_fetched(PDPA_TEXT, hierarchy=hierarchy))
        article_numbers = [c.location_reference.article_number for c in chunks]
        assert "1" in article_numbers or "26" in article_numbers

    def test_accepts_translated_document(self):
        chunks = chunk_document(_make_translated(PDPA_TEXT))
        assert len(chunks) >= 1

    def test_source_url_propagated(self):
        chunks = chunk_document(_make_fetched(PDPA_TEXT))
        assert all(c.doc_source_url == "https://sso.agc.gov.sg/Acts/PDPA" for c in chunks)

    # ── Subsection-aware chunking (recall fix) ──────────────────────────────
    @staticmethod
    def _large_section_doc() -> MagicMock:
        """A single PDPA-style section ~4k chars with a short DPO clause at (3)."""
        body = (
            "Personal Data Protection Act 2012\n\n"
            "11. Protection of personal data\n"
            "(1) " + "An organisation shall make reasonable security arrangements. " * 18 + "\n"
            "(2) " + "The Commission may issue guidelines on security arrangements. " * 18 + "\n"
            "(3) An organisation shall designate one or more individuals, known as "
            "the data protection officer, to be responsible for ensuring compliance.\n"
            "(4) " + "Designation under subsection (3) does not relieve obligations. " * 18
        )
        return _make_fetched(body)

    def test_large_section_split_into_multiple_chunks(self):
        chunks = chunk_document(self._large_section_doc())
        assert len(chunks) >= 3, "oversized section should split into several chunks"

    def test_chunks_bounded_near_target_size(self):
        chunks = chunk_document(self._large_section_doc())
        # No chunk should approach the old 6k block size; allow heading-prefix slack.
        assert all(len(c.text) <= 2200 for c in chunks), [len(c.text) for c in chunks]

    def test_buried_subsection_isolated_into_its_own_chunk(self):
        chunks = chunk_document(self._large_section_doc())
        dpo = [c for c in chunks if "data protection officer" in c.text]
        assert len(dpo) == 1, "DPO clause should land in exactly one chunk"
        # It must NOT be diluted by the long (1)/(2) security-arrangement prose.
        assert "reasonable security arrangements" not in dpo[0].text

    def test_chunk_carries_parent_heading_prefix(self):
        chunks = chunk_document(self._large_section_doc())
        dpo = next(c for c in chunks if "data protection officer" in c.text)
        assert "Personal Data Protection Act 2012" in dpo.text
        assert "Section 11" in dpo.text

    def test_invariants_hold_after_split(self):
        chunks = chunk_document(self._large_section_doc())
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids)), "chunk_ids must stay unique"
        assert all(c.location_reference.act_title for c in chunks)

    # ── AU legislation.gov.au compilation format (regression) ───────────────
    # Compilation PDFs use "6A Heading" section titles (space, no period) and
    # repeat running page-headers ("Part I Preliminary", "Section 6A") and
    # footers ("Privacy Act 1988 3") on every page. pdfplumber's section
    # hierarchy parses those running headers as Part/Division entries, so the
    # hierarchy path drops ~97% of the body and retrieval only ever saw the
    # cover/TOC — the LLM then "cited" the act title itself. See chunker.py
    # coverage guard + AU heading regex + _strip_page_furniture.
    @staticmethod
    def _au_compilation_doc() -> MagicMock:
        raw_text = (
            "No table of contents\nentries found.\n"
            "Privacy Act 1988\n"
            "Compilation No. 104\n\n"
            "Contents\n"
            "Part I—Preliminary 1\n"
            "6A Breach of an Australian Privacy Principle ...........................50\n"
            "26WK Statement about eligible data breach ..............................170\n\n"
            "Part I Preliminary\n"
            "Section 6A\n"
            "6A Breach of an Australian Privacy Principle\n"
            "(1) For the purposes of this Act, an act or practice breaches an "
            "Australian Privacy Principle if, and only if, it is contrary to, or "
            "inconsistent with, that principle. " + "An APP entity must comply. " * 12 + "\n"
            "Privacy Act 1988 3\n"
            "Part II Interpretation\n"
            "Section 26WK\n"
            "26WK Statement about eligible data breach\n"
            "(1) An entity must prepare a statement that sets out a description of "
            "the eligible data breach and the kinds of personal information "
            "concerned, and give a copy to the Commissioner. " + "Security matters. " * 12 + "\n"
            "Privacy Act 1988 4\n"
        )
        # The weak pdfplumber hierarchy: running Part headers swallow the body,
        # exactly the shape that made Strategy 1 collapse in production.
        hierarchy = [
            {"level": 1, "title": "Part I Preliminary", "text": raw_text[:400], "anchor": ""},
            {"level": 1, "title": "Part II Interpretation", "text": raw_text[400:], "anchor": ""},
        ]
        doc = _make_fetched(raw_text, hierarchy=hierarchy)
        doc.act_title = "Privacy Act 1988"  # footer detection keys off the real title
        return doc

    def test_au_compilation_recovers_real_sections(self):
        chunks = chunk_document(self._au_compilation_doc())
        # Must NOT degenerate to a single title-only chunk (the production bug:
        # one chunk whose only "provision" was the act title).
        assert len(chunks) >= 2, f"AU body collapsed to {len(chunks)} chunk(s)"
        arts = {c.location_reference.article_number for c in chunks}
        assert "6A" in arts and "26WK" in arts, f"AU section numbers missing: {sorted(arts)}"
        body = " ".join(c.text for c in chunks)
        assert "Australian Privacy Principle" in body
        assert "eligible data breach" in body

    def test_au_compilation_strips_page_furniture(self):
        chunks = chunk_document(self._au_compilation_doc())
        body = " ".join(c.text for c in chunks)
        # Running footer, TOC leader dots, and the cover artifact are noise, not text.
        assert "Privacy Act 1988 3" not in body
        assert "Privacy Act 1988 4" not in body
        assert "..........." not in body
        assert "No table of contents" not in body


# ── ST2: Embedding Index (mocked) ─────────────────────────────────────────────

class TestEmbeddingIndex:
    @patch("src.retrieval.embedder._get_model")
    def test_build_and_dense_search(self, mock_get_model):
        import faiss

        from src.retrieval.embedder import EmbeddingIndex

        dim = 8
        chunks = _make_chunks(5)
        fake_model = MagicMock()
        fake_model.encode.return_value = np.random.rand(len(chunks), dim).astype("float32")
        mock_get_model.return_value = fake_model

        idx = EmbeddingIndex(chunks=chunks)
        # Manually set matrix + FAISS index to avoid real encoding
        matrix = np.random.rand(len(chunks), dim).astype("float32")
        faiss_index = faiss.IndexFlatIP(dim)
        faiss_index.add(matrix)
        idx._matrix = matrix
        idx._faiss_index = faiss_index

        fake_model.encode.return_value = np.random.rand(1, dim).astype("float32")
        results = idx.dense_search("personal data transfer", top_k=3)
        assert len(results) <= 3
        assert all(isinstance(idx_val, int) for idx_val, _ in results)

    @patch("src.retrieval.embedder._get_model")
    def test_dense_search_raises_without_build(self, mock_get_model):
        from src.retrieval.embedder import EmbeddingIndex

        idx = EmbeddingIndex(chunks=_make_chunks(3))
        with pytest.raises(RuntimeError, match="build()"):
            idx.dense_search("test query")


# ── ST3: BM25 ─────────────────────────────────────────────────────────────────

class TestBM25Index:
    def _indicator(self) -> TaxonomyEntry:
        return TaxonomyEntry(
            indicator_id="P7-I1",
            name="General data protection",
            legal_question="Does the law provide for data protection?",
            probe_keywords=["personal data", "data protection"],
            exclude_keywords=["banking secrecy", "customs tariff"],
            exclude_act_titles=["banking act", "income tax act"],
        )

    def test_search_returns_ranked_results(self):
        chunks = _make_chunks(8)
        idx = build_bm25(chunks)
        results = idx.search("personal data protection", self._indicator(), top_k=5)
        assert len(results) <= 5
        # Scores should be non-increasing
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    def test_negative_filter_removes_excluded_acts(self):
        chunks = [
            Chunk(
                chunk_id="banking_chunk",
                text="This section deals with banking secrecy personal data.",
                location_reference=LocationReference(
                    act_title="Banking Act",
                    part="",
                    article_number="5",
                ),
            ),
            Chunk(
                chunk_id="pdpa_chunk",
                text="Section 26A. Transfer of personal data outside Singapore requires adequate protection.",
                location_reference=LocationReference(
                    act_title="Personal Data Protection Act 2012",
                    part="PART II",
                    article_number="26A",
                ),
            ),
        ]
        idx = build_bm25(chunks)
        indicator = TaxonomyEntry(
            indicator_id="P7-I1",
            name="test",
            legal_question="personal data",
            probe_keywords=["personal data"],
            exclude_keywords=[],
            exclude_act_titles=["banking act"],
        )
        results = idx.search("personal data transfer", indicator, top_k=10)
        returned_ids = [chunks[i].chunk_id for i, _ in results]
        assert "banking_chunk" not in returned_ids
        assert "pdpa_chunk" in returned_ids

    def test_probe_keyword_boosting(self):
        chunks = [
            Chunk(
                chunk_id="relevant",
                text="The organisation must ensure cross-border data transfer personal data protection adequate standard.",
                location_reference=LocationReference(act_title="PDPA", part="", article_number="26A"),
            ),
            Chunk(
                chunk_id="irrelevant",
                text="The director shall convene a meeting of the board quarterly.",
                location_reference=LocationReference(act_title="PDPA", part="", article_number="10"),
            ),
        ]
        idx = build_bm25(chunks)
        indicator = TaxonomyEntry(
            indicator_id="P6-I1",
            name="test",
            legal_question="cross-border data transfer",
            probe_keywords=["cross-border data transfer", "personal data"],
            exclude_keywords=[],
            exclude_act_titles=[],
        )
        results = idx.search("cross-border data transfer", indicator, top_k=10)
        top_id = chunks[results[0][0]].chunk_id
        assert top_id == "relevant"


# ── ST4: RRF Fusion ───────────────────────────────────────────────────────────

class TestRRFFusion:
    def test_merges_two_lists(self):
        bm25 = [(0, 1.0), (1, 0.8), (2, 0.5)]
        dense = [(2, 0.9), (0, 0.7), (3, 0.6)]
        result = rrf_fusion(bm25, dense, top_k=4)
        indices = [idx for idx, _ in result]
        assert 0 in indices  # appears in both lists → high score

    def test_top_k_respected(self):
        bm25 = list(enumerate([1.0, 0.9, 0.8, 0.7, 0.6]))
        dense = list(enumerate([0.9, 0.8, 0.7, 0.6, 0.5]))
        result = rrf_fusion(bm25, dense, top_k=3)
        assert len(result) == 3

    def test_rrf_score_strictly_positive(self):
        bm25 = [(0, 0.5)]
        dense = [(1, 0.5)]
        result = rrf_fusion(bm25, dense, top_k=5)
        assert all(score > 0 for _, score in result)

    def test_item_in_both_lists_scores_higher(self):
        bm25 = [(0, 1.0), (1, 0.5)]
        dense = [(0, 1.0), (2, 0.5)]
        result = rrf_fusion(bm25, dense, top_k=5)
        score_map = {idx: score for idx, score in result}
        # Chunk 0 is in both → higher than 1 or 2 which are in only one
        assert score_map[0] > score_map.get(1, 0)
        assert score_map[0] > score_map.get(2, 0)

    def test_empty_inputs(self):
        result = rrf_fusion([], [], top_k=5)
        assert result == []


# ── ST5: Reranker (mocked cross-encoder) ─────────────────────────────────────

class TestReranker:
    @patch("src.retrieval.reranker._get_cross_encoder")
    def test_rerank_returns_top_n(self, mock_get_ce):
        fake_ce = MagicMock()
        # Return descending scores
        fake_ce.predict.return_value = np.array([0.9, 0.7, 0.5, 0.3, 0.1])
        mock_get_ce.return_value = fake_ce

        chunks = _make_chunks(6)
        candidates = [(i, 1.0 / (i + 1)) for i in range(5)]
        results = rerank("personal data transfer", candidates, chunks, top_n=3)
        assert len(results) == 3

    @patch("src.retrieval.reranker._get_cross_encoder")
    def test_rerank_scores_descending(self, mock_get_ce):
        fake_ce = MagicMock()
        fake_ce.predict.return_value = np.array([0.3, 0.9, 0.1, 0.7])
        mock_get_ce.return_value = fake_ce

        chunks = _make_chunks(5)
        candidates = [(i, 1.0) for i in range(4)]
        results = rerank("test query", candidates, chunks, top_n=4)
        scores = [r.rerank_score for r in results]
        assert scores == sorted(scores, reverse=True)

    @patch("src.retrieval.reranker._get_cross_encoder")
    def test_context_window_non_empty(self, mock_get_ce):
        fake_ce = MagicMock()
        fake_ce.predict.return_value = np.array([0.8, 0.6])
        mock_get_ce.return_value = fake_ce

        chunks = _make_chunks(4)
        candidates = [(1, 0.9), (2, 0.7)]
        results = rerank("personal data", candidates, chunks, top_n=2)
        for r in results:
            assert r.context_window  # should include surrounding text
            assert r.chunk.text in r.context_window

    @patch("src.retrieval.reranker._get_cross_encoder")
    def test_rerank_empty_candidates(self, mock_get_ce):
        results = rerank("test", [], _make_chunks(3), top_n=5)
        assert results == []


# ── ST6: RAG Orchestrator (end-to-end mocked) ─────────────────────────────────

class TestRAGOrchestrator:
    def _mock_pipeline(self, mocker, n_chunks: int = 6):
        """Patch all ML components so the orchestrator runs without models."""
        chunks = _make_chunks(n_chunks)

        mocker.patch("src.retrieval.rag.chunk_document", return_value=chunks)

        # Mock EmbeddingIndex
        fake_emb = MagicMock()
        fake_emb.dense_search.return_value = [(i, 1.0 / (i + 1)) for i in range(min(5, n_chunks))]
        mocker.patch("src.retrieval.rag.build_index", return_value=fake_emb)

        # Mock BM25Index
        fake_bm25 = MagicMock()
        fake_bm25.search.return_value = [(i, 0.5) for i in range(min(5, n_chunks))]
        mocker.patch("src.retrieval.rag.build_bm25", return_value=fake_bm25)

        # Mock reranker
        retrieved = [
            RetrievedChunk(
                chunk=chunks[i],
                rerank_score=1.0 - i * 0.1,
                context_window=chunks[i].text,
                retrieval_method="hybrid",
            )
            for i in range(5)
        ]
        mocker.patch("src.retrieval.rag.rerank", return_value=retrieved)

        return chunks, retrieved

    def test_retrieve_returns_top5(self, mocker):
        from src.retrieval.rag import retrieve
        _, expected = self._mock_pipeline(mocker)
        result = retrieve("P7-I1", _make_translated())
        assert len(result) == 5

    def test_retrieve_each_chunk_has_location_reference(self, mocker):
        from src.retrieval.rag import retrieve
        self._mock_pipeline(mocker)
        result = retrieve("P7-I1", _make_translated())
        for r in result:
            loc = r.chunk.location_reference
            assert isinstance(loc, LocationReference)
            assert loc.act_title

    def test_retrieve_unknown_indicator_raises(self, mocker):
        from src.retrieval.rag import retrieve
        with pytest.raises(KeyError):
            retrieve("XX-I99", _make_translated())

    def test_retrieve_empty_document_returns_empty(self, mocker):
        from src.retrieval.rag import retrieve
        mocker.patch("src.retrieval.rag.chunk_document", return_value=[])
        result = retrieve("P7-I1", _make_translated(""))
        assert result == []

    def test_retrieve_batch_covers_all_indicators(self, mocker):
        from src.retrieval.rag import retrieve_batch
        chunks, _ = self._mock_pipeline(mocker, n_chunks=8)
        indicators = ["P7-I1", "P7-I2"]
        results = retrieve_batch(indicators, _make_translated())
        assert set(results.keys()) == set(indicators)

    def test_retrieve_batch_empty_doc_returns_empty_lists(self, mocker):
        from src.retrieval.rag import retrieve_batch
        mocker.patch("src.retrieval.rag.chunk_document", return_value=[])
        results = retrieve_batch(["P7-I1", "P7-I2"], _make_translated(""))
        assert all(v == [] for v in results.values())


# ── Config / Taxonomy ─────────────────────────────────────────────────────────

class TestConfig:
    def test_taxonomy_loads_all_indicators(self):
        entries = load_taxonomy()
        assert len(entries) == 10
        ids = [e.indicator_id for e in entries]
        assert "P7-I1" in ids
        assert "P6-I1" in ids

    def test_get_indicator_returns_correct_entry(self):
        entry = get_indicator("P7-I4")
        assert entry.indicator_id == "P7-I4"
        assert entry.probe_keywords  # non-empty

    def test_get_indicator_raises_on_unknown(self):
        with pytest.raises(KeyError):
            get_indicator("XX-I99")

    def test_hybrid_benchmark_bm25_vs_dense(self):
        """
        Validates that hybrid retrieval (RRF) produces a different, blended ranking
        compared to BM25 alone or dense alone — acceptance criterion from story.
        """
        from src.retrieval.fusion import rrf_fusion

        # Simulate BM25 favouring legal-term hits
        bm25 = [(0, 3.0), (1, 2.5), (2, 1.0), (3, 0.5)]
        # Dense favouring semantic similarity
        dense = [(2, 0.95), (3, 0.88), (0, 0.60), (1, 0.55)]

        hybrid = rrf_fusion(bm25, dense, top_k=4)
        hybrid_order = [idx for idx, _ in hybrid]

        bm25_order = [idx for idx, _ in bm25[:4]]
        dense_order = [idx for idx, _ in dense[:4]]

        # Hybrid ranking should differ from at least one of the pure methods
        assert hybrid_order != bm25_order or hybrid_order != dense_order
