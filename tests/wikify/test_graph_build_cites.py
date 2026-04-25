"""Tests for CITES-edge emission in citestore.graph_build.

Verifies that the knowledge graph builder wires every citation from
``Document.citations`` to a source node -- either the in-corpus corpus
node (when the bibkey resolves to a corpus doc) or the dedicated
``cited`` node that represents the external reference.
"""

from __future__ import annotations

from wikify.citations.models import CitationEntry
from wikify.corpus.graph_build import build_knowledge_graph
from wikify.models import Document


def _doc(
    doc_id: str,
    title: str,
    *,
    citations: list[CitationEntry] | None = None,
    cites: list[str] | None = None,
    doi: str = "",
) -> Document:
    return Document(
        id=doc_id,
        source_path=f"/tmp/{doc_id}.pdf",
        kind="pdf",
        title=title,
        metadata={"authors": ["Alice Example"], "year": 2020, "doi": doi},
        markdown_path="",
        image_dir="",
        citations=list(citations or []),
        cites=list(cites or []),
    )


def _citation_index(
    docs: list[Document],
    *,
    doc_bibkeys: dict[str, str],
    doc_citations: dict[str, list[str]],
    external_entries: dict[str, dict],
) -> dict:
    entries: dict[str, dict] = {}
    for doc in docs:
        bibkey = doc_bibkeys[doc.id]
        entries[bibkey] = {
            "bibkey": bibkey,
            "kind": "source",
            "title": doc.title,
            "year": doc.metadata.get("year"),
            "doi": doc.metadata.get("doi", ""),
            "authors": doc.metadata.get("authors") or [],
        }
    entries.update(external_entries)
    return {
        "schema_version": 2,
        "entries": entries,
        "doc_bibkeys": doc_bibkeys,
        "doc_citations": doc_citations,
        "doi_bibkeys": {},
    }


def _cites_edges(kg, src_id: str) -> list[dict]:
    return [
        {**kg._backend.G.nodes[v], "id": v}
        for _, v, d in kg._backend.G.out_edges(src_id, data=True)
        if d.get("kind") == "CITES"
    ]


def test_cites_edges_include_external_cited_targets():
    """Citations to works outside the corpus must land on ``cited`` nodes."""
    citing = _doc(
        "citing_1", "Citing Paper",
        citations=[
            CitationEntry(ord=0, raw_text="Smith et al. 2019.", doi="10.1/ext"),
            CitationEntry(ord=1, raw_text="Jones 2018.", doi="10.2/ext2"),
        ],
    )
    docs = [citing]
    doc_bibkeys = {"citing_1": "citing_1"}
    external = {
        "ref_2019_Smith": {
            "bibkey": "ref_2019_Smith", "kind": "reference",
            "title": "External Work A", "year": 2019, "doi": "10.1/ext",
            "authors": ["J. Smith"],
        },
        "ref_2018_Jones": {
            "bibkey": "ref_2018_Jones", "kind": "reference",
            "title": "External Work B", "year": 2018, "doi": "10.2/ext2",
            "authors": ["J. Jones"],
        },
    }
    index = _citation_index(
        docs,
        doc_bibkeys=doc_bibkeys,
        doc_citations={"citing_1": ["ref_2019_Smith", "ref_2018_Jones"]},
        external_entries=external,
    )

    kg = build_knowledge_graph(docs, [], vectors=None, citation_index=index)
    g = kg._backend.G

    # Both external nodes exist as cited source nodes.
    for bk in ("ref_2019_Smith", "ref_2018_Jones"):
        assert bk in g.nodes
        assert g.nodes[bk].get("kind") == "cited"

    targets = [v for _, v, d in g.out_edges("citing_1", data=True) if d.get("kind") == "CITES"]
    assert "ref_2019_Smith" in targets
    assert "ref_2018_Jones" in targets

    # No cited node should be isolated when at least one corpus paper cites it.
    for bk in ("ref_2019_Smith", "ref_2018_Jones"):
        assert g.in_edges(bk), f"cited node {bk} has no incoming CITES edge"


def test_cites_total_matches_per_doc_citation_count():
    """Total CITES edges approximately equal the sum of per-doc citations."""
    a = _doc("corpus_a", "Corpus A")
    b = _doc("corpus_b", "Corpus B")
    citing = _doc(
        "corpus_c", "Citing",
        citations=[
            CitationEntry(ord=0, raw_text="Corpus A 2020.", doi="10.0/a"),
            CitationEntry(ord=1, raw_text="External 2021.", doi="10.9/x"),
        ],
        cites=["corpus_a"],
    )
    docs = [a, b, citing]
    doc_bibkeys = {"corpus_a": "corpus_a", "corpus_b": "corpus_b", "corpus_c": "corpus_c"}
    external = {
        "ref_ext_2021": {
            "bibkey": "ref_ext_2021", "kind": "reference",
            "title": "External 2021", "year": 2021, "doi": "10.9/x",
            "authors": ["X. Author"],
        },
    }
    index = _citation_index(
        docs,
        doc_bibkeys=doc_bibkeys,
        doc_citations={"corpus_c": ["corpus_a", "ref_ext_2021"]},
        external_entries=external,
    )

    kg = build_knowledge_graph(docs, [], vectors=None, citation_index=index)
    g = kg._backend.G

    cites_edges = [
        (u, v) for u, v, d in g.edges(data=True) if d.get("kind") == "CITES"
    ]
    # Two citations in total, one to corpus, one to cited.
    assert len(cites_edges) == 2
    kinds = [g.nodes[v].get("kind") for _, v in cites_edges]
    assert sorted(kinds) == ["cited", "corpus"]


def test_cites_edges_when_bibkey_differs_from_doc_id():
    """Corpus docs whose bibkey != doc.id must still receive CITES edges
    on their corpus node, not on a shadow cited node."""
    # Realistic: doc.id has weird chars that sanitize changes.
    doc_a = _doc("[2020 X] Paper A_abcdef", "Paper A", doi="10.0/a")
    doc_b = _doc(
        "[2020 Y] Paper B_ghijkl", "Paper B",
        citations=[
            CitationEntry(ord=0, raw_text="Paper A 2020.", doi="10.0/a"),
        ],
    )
    docs = [doc_a, doc_b]
    doc_bibkeys = {
        "[2020 X] Paper A_abcdef": "2020_X_Paper_A_abcdef",
        "[2020 Y] Paper B_ghijkl": "2020_Y_Paper_B_ghijkl",
    }
    index = _citation_index(
        docs,
        doc_bibkeys=doc_bibkeys,
        doc_citations={
            "[2020 Y] Paper B_ghijkl": ["2020_X_Paper_A_abcdef"],
        },
        external_entries={},
    )

    kg = build_knowledge_graph(docs, [], vectors=None, citation_index=index)
    g = kg._backend.G

    # No shadow 'cited' node for the corpus doc.
    assert "2020_X_Paper_A_abcdef" not in g.nodes

    # CITES edge lands on the corpus node.
    targets = [
        v for _, v, d in g.out_edges("[2020 Y] Paper B_ghijkl", data=True)
        if d.get("kind") == "CITES"
    ]
    assert targets == ["[2020 X] Paper A_abcdef"]
    assert g.nodes["[2020 X] Paper A_abcdef"].get("kind") == "corpus"


def test_pagerank_uses_only_corpus_to_corpus_cites():
    """PageRank must be strict corpus-to-corpus citation centrality.

    Edges to ``cited`` (external) source nodes must NOT participate; only
    ``corpus -> corpus`` CITES contribute. See
    docs/distill-test-readiness.md, Issue 1.
    """
    a = _doc("corpus_a", "Corpus A", doi="10.0/a")
    b = _doc(
        "corpus_b", "Corpus B",
        citations=[
            CitationEntry(ord=0, raw_text="Corpus A 2020.", doi="10.0/a"),
            CitationEntry(ord=1, raw_text="External 2021.", doi="10.9/x"),
        ],
        cites=["corpus_a"],
    )
    docs = [a, b]
    doc_bibkeys = {"corpus_a": "corpus_a", "corpus_b": "corpus_b"}
    external = {
        "ref_ext_2021": {
            "bibkey": "ref_ext_2021", "kind": "reference",
            "title": "External 2021", "year": 2021, "doi": "10.9/x",
            "authors": ["X. Author"],
        },
    }
    index = _citation_index(
        docs,
        doc_bibkeys=doc_bibkeys,
        doc_citations={"corpus_b": ["corpus_a", "ref_ext_2021"]},
        external_entries=external,
    )

    kg = build_knowledge_graph(docs, [], vectors=None, citation_index=index)
    g = kg._backend.G

    # External cited node MUST exist but MUST NOT have a pagerank score.
    assert g.nodes["ref_ext_2021"].get("kind") == "cited"
    assert "pagerank" not in g.nodes["ref_ext_2021"]

    # Both corpus nodes MUST have pageranks summing to 1.0 (within fp tol).
    pr_a = g.nodes["corpus_a"].get("pagerank")
    pr_b = g.nodes["corpus_b"].get("pagerank")
    assert pr_a is not None and pr_b is not None
    assert abs(pr_a + pr_b - 1.0) < 1e-3
    # The cited target receives the rank flow, not corpus_b.
    assert pr_a > pr_b


def test_cited_nodes_are_not_isolated_when_cited():
    """No cited node should be isolated if at least one corpus paper cites it."""
    citing = _doc(
        "c1", "Citing",
        citations=[CitationEntry(ord=0, raw_text="X 2019.", doi="10.0/x")],
    )
    docs = [citing]
    doc_bibkeys = {"c1": "c1"}
    external = {
        "ref_X_2019": {
            "bibkey": "ref_X_2019", "kind": "reference",
            "title": "Work X", "year": 2019, "doi": "10.0/x",
            "authors": ["X"],
        },
    }
    index = _citation_index(
        docs, doc_bibkeys=doc_bibkeys,
        doc_citations={"c1": ["ref_X_2019"]},
        external_entries=external,
    )
    kg = build_knowledge_graph(docs, [], vectors=None, citation_index=index)
    g = kg._backend.G

    touched = set()
    for u, v in g.edges():
        touched.add(u)
        touched.add(v)
    cited_ids = [n for n, data in g.nodes(data=True) if data.get("kind") == "cited"]
    assert cited_ids == ["ref_X_2019"]
    assert all(cid in touched for cid in cited_ids)
