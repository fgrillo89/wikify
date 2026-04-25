"""Sidecar metadata for ``corpus/vectors.npz``.

Records which embedder backend produced the matrix so that downstream
tools (eval, query) can construct the *exact* matching embedder. The
schema is forward-stable: unknown keys are ignored on read.

Shape::

    {
      "version": 1,
      "backend": "hash" | "fastembed",
      "dim": 384,
      "model": "sentence-transformers/all-MiniLM-L6-v2"  # or null for hash
    }

Legacy corpora may carry ``"sentence_transformers"`` as the backend
string; ``embedder_for`` aliases that to ``"fastembed"`` (same model,
same dimension, drop-in replacement) so old corpora load without
re-embedding.
"""

import json
from dataclasses import dataclass
from pathlib import Path

META_NAME = "vectors.meta.json"


@dataclass(frozen=True)
class VectorsMeta:
    backend: str  # "hash" | "fastembed"
    dim: int
    model: str | None = None


def meta_path_for(vectors_path: Path) -> Path:
    return vectors_path.parent / META_NAME


def write_meta(vectors_path: Path, meta: VectorsMeta) -> Path:
    out = meta_path_for(vectors_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "backend": meta.backend,
        "dim": int(meta.dim),
        "model": meta.model,
    }
    from .chunks import atomic_write_text

    atomic_write_text(out, json.dumps(payload, indent=2))
    return out


def read_meta(vectors_path: Path) -> VectorsMeta | None:
    p = meta_path_for(vectors_path)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return VectorsMeta(
        backend=str(data.get("backend", "hash")),
        dim=int(data.get("dim", 0)),
        model=data.get("model"),
    )
