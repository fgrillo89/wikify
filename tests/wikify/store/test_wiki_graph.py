"""Tests for WikiKnowledgeGraph + WikiQueryBuilder."""

from __future__ import annotations

import pytest

from wikify.models import Evidence, WikiPage
from wikify.store.wiki_graph import (
    PAGE,
    WikiKnowledgeGraph,
    build_wiki_graph,
    build_wiki_vectors,
    load_wiki_graph,
    save_wiki_graph,
)


def _make_pages() -> list[WikiPage]:
    return [
        WikiPage(
            id="Atomic Layer Deposition",
            kind="article",
            title="Atomic Layer Deposition",
            aliases=["ALD"],
            body_markdown="Atomic layer deposition (ALD) is a thin-film growth technique used in semiconductor manufacturing.",
            evidence=[
                Evidence(marker="e1", chunk_id="p1_c0", doc_id="p1", quote="ALD is a thin-film"),
                Evidence(marker="e2", chunk_id="p1_c1", doc_id="p1", quote="growth technique"),
                Evidence(marker="e3", chunk_id="p2_c0", doc_id="p2", quote="ALD for memristors"),
            ],
            links=["Memristor", "Hafnium Oxide"],
        ),
        WikiPage(
            id="Memristor",
            kind="article",
            title="Memristor",
            aliases=["memristive device"],
            body_markdown="A memristor is a resistive switching device that retains state without power.",
            evidence=[
                Evidence(marker="e1", chunk_id="p2_c1", doc_id="p2", quote="resistive switching"),
                Evidence(marker="e2", chunk_id="p3_c0", doc_id="p3", quote="memristor concept"),
            ],
            links=["Resistive Switching"],
        ),
        WikiPage(
            id="Hafnium Oxide",
            kind="article",
            title="Hafnium Oxide",
            aliases=["HfO2"],
            body_markdown="Hafnium oxide is a high-k dielectric material used in ALD-grown films for memristive devices.",
            evidence=[
                Evidence(marker="e1", chunk_id="p1_c2", doc_id="p1", quote="HfO2 dielectric"),
                Evidence(marker="e2", chunk_id="p2_c2", doc_id="p2", quote="hafnium oxide films"),
            ],
            links=["Atomic Layer Deposition"],
        ),
        WikiPage(
            id="Resistive Switching",
            kind="article",
            title="Resistive Switching",
            aliases=[],
            body_markdown="Resistive switching is the voltage-driven change of conductance in metal-insulator-metal stacks.",
            evidence=[
                Evidence(marker="e1", chunk_id="p3_c1", doc_id="p3", quote="switching mechanism"),
            ],
            links=["Memristor"],
        ),
        WikiPage(
            id="Stuart Parkin",
            kind="person",
            title="Stuart Parkin",
            aliases=[],
            body_markdown="Stuart Parkin is a physicist known for contributions to spintronics and racetrack memory.",
            evidence=[
                Evidence(marker="e1", chunk_id="p4_c0", doc_id="p4", quote="Parkin spintronics"),
            ],
            links=[],
        ),
    ]


def _hash_embed(texts):
    from wikify.embedding import _hash_embed
    return _hash_embed(texts)


@pytest.fixture
def pages():
    return _make_pages()


@pytest.fixture
def wkg(pages) -> WikiKnowledgeGraph:
    vectors = build_wiki_vectors(pages, _hash_embed)
    return build_wiki_graph(pages, vectors=vectors, embed_fn=_hash_embed)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


class TestEntryPoints:
    def test_page_existing(self, wkg):
        qb = wkg.page("Memristor")
        assert qb.count() == 1
        assert qb.first()["title"] == "Memristor"

    def test_page_missing(self, wkg):
        assert wkg.page("Nonexistent").count() == 0

    def test_pages_all(self, wkg):
        assert wkg.pages().count() == 5

    def test_pages_filtered(self, wkg):
        persons = wkg.pages(kind="person")
        assert persons.count() == 1
        assert persons.first()["id"] == "Stuart Parkin"

    def test_titles(self, wkg):
        titles = wkg.pages().titles()
        assert len(titles) == 5
        assert "Atomic Layer Deposition" in titles
        assert "Stuart Parkin" in titles

    def test_titles_filtered(self, wkg):
        titles = wkg.pages(kind="article").titles()
        assert "Stuart Parkin" not in titles
        assert "Atomic Layer Deposition" in titles

    def test_stats(self, wkg):
        s = wkg.stats()
        assert s["pages"] == 5
        assert s["evidence"] > 0
        assert s["edges"] > 0


# ---------------------------------------------------------------------------
# Traversals
# ---------------------------------------------------------------------------


class TestTraversals:
    def test_links(self, wkg):
        """ALD links to Memristor and Hafnium Oxide."""
        linked = wkg.page("Atomic Layer Deposition").links()
        ids = set(linked.ids())
        assert "Memristor" in ids
        assert "Hafnium Oxide" in ids

    def test_linked_by(self, wkg):
        """Memristor is linked to by ALD."""
        linkers = wkg.page("Memristor").linked_by()
        assert "Atomic Layer Deposition" in set(linkers.ids())

    def test_co_evidence(self, wkg):
        """ALD and Memristor share doc p2 as evidence source."""
        co = wkg.page("Atomic Layer Deposition").co_evidence()
        ids = set(co.ids())
        assert "Memristor" in ids
        assert "Hafnium Oxide" in ids

    def test_co_evidence_excludes_self(self, wkg):
        co = wkg.page("Memristor").co_evidence()
        assert "Memristor" not in set(co.ids())

    def test_evidence(self, wkg):
        """ALD has 3 evidence entries."""
        ev = wkg.page("Atomic Layer Deposition").evidence()
        assert ev.count() == 3
        first = ev.first()
        assert first["type"] == "evidence"
        assert "chunk_id" in first


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestFilters:
    def test_where(self, wkg):
        articles = wkg.pages().where(kind="article")
        assert articles.count() == 4

    def test_top_by_n_evidence(self, wkg):
        top1 = wkg.pages().top(1, by="n_evidence")
        # ALD has 3 evidence entries, the most
        assert top1.first()["id"] == "Atomic Layer Deposition"


# ---------------------------------------------------------------------------
# Fluent chaining
# ---------------------------------------------------------------------------


class TestFluentChaining:
    def test_links_then_co_evidence(self, wkg):
        """Pages linked from ALD -> their co-evidence neighbors."""
        result = wkg.page("Atomic Layer Deposition").links().co_evidence()
        # Memristor and HfO2 are linked; their co-evidence includes ALD
        assert result.count() > 0

    def test_chain_narrows(self, wkg):
        all_count = wkg.pages().count()
        linked_count = wkg.page("Memristor").links().count()
        assert linked_count < all_count


# ---------------------------------------------------------------------------
# Vector search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_all(self, wkg):
        results = wkg.search("thin film deposition technique", top_k=3)
        assert len(results) > 0
        assert all("score" in r for r in results)

    def test_search_scoped(self, wkg):
        """Search scoped to pages linked from ALD."""
        results = wkg.page("Atomic Layer Deposition").links().search(
            "resistive switching device", top_k=2,
        )
        linked_ids = set(wkg.page("Atomic Layer Deposition").links().ids())
        for r in results:
            assert r["id"] in linked_ids

    def test_search_no_vectors(self, pages):
        """search() returns empty when no vectors."""
        wkg = build_wiki_graph(pages)
        assert wkg.search("test") == []


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_roundtrip(self, wkg, tmp_path):
        path = tmp_path / "wkg.json"
        save_wiki_graph(path, wkg)
        assert path.exists()

        loaded = load_wiki_graph(path)
        assert loaded.pages().count() == wkg.pages().count()
        assert set(loaded.page("Memristor").links().ids()) == set(
            wkg.page("Memristor").links().ids()
        )


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


class TestBuild:
    def test_page_nodes(self, wkg):
        for pid in ["Atomic Layer Deposition", "Memristor", "Hafnium Oxide",
                     "Resistive Switching", "Stuart Parkin"]:
            node = wkg.page(pid).first()
            assert node is not None
            assert node["type"] == PAGE

    def test_links_to_edges(self, wkg):
        assert "Memristor" in set(wkg.page("Atomic Layer Deposition").links().ids())

    def test_co_evidence_from_shared_doc(self, wkg):
        """ALD and HfO2 both cite doc p1 -> CO_EVIDENCE edge."""
        co = wkg.page("Atomic Layer Deposition").co_evidence()
        assert "Hafnium Oxide" in set(co.ids())

    def test_evidence_doc_ids_on_node(self, wkg):
        node = wkg.page("Atomic Layer Deposition").first()
        assert "p1" in node["evidence_doc_ids"]
        assert "p2" in node["evidence_doc_ids"]

    def test_wiki_vectors(self, pages):
        vectors = build_wiki_vectors(pages, _hash_embed)
        assert len(vectors.ids) == 5
        assert vectors.matrix.shape[0] == 5
