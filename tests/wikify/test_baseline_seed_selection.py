"""Regression tests for the abstract-first baseline's seed selection.

Validates the explicit greedy submodular objective described in
``docs/distill-test-readiness.md``: ``0.7 * pr_norm(d) + 0.3 *
coverage_gain(d | S)`` over corpus-citation PageRank and mean-pooled
document embeddings.

The seed-selection logic is also the canonical implementation for the
optional seeded-bootstrap helper in ``distill/seed.py`` — keeping it
in one place is what lets us turn seeded bootstrap on later as a side
experiment without forking the math.
"""

from __future__ import annotations

from typing import Iterable

import networkx as nx
import numpy as np
import pytest

from wikify.corpus.graph import CHUNK, SOURCE, KnowledgeGraph, NetworkXBackend
from wikify.corpus.seed import (
    doc_embeddings,
    greedy_seed_select,
    pagerank_normalised,
    select_seeded_bootstrap,
)
from wikify.corpus.vectors import VectorStore
from wikify.models import Chunk


def _kg(
    doc_ids: Iterable[str],
    pagerank: dict[str, float],
    *,
    abstract_chunks: dict[str, str] | None = None,
) -> KnowledgeGraph:
    """Build a small KG with corpus sources and (optionally) one
    canonical abstract chunk per doc — the post-tagger invariant.
    """
    g = nx.MultiDiGraph()
    abstract_chunks = abstract_chunks or {}
    for did in doc_ids:
        g.add_node(did, type=SOURCE, kind="corpus", pagerank=pagerank.get(did, 0.0))
        cid = abstract_chunks.get(did)
        if cid:
            g.add_node(cid, type=CHUNK, source_id=did, ord=0,
                       section_type="abstract", is_boilerplate=False)
            g.add_edge(did, cid, kind="CONTAINS_CHUNK")
    backend = NetworkXBackend(G=g)
    backend.rebuild_indexes()
    return KnowledgeGraph(backend=backend)


def _chunk(cid: str, doc_id: str, ord_: int, *, section_type: str = "abstract") -> Chunk:
    return Chunk(
        id=cid,
        doc_id=doc_id,
        ord=ord_,
        text=f"text-{cid}",
        char_span=(0, 10),
        section_path=["1. Introduction"],
        section_type=section_type,
    )


def _normed(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms > 0, norms, 1.0)
    return (matrix / norms).astype(np.float32)


def test_pagerank_normalised_rescales_to_unit_interval():
    kg = _kg(["d1", "d2", "d3"], {"d1": 0.1, "d2": 0.4, "d3": 0.5})
    pr = pagerank_normalised(kg, ["d1", "d2", "d3"])
    assert pr[0] == pytest.approx(0.0)
    assert pr[2] == pytest.approx(1.0)
    assert 0.0 < pr[1] < 1.0


def test_pagerank_normalised_flat_falls_back_to_half():
    kg = _kg(["d1", "d2"], {"d1": 0.0, "d2": 0.0})
    pr = pagerank_normalised(kg, ["d1", "d2"])
    assert all(v == pytest.approx(0.5) for v in pr.tolist())


def test_doc_embeddings_skip_captions_and_references():
    chunks = [
        _chunk("d1#c0", "d1", 0, section_type="abstract"),
        _chunk("d1#c1", "d1", 1, section_type="results"),
        _chunk("d1#c2", "d1", 2, section_type="references"),
        # Caption chunk -- must be excluded from doc embedding.
        Chunk(
            id="d1#cap",
            doc_id="d1",
            ord=99,
            text="Fig. 1 caption",
            char_span=(0, 10),
            section_path=["__image__", "Fig. 1"],
            section_type="body",
        ),
    ]
    matrix = _normed(
        np.array(
            [
                [1.0, 0.0],   # abstract
                [0.0, 1.0],   # results
                [1.0, 1.0],   # references — excluded
                [-1.0, 0.0],  # caption — excluded
            ],
            dtype=np.float32,
        )
    )
    vs = VectorStore(ids=["d1#c0", "d1#c1", "d1#c2", "d1#cap"], matrix=matrix)
    embeds, doc_order = doc_embeddings(chunks, vs)
    assert doc_order == ["d1"]
    # Mean of the two usable rows, then unit-normalised.
    expected = _normed((matrix[0] + matrix[1])[None, :])
    assert np.allclose(embeds, expected, atol=1e-6)


def test_greedy_seed_select_prefers_pagerank_when_corpus_is_homogeneous():
    """When all docs have identical embeddings, coverage_gain is 0 and the
    objective collapses to PageRank ranking."""
    doc_order = ["d1", "d2", "d3"]
    embeds = _normed(np.ones((3, 4), dtype=np.float32))
    pr_norm = np.array([0.1, 0.9, 0.4], dtype=np.float32)
    out = greedy_seed_select(
        doc_order=doc_order,
        doc_embeddings=embeds,
        pr_norm=pr_norm,
        max_seeds=2,
    )
    # The first pick is the highest PR doc. Once added, all docs have
    # max_sim_to_S = 1, coverage_gain stays zero, so the second pick is
    # the next-highest PR doc.
    assert out[0] == "d2"
    assert out[1] == "d3"


def test_greedy_seed_select_picks_diverse_docs_when_pagerank_is_flat():
    """When PR is flat, the coverage term decides — picks should span the
    embedding space rather than cluster on one side."""
    doc_order = ["a1", "a2", "b1"]
    embeds = _normed(
        np.array(
            [
                [1.0, 0.0],   # a1 cluster
                [0.99, 0.01], # a2 cluster (near a1)
                [0.0, 1.0],   # b1 — far from a-cluster
            ],
            dtype=np.float32,
        )
    )
    pr_norm = np.array([0.5, 0.5, 0.5], dtype=np.float32)
    out = greedy_seed_select(
        doc_order=doc_order,
        doc_embeddings=embeds,
        pr_norm=pr_norm,
        max_seeds=2,
    )
    # First pick: tied (everyone has identical PR + identical coverage_gain
    # at S = empty); ties resolved by argmax which returns the first index.
    # Second pick MUST be the diverse one (b1), not the near-duplicate.
    assert out[1] == "b1"


def test_greedy_seed_select_returns_at_most_max_seeds():
    """``max_seeds`` is the only cap; no token-cost calibration in play."""
    doc_order = [f"d{i}" for i in range(10)]
    embeds = _normed(np.eye(10, dtype=np.float32))
    pr_norm = np.linspace(0.0, 1.0, 10, dtype=np.float32)
    out = greedy_seed_select(
        doc_order=doc_order,
        doc_embeddings=embeds,
        pr_norm=pr_norm,
        max_seeds=3,
    )
    assert len(out) == 3


def test_greedy_seed_select_clamps_max_seeds_to_doc_count():
    """Asking for more seeds than docs returns the whole corpus."""
    doc_order = ["d1", "d2"]
    embeds = _normed(np.eye(2, dtype=np.float32))
    pr_norm = np.array([0.4, 0.6], dtype=np.float32)
    out = greedy_seed_select(
        doc_order=doc_order,
        doc_embeddings=embeds,
        pr_norm=pr_norm,
        max_seeds=99,
    )
    assert sorted(out) == ["d1", "d2"]


def _long_chunk(cid, doc_id, ord_, *, section_type="body", n_chars=400, n_words=None):
    """Build a chunk with controlled char and word counts.

    ``n_words`` (when provided) overrides ``n_chars``: produces a chunk
    of exactly ``n_words`` short space-separated tokens. Use this when
    the test cares about the abstract-tier word-count floor.
    """
    if n_words is not None:
        text = " ".join(["abc"] * n_words)
        n_chars = len(text)
    else:
        text = "x" * n_chars
    return Chunk(
        id=cid,
        doc_id=doc_id,
        ord=ord_,
        text=text,
        char_span=(0, n_chars),
        section_path=["1. Introduction"],
        section_type=section_type,
    )


# abstract-picker tests live in test_abstract_tagger.py now (the
# canonical implementation has moved to ingest-time tagging).
# This file keeps only the tests for greedy seed selection itself
# plus an end-to-end check on select_seeded_bootstrap.


def test_select_seeded_bootstrap_end_to_end_is_deterministic():
    """select_seeded_bootstrap returns each seed doc's canonical
    abstract chunk via the fluent KG API. The KG is pre-tagged
    (one ``section_type='abstract'`` chunk per doc) — that's the
    post-ingest invariant the picker depends on."""
    chunks = [
        _long_chunk("d1#c0", "d1", 0, section_type="abstract", n_words=120),
        _long_chunk("d2#c0", "d2", 0, section_type="abstract", n_words=120),
        _long_chunk("d3#c0", "d3", 0, section_type="abstract", n_words=120),
    ]
    matrix = _normed(np.eye(3, dtype=np.float32))
    vs = VectorStore(ids=["d1#c0", "d2#c0", "d3#c0"], matrix=matrix)
    kg = _kg(
        ["d1", "d2", "d3"],
        {"d1": 0.1, "d2": 0.6, "d3": 0.3},
        abstract_chunks={"d1": "d1#c0", "d2": "d2#c0", "d3": "d3#c0"},
    )
    out_a = select_seeded_bootstrap(
        chunks=chunks, vectors=vs, kg=kg, max_seeds=2,
    )
    out_b = select_seeded_bootstrap(
        chunks=chunks, vectors=vs, kg=kg, max_seeds=2,
    )
    assert out_a == out_b
    assert len(out_a) == 2
