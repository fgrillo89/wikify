"""Tiny on-disk vector store: id-to-row mapping + a single .npz matrix.

Deliberately minimal — one numpy file. No vendor lock-in. Embeddings are
unit-norm float32 vectors. Adequate for the corpora wikify targets
(<= 10^4 chunks); swap for lancedb when it stops being adequate.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass
class VectorStore:
    ids: list[str]
    matrix: np.ndarray  # (n, d), unit-norm float32

    def index_of(self, chunk_id: str) -> int:
        return self._lookup[chunk_id]

    def __post_init__(self) -> None:
        self._lookup = {cid: i for i, cid in enumerate(self.ids)}

    def vector(self, chunk_id: str) -> np.ndarray:
        return self.matrix[self._lookup[chunk_id]]

    def cosine_to_all(self, vec: np.ndarray) -> np.ndarray:
        return self.matrix @ vec

    def topk(self, vec: np.ndarray, k: int) -> list[tuple[str, float]]:
        sims = self.cosine_to_all(vec)
        order = np.argsort(-sims)[:k]
        return [(self.ids[i], float(sims[i])) for i in order]


def save_vectors(path: Path, store: VectorStore) -> None:
    import os
    import tempfile

    from .corpus import atomic_write_text

    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic npz: write to temp, then os.replace.
    fd, tmp = tempfile.mkstemp(prefix=".vectors-", suffix=".npz", dir=str(path.parent))
    os.close(fd)
    try:
        np.savez_compressed(tmp, ids=np.array(store.ids), matrix=store.matrix)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    atomic_write_text(path.with_suffix(".ids.json"), json.dumps(store.ids))


def load_vectors(path: Path) -> VectorStore:
    data = np.load(path, allow_pickle=False)
    ids = [str(x) for x in data["ids"].tolist()]
    matrix = data["matrix"].astype(np.float32)
    return VectorStore(ids=ids, matrix=matrix)
