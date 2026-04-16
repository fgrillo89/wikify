"""Shared text embedder used by ingest, eval, and query.

Configuration (env vars, checked at ingest time):

- ``WIKIFY_EMBEDDER``: backend name. ``fastembed`` (default) or ``hash``.
- ``WIKIFY_EMBED_MODEL``: HuggingFace model name for the fastembed backend.
  Default: ``sentence-transformers/all-MiniLM-L6-v2`` (384-d, 512-tok, 22M
  params, MTEB ~41 NDCG@10). Long-context alternative: set to
  ``nomic-ai/nomic-embed-text-v1.5-Q`` (768-d, 8192-tok, 137M params,
  MTEB ~49). Nomic is ~20x slower than MiniLM and wants a GPU.
- ``WIKIFY_EMBED_BATCH_SIZE``: override the per-model batch size. Nomic
  defaults to 32 (safe on 8 GB DirectML); MiniLM defaults to 256.

Backends:

- ``fastembed``: ONNX-served sentence-transformer. Model is configurable.
  Long-context models (nomic v1.5) require task prefixes: ``"search_document: "``
  on passages and ``"search_query: "`` on queries. ``embed_passages`` and
  ``embed_queries`` handle that transparently based on ``_MODEL_CONFIGS``.
- ``hash``: deterministic hashed bag-of-words projection. Offline, no
  model dependency, adequate for CI/smoke. 128-d. Ignores model setting.

Returns row-unit-norm float32 ``np.ndarray`` with shape ``(len(texts), dim)``.

Use ``embedder_for(backend, model, mode=...)`` when you need an *explicit*
embedder (no env var dependency) --- eval, query, and preload call this to
reconstruct the same embedder that ingest used, based on ``vectors.meta.json``.
"""

import hashlib
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

HASH_DIM = 128
# Fastembed uses the fully-qualified HuggingFace name for the model.
# MiniLM-L6-v2 is the default: tiny, fast, sufficient quality for small
# corpora. Switch to nomic v1.5-Q when you need the 8192-token window and
# can afford ~20x slower embed on GPU.
FE_MODEL_DEFAULT = "sentence-transformers/all-MiniLM-L6-v2"


@dataclass(frozen=True)
class ModelConfig:
    """Static per-model metadata: dimensionality, window, task prefixes.

    ``batch_size`` is the safe default for this model on a commodity laptop
    GPU (6-8 GB VRAM via DirectML / CUDA). Small models (MiniLM) tolerate
    fastembed's 256 default; large long-context models (nomic-base) crash
    DirectML around batch 64 and should stay at 32.
    """

    dim: int
    max_tokens: int
    passage_prefix: str = ""
    query_prefix: str = ""
    batch_size: int = 256


_MODEL_CONFIGS: dict[str, ModelConfig] = {
    "sentence-transformers/all-MiniLM-L6-v2": ModelConfig(
        dim=384, max_tokens=512, batch_size=256,
    ),
    "nomic-ai/nomic-embed-text-v1.5": ModelConfig(
        dim=768,
        max_tokens=8192,
        passage_prefix="search_document: ",
        query_prefix="search_query: ",
        batch_size=32,
    ),
    "nomic-ai/nomic-embed-text-v1.5-Q": ModelConfig(
        dim=768,
        max_tokens=8192,
        passage_prefix="search_document: ",
        query_prefix="search_query: ",
        batch_size=32,
    ),
}

_FALLBACK_CONFIG = ModelConfig(dim=384, max_tokens=512)


def model_config(model: str | None) -> ModelConfig:
    """Return the registered ``ModelConfig`` for ``model`` or a safe fallback."""
    if model is None:
        return _FALLBACK_CONFIG
    return _MODEL_CONFIGS.get(model, _FALLBACK_CONFIG)


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


def _resolve_batch_size(model: str | None, override: int | None) -> int:
    """Batch size precedence: explicit arg > env var > model config default."""
    if override is not None:
        return override
    env = os.environ.get("WIKIFY_EMBED_BATCH_SIZE")
    if env:
        return int(env)
    return model_config(model or FE_MODEL_DEFAULT).batch_size


def _fe_embed_with(
    model: str | None,
    texts: Sequence[str],
    *,
    batch_size: int | None = None,
) -> np.ndarray:
    _load_fe(model)
    assert _fe_model is not None, "_load_fe must initialise _fe_model"
    if not texts:
        cfg = model_config(model or FE_MODEL_DEFAULT)
        dim = getattr(_fe_model, "embedding_size", cfg.dim) or cfg.dim
        return np.zeros((0, dim), dtype=np.float32)
    bs = _resolve_batch_size(model, batch_size)
    arr = np.asarray(
        list(_fe_model.embed(list(texts), batch_size=bs)),
        dtype=np.float32,
    )
    return arr


def _apply_prefix(texts: Sequence[str], prefix: str) -> list[str]:
    if not prefix:
        return list(texts)
    return [f"{prefix}{t}" for t in texts]


def embed_passages(
    texts: Sequence[str], *, batch_size: int | None = None,
) -> np.ndarray:
    """Embed passage / document texts. Prepends the model's passage prefix."""
    backend = os.environ.get("WIKIFY_EMBEDDER", "fastembed").lower()
    if backend == "hash":
        return _hash_embed(texts)
    model = os.environ.get("WIKIFY_EMBED_MODEL") or FE_MODEL_DEFAULT
    prefixed = _apply_prefix(texts, model_config(model).passage_prefix)
    return _fe_embed_with(model, prefixed, batch_size=batch_size)


def embed_queries(
    texts: Sequence[str], *, batch_size: int | None = None,
) -> np.ndarray:
    """Embed query texts. Prepends the model's query prefix."""
    backend = os.environ.get("WIKIFY_EMBEDDER", "fastembed").lower()
    if backend == "hash":
        return _hash_embed(texts)
    model = os.environ.get("WIKIFY_EMBED_MODEL") or FE_MODEL_DEFAULT
    prefixed = _apply_prefix(texts, model_config(model).query_prefix)
    return _fe_embed_with(model, prefixed, batch_size=batch_size)


# Backward-compat alias: historical callers treat ``embed_texts`` as
# passage embedding (ingest, bundle bodies, page embeddings).
embed_texts = embed_passages


def embedder_for(
    backend: str,
    model: str | None = None,
    *,
    mode: str = "passage",
    batch_size: int | None = None,
) -> Callable[[Sequence[str]], np.ndarray]:
    """Return an explicit embed callable for the named backend.

    Does not consult ``WIKIFY_EMBEDDER``. Caller owns the choice.
    Recognised values: ``"fastembed"`` (default for any non-hash code
    path) and ``"hash"``. The legacy ``"sentence_transformers"`` value
    is silently aliased to ``"fastembed"`` so old ``vectors.meta.json``
    files (which used to record this string) still load with the
    drop-in ONNX backend on the same model + same dimensionality.

    ``mode`` selects the task prefix: ``"passage"`` for indexing
    documents, ``"query"`` for search-time queries. ``batch_size``
    overrides the per-model default (see ``ModelConfig``).
    """
    b = (backend or "").lower()
    if b == "hash":
        return _hash_embed
    if b in ("fastembed", "sentence_transformers"):
        cfg = model_config(model or FE_MODEL_DEFAULT)
        prefix = cfg.query_prefix if mode == "query" else cfg.passage_prefix

        def _call_fe(texts: Sequence[str]) -> np.ndarray:
            return _fe_embed_with(
                model, _apply_prefix(texts, prefix), batch_size=batch_size,
            )

        return _call_fe
    raise ValueError(f"unknown embedder backend: {backend!r}")


def current_backend() -> dict[str, str | int | None]:
    """Inspect the env-var-driven backend (what ``embed_passages`` will use)."""
    backend = os.environ.get("WIKIFY_EMBEDDER", "fastembed").lower()
    if backend == "hash":
        return {
            "backend": "hash",
            "dim": HASH_DIM,
            "model": None,
            "max_tokens": 0,
        }
    model = os.environ.get("WIKIFY_EMBED_MODEL") or FE_MODEL_DEFAULT
    cfg = model_config(model)
    return {
        "backend": "fastembed",
        "dim": cfg.dim,
        "model": model,
        "max_tokens": cfg.max_tokens,
    }
