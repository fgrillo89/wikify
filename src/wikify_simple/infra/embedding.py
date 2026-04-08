"""Shared text embedder used by ingest, eval, and query.

Backend selected by env var ``WIKIFY_SIMPLE_EMBEDDER``:

- ``hash`` (default): deterministic hashed bag-of-words projection. Offline,
  no model dependency, adequate for CI/smoke.
- ``sentence_transformers``: lazy-loads ``all-MiniLM-L6-v2`` and caches the
  model in a module-level immutable handle.

Returns row-unit-norm float32 ``np.ndarray`` with shape ``(len(texts), dim)``.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Sequence

import numpy as np

EMBED_DIM = 128
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")

_st_model = None  # immutable module-level cache for sentence-transformers


def _hash_embed(texts: Sequence[str], dim: int = EMBED_DIM) -> np.ndarray:
    out = np.zeros((len(texts), dim), dtype=np.float32)
    for i, text in enumerate(texts):
        for tok in _TOKEN_RE.findall(text.lower()):
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest()[:8], 16)
            out[i, h % dim] += 1.0
            sign = 1.0 if (h >> 4) & 1 else -1.0
            out[i, (h >> 8) % dim] += sign
    norms = np.linalg.norm(out, axis=1)
    safe = np.where(norms > 0, norms, 1.0)
    return out / safe[:, None]


def _st_embed(texts: Sequence[str]) -> np.ndarray:
    global _st_model
    if _st_model is None:
        from sentence_transformers import SentenceTransformer

        _st_model = SentenceTransformer("all-MiniLM-L6-v2")
    if not texts:
        dim = int(_st_model.get_sentence_embedding_dimension())
        return np.zeros((0, dim), dtype=np.float32)
    arr = _st_model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(arr, dtype=np.float32)


def embed_texts(texts: Sequence[str]) -> np.ndarray:
    """Embed texts to row-unit-norm float32 vectors. Backend via env var."""
    backend = os.environ.get("WIKIFY_SIMPLE_EMBEDDER", "hash").lower()
    if backend == "sentence_transformers":
        return _st_embed(texts)
    return _hash_embed(texts)
