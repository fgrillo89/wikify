"""Shared text embedder used by ingest, eval, and query.

Backend selected by env var ``WIKIFY_SIMPLE_EMBEDDER``:

- ``hash`` (default): deterministic hashed bag-of-words projection. Offline,
  no model dependency, adequate for CI/smoke. 128-d.
- ``sentence_transformers``: lazy-loads ``all-MiniLM-L6-v2`` and caches the
  model in a module-level immutable handle. 384-d.

Returns row-unit-norm float32 ``np.ndarray`` with shape ``(len(texts), dim)``.

Use ``embedder_for(backend, model)`` when you need an *explicit* embedder
(no env var dependency) — eval and query call this to construct the same
embedder that ingest used, based on ``vectors.meta.json``.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable, Sequence

import numpy as np

EMBED_DIM = 128
HASH_DIM = 128
ST_MODEL_DEFAULT = "all-MiniLM-L6-v2"
ST_DIM = 384

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")

_st_model = None  # immutable module-level cache for sentence-transformers
_st_model_id: str | None = None


def _hash_embed(texts: Sequence[str], dim: int = HASH_DIM) -> np.ndarray:
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


def _load_st(model: str | None) -> None:
    global _st_model, _st_model_id
    name = model or ST_MODEL_DEFAULT
    if _st_model is not None and _st_model_id == name:
        return
    from sentence_transformers import SentenceTransformer

    _st_model = SentenceTransformer(name)
    _st_model_id = name


def _st_embed_with(model: str | None, texts: Sequence[str]) -> np.ndarray:
    _load_st(model)
    if not texts:
        dim = int(_st_model.get_sentence_embedding_dimension())
        return np.zeros((0, dim), dtype=np.float32)
    arr = _st_model.encode(list(texts), normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(arr, dtype=np.float32)


def embed_texts(texts: Sequence[str]) -> np.ndarray:
    """Embed texts to row-unit-norm float32 vectors. Backend via env var.

    Kept for back-compat with code paths that don't have a corpus handle.
    Eval/query should prefer ``embedder_for(meta.backend, meta.model)``.
    """
    backend = os.environ.get("WIKIFY_SIMPLE_EMBEDDER", "hash").lower()
    if backend == "sentence_transformers":
        return _st_embed_with(None, texts)
    return _hash_embed(texts)


def embedder_for(backend: str, model: str | None = None) -> Callable[[Sequence[str]], np.ndarray]:
    """Return an explicit embed callable for the named backend.

    Does not consult ``WIKIFY_SIMPLE_EMBEDDER``. Caller owns the choice.
    """
    b = (backend or "").lower()
    if b == "hash":
        return _hash_embed
    if b == "sentence_transformers":

        def _call(texts: Sequence[str]) -> np.ndarray:
            return _st_embed_with(model, texts)

        return _call
    raise ValueError(f"unknown embedder backend: {backend!r}")


def current_backend() -> dict[str, str | int | None]:
    """Inspect the env-var-driven backend (what ``embed_texts`` will use)."""
    backend = os.environ.get("WIKIFY_SIMPLE_EMBEDDER", "hash").lower()
    if backend == "sentence_transformers":
        return {"backend": "sentence_transformers", "dim": ST_DIM, "model": ST_MODEL_DEFAULT}
    return {"backend": "hash", "dim": HASH_DIM, "model": None}
