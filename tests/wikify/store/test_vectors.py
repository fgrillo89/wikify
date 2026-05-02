"""Vector index tests: encode/decode round-trip, cosine search, fingerprint reuse."""

from __future__ import annotations

import numpy as np

from wikify.corpus.store import Store
from wikify.corpus.store.vectors import decode_vector, encode_vector


def test_encode_decode_round_trip_unit_normalizes():
    rng = np.random.default_rng(0)
    v = rng.normal(size=8).astype("float32") * 17.0
    blob = encode_vector(v)
    out = decode_vector(blob, 8)
    assert abs(np.linalg.norm(out) - 1.0) < 1e-5


def test_vector_search_topk_against_known_neighbors():
    s = Store(":memory:")
    s.upsert_embedding_space("hash384", "hash", "stub", 8)
    rng = np.random.default_rng(1)
    base = rng.normal(size=8).astype("float32")
    items = []
    items.append(("chunk", "c-best", base.copy()))
    for i in range(10):
        items.append(("chunk", f"c{i}", rng.normal(size=8).astype("float32")))
    s.upsert_embeddings("hash384", items)
    vi = s.vector_index("hash384")
    out = vi.search(base, top_k=3)
    assert out[0][0] == "c-best"
    assert abs(out[0][1] - 1.0) < 1e-3


def test_two_embedding_spaces_coexist_no_orphans():
    """Phase-1 fingerprint reuse: switching backend creates a new space row,
    old rows remain queryable, no orphan embeddings rows."""
    s = Store(":memory:")
    s.upsert_embedding_space("hash:dim8", "hash", None, 8)
    s.upsert_embedding_space("fastembed:minilm", "fastembed", "all-MiniLM-L6-v2", 8)
    rng = np.random.default_rng(0)

    def _vecs(prefix: str) -> list[tuple[str, str, np.ndarray]]:
        return [
            ("chunk", f"{prefix}{i}", rng.normal(size=8).astype("float32"))
            for i in range(3)
        ]

    s.upsert_embeddings("hash:dim8", _vecs("a"))
    s.upsert_embeddings("fastembed:minilm", _vecs("b"))

    spaces = sorted(r["space_id"] for r in s.con.execute("SELECT * FROM embedding_spaces"))
    assert spaces == ["fastembed:minilm", "hash:dim8"]
    counts = dict(s.con.execute("SELECT space_id, COUNT(*) FROM embeddings GROUP BY space_id"))
    assert counts == {"hash:dim8": 3, "fastembed:minilm": 3}
