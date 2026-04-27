"""Tests for KnowledgeGraph + QueryBuilder fluent API."""

from __future__ import annotations

from pathlib import Path

import pytest

from wikify.corpus.graph import (
    AUTHOR,
    SOURCE,
    KnowledgeGraph,
)
from wikify.corpus.graph_build import (
    build_knowledge_graph,
    load_knowledge_graph,
    save_knowledge_graph,
)
from wikify.corpus.vectors import VectorStore
from wikify.models import Chunk, DocImage, DocSection, Document

# ---------------------------------------------------------------------------
# Fixture: small graph with 3 papers, 2 authors, chunks, sections
# ---------------------------------------------------------------------------


def _make_docs() -> list[Document]:
    """3 papers: A cites B, C cites A and B."""
    return [
        Document(
            id="paper_A",
            source_path="a.pdf",
            kind="pdf",
            title="Paper A: Foundations",
            metadata={"authors": ["Smith, J.", "Jones, K."], "year": 2020, "doi": "10.1/a"},
            markdown_path="corpus/markdown/paper_A.md",
            image_dir="corpus/images/paper_A/",
            sections=[
                DocSection(path=["Introduction"], chunk_ids=["paper_A_c0"]),
                DocSection(path=["Methods"], chunk_ids=["paper_A_c1"]),
                DocSection(path=["Conclusions"], chunk_ids=["paper_A_c2"]),
            ],
            images=[
                DocImage(id="paper_A/fig_01", path="corpus/images/paper_A/fig_01.png",
                         caption="IV curve", near_chunk_ids=["paper_A_c1"]),
            ],
            equations=[{"id": "paper_A_eq1", "latex": "V=IR", "label": "Eq. 1", "kind": "inline", "chunk_id": "paper_A_c1"}],
            cites=["paper_B"],
            n_chunks=3,
            n_tokens=1500,
        ),
        Document(
            id="paper_B",
            source_path="b.pdf",
            kind="pdf",
            title="Paper B: Methods",
            metadata={"authors": ["Smith, J."], "year": 2019, "doi": "10.1/b"},
            markdown_path="corpus/markdown/paper_B.md",
            image_dir="corpus/images/paper_B/",
            sections=[
                DocSection(path=["Introduction"], chunk_ids=["paper_B_c0"]),
                DocSection(path=["Results"], chunk_ids=["paper_B_c1"]),
            ],
            cites=[],
            n_chunks=2,
            n_tokens=1000,
        ),
        Document(
            id="paper_C",
            source_path="c.pdf",
            kind="pdf",
            title="Paper C: Applications",
            metadata={"authors": ["Jones, K.", "Lee, M."], "year": 2021, "doi": "10.1/c"},
            markdown_path="corpus/markdown/paper_C.md",
            image_dir="corpus/images/paper_C/",
            sections=[
                DocSection(path=["Introduction"], chunk_ids=["paper_C_c0"]),
                DocSection(path=["Discussion"], chunk_ids=["paper_C_c1"]),
            ],
            cites=["paper_A", "paper_B"],
            n_chunks=2,
            n_tokens=800,
        ),
    ]


def _make_chunks() -> list[Chunk]:
    return [
        Chunk(id="paper_A_c0", doc_id="paper_A", ord=0, text="Introduction to foundations of memristors.",
              char_span=(0, 100), section_path=["Introduction"], section_type="introduction"),
        Chunk(id="paper_A_c1", doc_id="paper_A", ord=1, text="Methods for fabricating thin film devices using ALD.",
              char_span=(100, 250), section_path=["Methods"], section_type="methods",
              equation_ids=["paper_A_eq1"]),
        Chunk(id="paper_A_c2", doc_id="paper_A", ord=2, text="Conclusions about switching behavior in memristors.",
              char_span=(250, 400), section_path=["Conclusions"], section_type="conclusions"),
        Chunk(id="paper_B_c0", doc_id="paper_B", ord=0, text="Introduction to experimental methods for oxide films.",
              char_span=(0, 120), section_path=["Introduction"], section_type="introduction"),
        Chunk(id="paper_B_c1", doc_id="paper_B", ord=1, text="Results of resistive switching measurements in HfO2.",
              char_span=(120, 280), section_path=["Results"], section_type="results"),
        Chunk(id="paper_C_c0", doc_id="paper_C", ord=0, text="Introduction to applications of memristive devices.",
              char_span=(0, 100), section_path=["Introduction"], section_type="introduction"),
        Chunk(id="paper_C_c1", doc_id="paper_C", ord=1, text="Discussion of neuromorphic computing with memristors.",
              char_span=(100, 250), section_path=["Discussion"], section_type="discussion"),
    ]


def _make_vectors(chunks: list[Chunk]) -> VectorStore:
    """Deterministic hash-based embeddings for testing."""
    from wikify.embedding import _hash_embed

    texts = [ck.text for ck in chunks]
    matrix = _hash_embed(texts)
    return VectorStore(ids=[ck.id for ck in chunks], matrix=matrix)


@pytest.fixture
def fixture_data():
    docs = _make_docs()
    chunks = _make_chunks()
    vectors = _make_vectors(chunks)
    return docs, chunks, vectors


@pytest.fixture
def kg(fixture_data) -> KnowledgeGraph:
    docs, chunks, vectors = fixture_data

    return build_knowledge_graph(docs, chunks, vectors=vectors)


@pytest.fixture
def kg_with_search(fixture_data) -> KnowledgeGraph:
    docs, chunks, vectors = fixture_data
    from wikify.embedding import _hash_embed

    kg = build_knowledge_graph(docs, chunks, vectors=vectors)
    kg._embed_fn = _hash_embed
    return kg


# ---------------------------------------------------------------------------
# KnowledgeGraph entry points
# ---------------------------------------------------------------------------


class TestKnowledgeGraphEntryPoints:
    def test_source_existing(self, kg: KnowledgeGraph):
        qb = kg.source("paper_A")
        assert qb.count() == 1
        assert qb.first()["id"] == "paper_A"

    def test_source_missing(self, kg: KnowledgeGraph):
        qb = kg.source("nonexistent")
        assert qb.count() == 0
        assert qb.first() is None

    def test_sources_all(self, kg: KnowledgeGraph):
        # At least the 3 corpus papers
        assert kg.sources().count() >= 3

    def test_author_existing(self, kg: KnowledgeGraph):
        qb = kg.author("smith j")
        assert qb.count() == 1

    def test_authors_all(self, kg: KnowledgeGraph):
        assert kg.authors().count() >= 2

    def test_chunks_all(self, kg: KnowledgeGraph):
        assert kg.chunks().count() == 7

    def test_titles_sources(self, kg: KnowledgeGraph):
        titles = kg.sources(kind="corpus").titles()
        assert len(titles) == 3
        assert "Paper A: Foundations" in titles

    def test_titles_authors(self, kg: KnowledgeGraph):
        titles = kg.authors().titles()
        # Authors have no 'title' attr, so titles() returns the node id (name)
        assert "smith j" in titles

    def test_corpus_stats(self, kg: KnowledgeGraph):
        stats = kg.corpus_stats()
        assert stats["sources"] >= 3
        assert stats["authors"] >= 2
        assert stats["chunks"] == 7
        assert stats["edges"] > 0


# ---------------------------------------------------------------------------
# Citation traversals
# ---------------------------------------------------------------------------


class TestCitationTraversals:
    def test_cited_by(self, kg: KnowledgeGraph):
        """Paper A is cited by Paper C."""
        citing = kg.source("paper_A").cited_by()
        ids = set(citing.ids())
        assert "paper_C" in ids

    def test_references(self, kg: KnowledgeGraph):
        """Paper C cites A and B."""
        refs = kg.source("paper_C").references()
        ids = set(refs.ids())
        assert "paper_A" in ids
        assert "paper_B" in ids

    def test_cited_by_count(self, kg: KnowledgeGraph):
        """Paper B is cited by A and C."""
        assert kg.source("paper_B").cited_by().count() == 2

    def test_references_with_ords(self, kg: KnowledgeGraph):
        """Test ordinal-based reference lookup when ord_refs is available."""
        # Paper A has ord_refs built from its citations
        refs_all = kg.source("paper_A").references()
        # At least paper_B should be in references
        assert "paper_B" in set(refs_all.ids())

    def test_neighborhood(self, kg: KnowledgeGraph):
        """1-hop neighbors include citation neighbors and authors."""
        neighbors = kg.source("paper_A").neighborhood(hops=1)
        assert neighbors.count() > 0


# ---------------------------------------------------------------------------
# Authorship traversals
# ---------------------------------------------------------------------------


class TestAuthorshipTraversals:
    def test_authors_of_source(self, kg: KnowledgeGraph):
        """Paper A has Smith and Jones as authors."""
        authors = kg.source("paper_A").authors()
        ids = set(authors.ids())
        assert "smith j" in ids
        assert "jones k" in ids

    def test_sources_of_author(self, kg: KnowledgeGraph):
        """Smith authored papers A and B."""
        sources = kg.author("smith j").sources()
        ids = set(sources.ids())
        assert "paper_A" in ids
        assert "paper_B" in ids

    def test_coauthors(self, kg: KnowledgeGraph):
        """Smith's coauthors include Jones (co-authored paper A)."""
        coauthors = kg.author("smith j").coauthors()
        ids = set(coauthors.ids())
        assert "jones k" in ids
        # Smith should NOT be in own coauthors
        assert "smith j" not in ids


# ---------------------------------------------------------------------------
# Document structure traversals
# ---------------------------------------------------------------------------


class TestStructureTraversals:
    def test_sections(self, kg: KnowledgeGraph):
        """Paper A has 3 sections."""
        sections = kg.source("paper_A").sections()
        assert sections.count() == 3

    def test_sections_by_type(self, kg: KnowledgeGraph):
        """Filter sections by type."""
        conclusions = kg.source("paper_A").sections(type="conclusions")
        assert conclusions.count() == 1

    def test_chunks_of_source(self, kg: KnowledgeGraph):
        """Paper A has 3 chunks."""
        chunks = kg.source("paper_A").chunks()
        assert chunks.count() == 3

    def test_chunks_of_section(self, kg: KnowledgeGraph):
        """Chunks of a specific section."""
        methods = kg.source("paper_A").sections(type="methods")
        chunks = methods.chunks()
        assert chunks.count() == 1
        assert "paper_A_c1" in set(chunks.ids())

    def test_figures(self, kg: KnowledgeGraph):
        """Paper A has 1 figure."""
        figs = kg.source("paper_A").figures()
        assert figs.count() == 1
        fig = figs.first()
        assert fig["caption"] == "IV curve"

    def test_equations(self, kg: KnowledgeGraph):
        """Paper A has 1 equation."""
        eqs = kg.source("paper_A").equations()
        assert eqs.count() == 1
        eq = eqs.first()
        assert eq["latex"] == "V=IR"


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestFilters:
    def test_where(self, kg: KnowledgeGraph):
        """Filter sources by kind=corpus."""
        corpus = kg.sources().where(kind="corpus")
        assert corpus.count() == 3

    def test_since(self, kg: KnowledgeGraph):
        """Filter by year >= 2021."""
        recent = kg.sources().since(2021)
        ids = set(recent.ids())
        assert "paper_C" in ids
        assert "paper_B" not in ids  # 2019

    def test_top_by_year(self, kg: KnowledgeGraph):
        """Top 1 by year should be the newest."""
        top1 = kg.sources().where(kind="corpus").top(1, by="year")
        assert top1.first()["id"] == "paper_C"

    def test_of_type(self, kg: KnowledgeGraph):
        """Filter mixed set to just sources."""
        all_nodes = kg.source("paper_A").neighborhood(hops=1)
        sources_only = all_nodes.of_type(SOURCE)
        for node in sources_only.collect():
            assert node["type"] == SOURCE


# ---------------------------------------------------------------------------
# Fluent chaining
# ---------------------------------------------------------------------------


class TestFluentChaining:
    def test_cited_by_then_chunks(self, kg: KnowledgeGraph):
        """UC1: chunks from papers citing paper_A."""
        chunks = kg.source("paper_A").cited_by().chunks()
        # Paper C cites A, so C's chunks should be in result
        ids = set(chunks.ids())
        assert "paper_C_c0" in ids or "paper_C_c1" in ids

    def test_chain_narrows_scope(self, kg: KnowledgeGraph):
        """Each step narrows: all sources > cited_by > chunks."""
        all_count = kg.sources().count()
        cited_count = kg.source("paper_A").cited_by().count()
        chunk_count = kg.source("paper_A").cited_by().chunks().count()
        assert cited_count < all_count
        assert chunk_count > 0

    def test_author_to_sources_to_cited_by(self, kg: KnowledgeGraph):
        """Author -> their papers -> who cites those papers."""
        citers = kg.author("smith j").sources().cited_by()
        # Smith wrote A and B; C cites both
        assert "paper_C" in set(citers.ids())

    def test_sections_conclusions_chunks(self, kg: KnowledgeGraph):
        """UC1: conclusions sections -> chunks."""
        chunks = kg.source("paper_A").sections(type="conclusions").chunks()
        assert "paper_A_c2" in set(chunks.ids())

    def test_figures_from_citing_papers(self, kg: KnowledgeGraph):
        """UC: figures from papers citing B."""
        figs = kg.source("paper_B").cited_by().figures()
        # Paper A and C cite B; A has a figure
        assert figs.count() >= 1


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_pagerank(self, kg: KnowledgeGraph):
        """Papers with citations should have positive PageRank."""
        pr = kg.sources().where(kind="corpus").pagerank()
        # Paper B is cited by both A and C -> should have highest
        assert pr.get("paper_B", 0) > 0

    def test_citation_count(self, kg: KnowledgeGraph):
        """Citation counts computed from graph."""
        cc = kg.sources().where(kind="corpus").citation_count()
        assert cc["paper_B"] == 2  # cited by A and C
        assert cc["paper_A"] == 1  # cited by C

    def test_h_index(self, kg: KnowledgeGraph):
        """Smith has 2 papers each with >= 1 citation -> h=1 or h=2."""
        author = kg.author("smith j").first()
        assert author is not None
        # h-index depends on citation counts of smith's papers
        assert author.get("h_index", 0) >= 1

    def test_top_by_pagerank(self, kg: KnowledgeGraph):
        """Top source by pagerank should be paper_B (most cited)."""
        top = kg.sources().where(kind="corpus").top(1, by="pagerank")
        assert top.first()["id"] == "paper_B"


# ---------------------------------------------------------------------------
# Vector search (scoped)
# ---------------------------------------------------------------------------


class TestScopedSearch:
    def test_search_all_chunks(self, kg_with_search: KnowledgeGraph):
        """Global search returns results."""
        results = kg_with_search.search("memristor switching", top_k=3)
        assert len(results) > 0
        assert all("score" in r for r in results)

    def test_search_scoped_to_source(self, kg_with_search: KnowledgeGraph):
        """Search scoped to paper_A's chunks only returns paper_A chunks."""
        results = kg_with_search.source("paper_A").chunks().search(
            "methods fabrication", top_k=3,
        )
        assert len(results) > 0
        for r in results:
            assert r["source_id"] == "paper_A"

    def test_search_scoped_to_citing(self, kg_with_search: KnowledgeGraph):
        """UC9: search scoped to papers citing paper_A."""
        results = kg_with_search.source("paper_A").cited_by().chunks().search(
            "neuromorphic", top_k=3,
        )
        citing_ids = set(kg_with_search.source("paper_A").cited_by().ids())
        for r in results:
            assert r["source_id"] in citing_ids

    def test_search_no_vectors(self, kg: KnowledgeGraph):
        """search() returns empty when no embed_fn is set."""
        results = kg.source("paper_A").chunks().search("test", top_k=3)
        assert results == []


# ---------------------------------------------------------------------------
# Nearby traversals (chunk -> figure/equation)
# ---------------------------------------------------------------------------


class TestNearbyTraversals:
    def test_nearby_figures(self, kg: KnowledgeGraph):
        """Chunks near figure should find it via nearby_figures."""
        # paper_A_c1 is near fig_01 (near_chunk_ids includes it)
        # Use chunks() then filter to just c1's neighbors
        all_figs = kg.source("paper_A").chunks().nearby_figures()
        assert all_figs.exists()
        # The figure linked to paper_A_c1 should be in the results
        fig_ids = set(all_figs.ids())
        assert "paper_A/fig_01" in fig_ids

    def test_nearby_equations(self, kg: KnowledgeGraph):
        """Chunks containing equations should find them via nearby_equations."""
        all_eqs = kg.source("paper_A").chunks().nearby_equations()
        assert all_eqs.exists()
        eq_ids = set(all_eqs.ids())
        assert "paper_A_eq1" in eq_ids

    def test_figures_scoped_search(self, kg_with_search: KnowledgeGraph):
        """Searching from figures resolves to their nearby chunks."""
        results = kg_with_search.source("paper_A").figures().search(
            "methods fabrication", top_k=3,
        )
        # Figure paper_A/fig_01 is near chunk paper_A_c1 -> search should scope to that
        assert len(results) > 0

    def test_equations_scoped_search(self, kg_with_search: KnowledgeGraph):
        """Searching from equations resolves to their chunks."""
        results = kg_with_search.source("paper_A").equations().search(
            "methods fabrication", top_k=3,
        )
        assert len(results) > 0

    def test_nearby_figures_from_search(self, kg_with_search: KnowledgeGraph):
        """Search for chunks, then find nearby figures."""
        hits = kg_with_search.source("paper_A").chunks().search("methods ALD", top_k=3)
        if hits:
            qb = kg_with_search.chunks().where(id=hits[0]["id"])
            # Just verify the chain doesn't crash
            figs = qb.nearby_figures()
            assert isinstance(figs.count(), int)


# ---------------------------------------------------------------------------
# Match filter (keyword search on node attributes)
# ---------------------------------------------------------------------------


class TestMatchFilter:
    def test_match_figure_caption(self, kg: KnowledgeGraph):
        """Find figures by caption keyword."""
        figs = kg.sources().figures().match("caption", "IV curve")
        assert figs.count() == 1
        assert figs.first()["caption"] == "IV curve"

    def test_match_equation_label(self, kg: KnowledgeGraph):
        """Find equations by label."""
        eqs = kg.sources().equations().match("label", "Eq. 1")
        assert eqs.count() == 1

    def test_match_case_insensitive(self, kg: KnowledgeGraph):
        """Match is case-insensitive."""
        figs = kg.sources().figures().match("caption", "iv curve")
        assert figs.count() == 1

    def test_match_no_results(self, kg: KnowledgeGraph):
        """Match with no hits returns empty."""
        figs = kg.sources().figures().match("caption", "nonexistent xyz")
        assert figs.count() == 0

    def test_match_source_title(self, kg: KnowledgeGraph):
        """Match on source titles."""
        sources = kg.sources().match("title", "Foundations")
        assert sources.count() == 1
        assert sources.first()["id"] == "paper_A"


# ---------------------------------------------------------------------------
# Similar_to (chunk-to-chunk cosine via existing vectors)
# ---------------------------------------------------------------------------


class TestSimilarTo:
    def test_similar_to_returns_results(self, kg_with_search: KnowledgeGraph):
        """similar_to finds chunks similar to a given chunk."""
        results = kg_with_search.chunks().similar_to("paper_A_c0", top_k=3)
        assert len(results) > 0
        assert all("score" in r for r in results)
        # Should not include the seed chunk itself
        assert all(r["id"] != "paper_A_c0" for r in results)

    def test_similar_to_scoped(self, kg_with_search: KnowledgeGraph):
        """similar_to scoped to one source returns only that source's chunks."""
        results = kg_with_search.source("paper_A").chunks().similar_to(
            "paper_A_c0", top_k=3,
        )
        for r in results:
            assert r["source_id"] == "paper_A"

    def test_similar_to_no_vectors(self, fixture_data):
        """similar_to returns empty when no vectors attached."""
        docs, chunks, _ = fixture_data
        kg_no_vec = build_knowledge_graph(docs, chunks, vectors=None)
        assert kg_no_vec.chunks().similar_to("paper_A_c0") == []

    def test_similar_to_missing_chunk(self, kg_with_search: KnowledgeGraph):
        """similar_to with nonexistent chunk returns empty."""
        assert kg_with_search.chunks().similar_to("nonexistent") == []


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_load_roundtrip(self, kg: KnowledgeGraph, tmp_path: Path):
        """Save and load should produce equivalent graph."""
        path = tmp_path / "kg.json"
        save_knowledge_graph(path, kg)
        assert path.exists()

        loaded = load_knowledge_graph(path)
        assert loaded.sources().count() == kg.sources().count()
        assert loaded.authors().count() == kg.authors().count()
        assert loaded.chunks().count() == kg.chunks().count()

        # Traversals should still work
        assert set(loaded.source("paper_A").cited_by().ids()) == set(
            kg.source("paper_A").cited_by().ids()
        )

    def test_load_with_vectors(self, fixture_data, tmp_path: Path):
        """Loading with vectors enables search."""
        docs, chunks, vectors = fixture_data
        from wikify.embedding import _hash_embed

        kg = build_knowledge_graph(docs, chunks, vectors=vectors)
        path = tmp_path / "kg.json"
        save_knowledge_graph(path, kg)

        loaded = load_knowledge_graph(path, vectors=vectors, embed_fn=_hash_embed)
        results = loaded.search("memristor", top_k=2)
        assert len(results) > 0


# ---------------------------------------------------------------------------
# build_knowledge_graph unit tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Tracing
# ---------------------------------------------------------------------------


class TestTracing:
    def test_trace_disabled_by_default(self, kg_with_search: KnowledgeGraph):
        """No trace entries when tracing is disabled."""
        kg_with_search.search("test", top_k=2)
        assert len(kg_with_search._trace.entries) == 0

    def test_trace_logs_search(self, kg_with_search: KnowledgeGraph):
        """Trace logs search calls when enabled."""
        kg_with_search.enable_trace(caller="test")
        kg_with_search.search("memristor", top_k=2)
        assert len(kg_with_search._trace.entries) == 1
        entry = kg_with_search._trace.entries[0]
        assert entry.method == "search"
        assert entry.caller == "test"
        assert entry.output_count > 0
        kg_with_search.disable_trace()

    def test_trace_logs_collect(self, kg_with_search: KnowledgeGraph):
        """Trace logs collect calls."""
        kg_with_search.enable_trace(caller="sampler")
        kg_with_search.sources().collect()
        assert any(e.method == "collect" for e in kg_with_search._trace.entries)
        kg_with_search.disable_trace()

    def test_trace_save_load(self, kg_with_search: KnowledgeGraph, tmp_path: Path):
        """Trace saves to JSONL and can be loaded.

        The corpus KG owns its own trace persistence (``corpus/graph.py``);
        the ``wikify.eval.trace_replay`` aggregator is decoupled from it
        (it consumes ``run/events.jsonl``, not corpus KG trace files), so
        this test reads the JSONL directly.
        """
        import json as _json

        kg_with_search.enable_trace(caller="test")
        kg_with_search.search("topic", top_k=3)
        kg_with_search.sources().collect()
        path = tmp_path / "trace.jsonl"
        kg_with_search.save_trace(path)
        assert path.exists()

        lines = [
            _json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(lines) == 2
        callers = [d.get("caller") for d in lines]
        assert callers.count("test") == 2
        kg_with_search.disable_trace()


# ---------------------------------------------------------------------------
# build_knowledge_graph unit tests
# ---------------------------------------------------------------------------


class TestBuildKnowledgeGraph:
    def test_source_nodes_created(self, kg: KnowledgeGraph):
        """All 3 corpus papers become source nodes."""
        for pid in ["paper_A", "paper_B", "paper_C"]:
            node = kg.source(pid).first()
            assert node is not None
            assert node["type"] == SOURCE
            assert node["kind"] == "corpus"

    def test_author_nodes_created(self, kg: KnowledgeGraph):
        """Authors extracted from metadata."""
        for key in ["smith j", "jones k", "lee m"]:
            node = kg.author(key).first()
            assert node is not None
            assert node["type"] == AUTHOR

    def test_chunk_nodes_created(self, kg: KnowledgeGraph):
        """All 7 chunks become nodes."""
        assert kg.chunks().count() == 7

    def test_section_classification(self, kg: KnowledgeGraph):
        """Sections classified by heading keywords."""
        intro = kg.source("paper_A").sections(type="introduction")
        assert intro.count() == 1
        methods = kg.source("paper_A").sections(type="methods")
        assert methods.count() == 1

    def test_figure_nodes(self, kg: KnowledgeGraph):
        figs = kg.source("paper_A").figures()
        assert figs.count() == 1

    def test_equation_nodes(self, kg: KnowledgeGraph):
        eqs = kg.source("paper_A").equations()
        assert eqs.count() == 1

    def test_cites_edges(self, kg: KnowledgeGraph):
        """Citation edges from Document.cites."""
        assert "paper_B" in set(kg.source("paper_A").references().ids())
        refs_c = set(kg.source("paper_C").references().ids())
        assert "paper_A" in refs_c
        assert "paper_B" in refs_c

    def test_authored_by_edges(self, kg: KnowledgeGraph):
        """Authorship edges connect sources to authors."""
        assert "smith j" in set(kg.source("paper_A").authors().ids())
        assert "jones k" in set(kg.source("paper_A").authors().ids())

    def test_collaborated_edges(self, kg: KnowledgeGraph):
        """Co-authorship from shared papers."""
        assert "jones k" in set(kg.author("smith j").coauthors().ids())
