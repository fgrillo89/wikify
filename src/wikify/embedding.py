"""Shared text embedder used by ingest, eval, and query.

Configuration (env vars, checked at ingest time):

- ``WIKIFY_EMBEDDER``: backend name. ``fastembed`` (default) or ``hash``.
- ``WIKIFY_EMBED_MODEL``: HuggingFace model name for the fastembed backend.
  Default: ``sentence-transformers/all-MiniLM-L6-v2`` (384-d, ONNX).

Backends:

- ``fastembed``: ONNX-served sentence-transformer. ~75 chunks/sec on
  commodity CPU. No PyTorch dependency. Model is configurable.
- ``hash``: deterministic hashed bag-of-words projection. Offline, no
  model dependency, adequate for CI/smoke. 128-d. Ignores model setting.

Returns row-unit-norm float32 ``np.ndarray`` with shape ``(len(texts), dim)``.

Use ``embedder_for(backend, model)`` when you need an *explicit* embedder
(no env var dependency) --- eval, query, and preload call this to
reconstruct the same embedder that ingest used, based on ``vectors.meta.json``.
"""

import hashlib
import os
import re
from collections.abc import Callable, Sequence

import numpy as np

EMBED_DIM = 128
HASH_DIM = 128
# Fastembed uses the fully-qualified HuggingFace name for the model.
FE_MODEL_DEFAULT = "sentence-transformers/all-MiniLM-L6-v2"
FE_DIM = 384

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]+")

_fe_model = None  # immutable module-level cache for fastembed
_fe_model_id: str | None = None


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


def _onnx_providers() -> list[str] | None:
    """Return GPU-accelerated ONNX providers if available, else None (default)."""
    try:
        import onnxruntime as ort

        available = ort.get_available_providers()
        if "CUDAExecutionProvider" in available:
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        if "DmlExecutionProvider" in available:
            return ["DmlExecutionProvider", "CPUExecutionProvider"]
    except ImportError:
        pass
    return None


def _load_fe(model: str | None) -> None:
    """Lazy-load the fastembed TextEmbedding model.

    Cached as a module-level handle so repeated calls within a process
    don't re-initialise. The first call downloads the ONNX model into
    fastembed's cache directory; subsequent calls are instant.

    Automatically uses GPU (CUDA or DirectML) when available.
    """
    global _fe_model, _fe_model_id
    name = model or FE_MODEL_DEFAULT
    if _fe_model is not None and _fe_model_id == name:
        return
    from fastembed import TextEmbedding

    providers = _onnx_providers()
    kwargs: dict = {"model_name": name}
    if providers:
        kwargs["providers"] = providers
    _fe_model = TextEmbedding(**kwargs)
    _fe_model_id = name


def _fe_embed_with(model: str | None, texts: Sequence[str]) -> np.ndarray:
    _load_fe(model)
    assert _fe_model is not None, "_load_fe must initialise _fe_model"
    if not texts:
        dim = getattr(_fe_model, "embedding_size", FE_DIM) or FE_DIM
        return np.zeros((0, dim), dtype=np.float32)
    # ``embed`` returns a generator of np.ndarray rows; materialise once.
    arr = np.asarray(list(_fe_model.embed(list(texts))), dtype=np.float32)
    return arr


def embed_texts(texts: Sequence[str]) -> np.ndarray:
    """Embed texts to row-unit-norm float32 vectors. Backend via env vars.

    Reads ``WIKIFY_EMBEDDER`` (backend) and ``WIKIFY_EMBED_MODEL`` (model).
    Eval/query should prefer ``embedder_for(meta.backend, meta.model)``.
    """
    backend = os.environ.get("WIKIFY_EMBEDDER", "fastembed").lower()
    if backend == "hash":
        return _hash_embed(texts)
    model = os.environ.get("WIKIFY_EMBED_MODEL") or None
    return _fe_embed_with(model, texts)


def embedder_for(backend: str, model: str | None = None) -> Callable[[Sequence[str]], np.ndarray]:
    """Return an explicit embed callable for the named backend.

    Does not consult ``WIKIFY_EMBEDDER``. Caller owns the choice.
    Recognised values: ``"fastembed"`` (default for any non-hash code
    path) and ``"hash"``. The legacy ``"sentence_transformers"`` value
    is silently aliased to ``"fastembed"`` so old ``vectors.meta.json``
    files (which used to record this string) still load with the
    drop-in ONNX backend on the same model + same dimensionality.
    """
    b = (backend or "").lower()
    if b == "hash":
        return _hash_embed
    if b in ("fastembed", "sentence_transformers"):

        def _call_fe(texts: Sequence[str]) -> np.ndarray:
            return _fe_embed_with(model, texts)

        return _call_fe
    raise ValueError(f"unknown embedder backend: {backend!r}")


def current_backend() -> dict[str, str | int | None]:
    """Inspect the env-var-driven backend (what ``embed_texts`` will use)."""
    backend = os.environ.get("WIKIFY_EMBEDDER", "fastembed").lower()
    if backend == "hash":
        return {"backend": "hash", "dim": HASH_DIM, "model": None}
    model = os.environ.get("WIKIFY_EMBED_MODEL") or FE_MODEL_DEFAULT
    return {"backend": "fastembed", "dim": FE_DIM, "model": model}
