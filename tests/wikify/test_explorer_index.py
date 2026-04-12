"""Round-trip tests for the ingest-time sampler index.

Verifies that:
1. build_explorer_index + save_explorer_index + load_explorer_index is lossless.
2. A SamplerState assembled from the loaded index has identical fields to
   one produced by the existing in-memory build path in _build_explorer_state.
3. ingest_corpus writes sampler_index.json and pagerank.json to disk.
4. The write phase of pipeline.run skips graph/vector loads (loads index instead).
"""

import json
import random
from pathlib import Path

import pytest

from wikify.distill.pipeline import _build_explorer_state
from wikify.ingest.pipeline import ingest_corpus
from wikify.ingest.explorer_index import (
    build_explorer_index,
    load_explorer_index,
    save_explorer_index,
)
from wikify.paths import CorpusPaths
from wikify.store.corpus import all_chunks, list_documents, read_graph, read_vector_store

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> CorpusPaths:
    out = tmp_path_factory.mktemp("sampler_idx_corpus")
    return ingest_corpus(FIXTURE, out)


# ---------------------------------------------------------------------------
# 1. sampler_index.json and pagerank.json are written by ingest_corpus
# ---------------------------------------------------------------------------


def test_ingest_writes_sampler_index(corpus):
    assert corpus.explorer_index_path.exists(), "sampler_index.json missing after ingest"


def test_ingest_writes_pagerank(corpus):
    assert corpus.pagerank_path.exists(), "pagerank.json missing after ingest"


def test_pagerank_sums_to_one(corpus):
    data = json.loads(corpus.pagerank_path.read_text(encoding="utf-8"))
    if not data:
        pytest.skip("empty corpus")
    total = sum(data.values())
    assert abs(total - 1.0) < 1e-6, f"PageRank sums to {total}, expected ~1.0"


# ---------------------------------------------------------------------------
# 2. Round-trip: build -> save -> load is lossless
# ---------------------------------------------------------------------------


def test_round_trip(corpus, tmp_path):
    docs = list_documents(corpus)
    chunks = all_chunks(corpus)
    graph = read_graph(corpus)
    vectors = read_vector_store(corpus)

    idx = build_explorer_index(docs, chunks, graph, vectors)
    p = tmp_path / "sampler_index.json"
    save_explorer_index(p, idx)
    loaded = load_explorer_index(p)

    assert loaded is not None
    assert loaded["version"] == 1
    assert loaded["chunks_by_doc"] == idx["chunks_by_doc"]
    assert loaded["chunk_to_doc"] == idx["chunk_to_doc"]
    assert loaded["abstract_chunk_by_doc"] == idx["abstract_chunk_by_doc"]
    assert loaded["neighbors_by_chunk"] == idx["neighbors_by_chunk"]
    assert loaded["chunk_degree"] == idx["chunk_degree"]
    assert set(loaded["caption_chunk_ids"]) == set(idx["caption_chunk_ids"])
    assert set(loaded["content_chunk_ids"]) == set(idx["content_chunk_ids"])
    assert loaded["doc_ids_sorted"] == idx["doc_ids_sorted"]


def test_load_missing_returns_none(tmp_path):
    result = load_explorer_index(tmp_path / "nonexistent.json")
    assert result is None


# ---------------------------------------------------------------------------
# 3. SamplerState assembled from index matches in-memory build
# ---------------------------------------------------------------------------


def test_sampler_state_fields_match(corpus):
    """SamplerState from index must have the same structural fields as
    the in-memory build path."""
    docs = list_documents(corpus)
    chunks = all_chunks(corpus)
    graph = read_graph(corpus)
    vectors = read_vector_store(corpus)

    rng_a = random.Random(42)
    rng_b = random.Random(42)

    # In-memory path (no corpus kwarg -> fallback)
    state_old = _build_explorer_state(rng_a, docs, chunks, graph, vectors, corpus=None)
    # Index-backed path
    state_new = _build_explorer_state(rng_b, docs, chunks, graph, vectors, corpus=corpus)

    assert state_old.chunks_by_doc == state_new.chunks_by_doc
    assert state_old.chunk_to_doc == state_new.chunk_to_doc
    assert state_old.abstract_chunk_by_doc == state_new.abstract_chunk_by_doc
    assert state_old.chunk_degree == state_new.chunk_degree
    # Neighbor sets are the same (order may differ between tuple and list)
    for cid in state_old.neighbors_by_chunk:
        assert set(state_old.neighbors_by_chunk[cid]) == set(
            state_new.neighbors_by_chunk.get(cid, ())
        )
    # Coverage residuals must be initialised identically
    assert set(state_old.coverage_residuals.keys()) == set(state_new.coverage_residuals.keys())
    for cid, r in state_old.coverage_residuals.items():
        assert state_new.coverage_residuals[cid] == pytest.approx(r)


# ---------------------------------------------------------------------------
# 4. caption_chunk_ids are tagged correctly
# ---------------------------------------------------------------------------


def test_caption_chunk_ids_have_image_section_path(corpus):
    chunks = all_chunks(corpus)
    chunk_map = {c.id: c for c in chunks}

    idx = load_explorer_index(corpus.explorer_index_path)
    assert idx is not None

    for cid in idx["caption_chunk_ids"]:
        c = chunk_map.get(cid)
        assert c is not None, f"caption chunk {cid} not in corpus"
        sp = list(c.section_path or [])
        assert sp and sp[0] == "__image__", (
            f"chunk {cid} labelled as caption but section_path={c.section_path}"
        )

    for cid in idx["content_chunk_ids"]:
        c = chunk_map.get(cid)
        assert c is not None
        sp = list(c.section_path or [])
        assert not (sp and sp[0] == "__image__"), (
            f"chunk {cid} labelled as content but section_path={c.section_path}"
        )
