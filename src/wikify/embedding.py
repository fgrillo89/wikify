"""Shared text embedder used by ingest, eval, and query.

Configuration (env vars, checked at ingest time):

- ``WIKIFY_EMBEDDER``: backend name. ``fastembed`` (default) or ``hash``.
- ``WIKIFY_EMBED_MODEL``: HuggingFace model name for the fastembed backend.
  Default: ``jinaai/jina-embeddings-v2-small-en`` (512-d, 8192-tok, 33M
  params, MTEB ~47). The 8192-tok window is what lets the chunker emit
  whole sections (see ``ingest/chunker.py``). Fast alternative:
  ``sentence-transformers/all-MiniLM-L6-v2`` (384-d, 512-tok, 22M, MTEB
  ~41) — ~5x faster but the chunker falls back to paragraph splitting.
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
reconstruct the same embedder that ingest used, based on the active
``embedding_spaces`` row in ``wikify.db``.
"""

import hashlib
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

HASH_DIM = 128
# Fastembed uses the fully-qualified HuggingFace name for the model.
# jina-v2-small-en is the default: 33M params, 8192-token window, 512-d.
# Long context is the point of section-level chunking; with MiniLM
# (512-tok window) the chunker falls back to paragraph-splitting.
# ~5x slower than MiniLM on DirectML RTX 3070 (27s vs 5s on mvp20's 886
# chunks) — acceptable on a laptop, and still a small model class. Swap to
# MiniLM for speed or nomic-Q for higher MTEB via WIKIFY_EMBED_MODEL.
FE_MODEL_DEFAULT = "jinaai/jina-embeddings-v2-small-en"


def active_embed_model_id() -> str:
    """Single source of truth for which embedder is active.

    Resolves ``WIKIFY_EMBED_MODEL`` then ``FE_MODEL_DEFAULT``. Every
    component that needs to reason about the embedder (the embedder
    itself, the chunker's tokenizer, schema fingerprinting) MUST go
    through this so chunk boundaries and embeddings stay in lockstep.
    """
    return os.environ.get("WIKIFY_EMBED_MODEL") or FE_MODEL_DEFAULT


def active_embed_max_tokens() -> int:
    """Token-window cap for the active embedder.

    Reads from ``_MODEL_CONFIGS`` if known; falls back to 2048 (a safe
    inline default for unknown models). The chunker uses this so the
    HybridChunker's max_tokens matches what the embedder will actually
    accept.
    """
    cfg = _MODEL_CONFIGS.get(active_embed_model_id())
    return cfg.max_tokens if cfg else 2048


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
    # BGE-small-v1.5: 33M params, MTEB ~49. Query instruction is the
    # v1.5-recommended prefix; passages use no prefix.
    "BAAI/bge-small-en-v1.5": ModelConfig(
        dim=384,
        max_tokens=512,
        query_prefix="Represent this sentence for searching relevant passages: ",
        batch_size=256,
    ),
    # Jina v2-small: 33M params, native 8192-tok window, 512-d, MTEB ~47.
    # max_tokens capped at 2048 here (not the model's native 8192) because
    # O(n²) attention at 8k × real section-sized inputs exhausts both 8 GB
    # DirectML VRAM (FusedMatMul 80070057 / Mul 8007000E at any batch) and
    # commodity RAM on CPU fallback ("bad allocation"). 2048 tokens still
    # covers typical section chunks; the chunker (ingest/config.py) derives
    # max_chunk_chars from this value (≈5120 chars).
    #
    # batch_size=2 (down from 8 -> 4 -> 2) — progressive tightening. The
    # 8->4 step stopped hangs on mvp20 but Marker-parsed ald_references
    # (Docling-emitted 8 k-char mega-chunks feeding the tokenizer splitter
    # into full 2046-token pieces) still blew past the 8 GB DirectML
    # ceiling, taking out the GPU with DXGI_ERROR_DEVICE_HUNG after
    # hours of CPU fallback. batch=2 halves the per-call activation
    # memory again (one piece = ~2 k tokens × 512 dim × N layers) and
    # stays within VRAM even on dense corpora.
    "jinaai/jina-embeddings-v2-small-en": ModelConfig(
        dim=512, max_tokens=2048, batch_size=2,
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


def _preload_cuda_dlls() -> bool:
    """Make onnxruntime-gpu's CUDA provider loadable from torch's bundled libs.

    onnxruntime-gpu ships against a CUDA major version but not the runtime
    DLLs themselves; the CUDA toolkit supplies ``cudart``/``cublas`` (found
    on PATH) while cuDNN ``cudnn64_9.dll`` is frequently absent system-wide,
    which makes the CUDA EP silently fall back to CPU. Torch (a hard
    dependency, already CUDA-enabled for the docling parse) bundles a
    matching cuDNN 9 under its ``lib`` dir, so we preload cuDNN from there
    and leave the CUDA runtime to the system install. Best-effort: any
    failure leaves the default search path in place and the caller degrades
    to CPU. Returns True iff the preload ran without error.
    """
    try:
        import onnxruntime as ort
        import torch

        torch_lib = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.isdir(torch_lib):
            os.add_dll_directory(torch_lib)
        ort.preload_dlls(cuda=False, cudnn=True, directory=torch_lib)
        return True
    except Exception:  # noqa: BLE001 - best-effort; CPU fallback is fine
        return False


def _onnx_providers() -> list[str] | None:
    """Return GPU-accelerated ONNX providers if available, else None (default).

    ``WIKIFY_EMBED_FORCE_CPU=1`` forces CPU, a safety valve for long-context
    embedders on GPUs that OOM on 8k sequences (DirectML on 8 GB cards
    cannot run jina-v2-small / nomic at full context).

    CUDA is requested only when a CUDA device is actually visible
    (``torch.cuda.is_available()``) so machines with onnxruntime-gpu but no
    usable runtime degrade to CPU instead of tripping the silent-fallback
    health check. cuDNN is preloaded from torch's bundled libs first.
    """
    if os.environ.get("WIKIFY_EMBED_FORCE_CPU", "") == "1":
        return ["CPUExecutionProvider"]
    try:
        import onnxruntime as ort

        available = ort.get_available_providers()
        if (
            "CUDAExecutionProvider" in available
            and _cuda_device_visible()
            and _preload_cuda_dlls()
        ):
            # Only request CUDA once its runtime DLLs (cuDNN) actually
            # loaded. If the preload failed there's no point requesting an
            # EP that will silently drop to CPU and then trip the
            # silent-fallback health check -- fall through to CPU cleanly.
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        if "DmlExecutionProvider" in available:
            return ["DmlExecutionProvider", "CPUExecutionProvider"]
    except (ImportError, AttributeError):
        # A partially-installed onnxruntime imports as an empty namespace
        # package whose ``get_available_providers`` is missing, raising
        # AttributeError rather than ImportError. Degrade to the default
        # provider instead of crashing every query-embedding path.
        pass
    return None


def _cuda_device_visible() -> bool:
    """True if torch sees a usable CUDA device. Best-effort, never raises."""
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        return False


def _load_fe(model: str | None) -> None:
    """Lazy-load the fastembed TextEmbedding model.

    Cached as a module-level handle so repeated calls within a process
    don't re-initialise. The first call downloads the ONNX model into
    fastembed's cache directory; subsequent calls are instant.

    Automatically uses GPU (CUDA or DirectML) when available. The active
    provider is logged, and on GPU-requested sessions we run a tiny
    health-check inference to detect silent CPU fallback before committing
    to a multi-hour embed — DirectML can silently route ops to CPU when
    VRAM is exhausted, which manifests as a multi-hour hang that
    sometimes ends with ``DXGI_ERROR_DEVICE_HUNG``. A 1-token warmup
    that takes > ``_HEALTH_CHECK_SLOW_S`` is treated as evidence of the
    fallback and raises ``RuntimeError`` with a clear remediation hint.
    """
    import sys

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

    # Report which ONNX provider fastembed actually wired up. fastembed
    # exposes the inner onnxruntime session at .model.model; when the
    # requested DirectML/CUDA provider is unavailable or rejected the
    # session silently falls back to CPU, which on long-context models
    # tanks throughput by 100×+.
    active = ""
    try:
        session = _fe_model.model.model  # type: ignore[union-attr]
        active = ", ".join(session.get_providers())
    except Exception:  # noqa: BLE001 - best-effort diagnostic only
        pass
    if active and os.environ.get("WIKIFY_EMBED_VERBOSE") == "1":
        print(f"[embed] model={name} providers={active}", file=sys.stderr)

    _assert_not_silent_cpu_fallback(name, providers)


# A trivial 1-token inference on Jina-v2-small takes ~30 ms warm on
# DirectML and ~500 ms on CPU. Anything over 2 s means we're probably
# on CPU and the caller is about to sink hours into a silent fallback.
# Threshold is intentionally generous so cold-model first-call latency
# (weight load + kernel compile) doesn't trip false positives.
_HEALTH_CHECK_SLOW_S = 2.0


def _assert_not_silent_cpu_fallback(
    model_name: str, providers: list[str] | None,
) -> None:
    """Probe with one short inference; fail loudly if it's absurdly slow.

    Only runs when a GPU provider was requested (CUDA or DirectML is in
    ``providers``) — CPU-intentional setups are left alone. If the probe
    exceeds ``_HEALTH_CHECK_SLOW_S`` we assume the session is executing
    on CPU despite the GPU provider being listed, raise
    ``RuntimeError`` with a remediation hint, and let the caller bail
    out immediately instead of burning the next N hours.
    """
    import sys
    import time

    # Opt out: either no GPU provider was requested, or the user has
    # explicitly asked us not to check (CI benches, embedded test envs).
    if providers is None:
        return
    gpu_requested = any(
        p in ("CUDAExecutionProvider", "DmlExecutionProvider")
        for p in providers
    )
    if not gpu_requested:
        return
    if os.environ.get("WIKIFY_EMBED_SKIP_HEALTH_CHECK", "") == "1":
        return

    try:
        t0 = time.monotonic()
        # Single trivial string; we don't care about the embedding, only
        # how long the inference takes to return.
        _ = list(_fe_model.embed(["health check"], batch_size=1))
        elapsed = time.monotonic() - t0
    except Exception as exc:  # noqa: BLE001 - any probe failure is diagnostic
        print(
            f"[embed] WARN: health-check inference raised {exc!r}; "
            f"proceeding without GPU validation",
            file=sys.stderr,
        )
        return

    if elapsed > _HEALTH_CHECK_SLOW_S:
        alt = (
            "sentence-transformers/all-MiniLM-L6-v2"
            if model_name != "sentence-transformers/all-MiniLM-L6-v2"
            else "hash"
        )
        raise RuntimeError(
            f"Embedder {model_name!r} took {elapsed:.1f}s to infer a "
            f"1-token input despite GPU providers {providers!r} being "
            f"requested — onnxruntime appears to have silently fallen "
            f"back to CPU. This will stall ingest for hours. Set "
            f"WIKIFY_EMBED_MODEL={alt!r} and retry, or set "
            f"WIKIFY_EMBED_SKIP_HEALTH_CHECK=1 to bypass this check.",
        )
    if os.environ.get("WIKIFY_EMBED_VERBOSE") == "1":
        print(
            f"[embed] health check OK ({elapsed*1000:.0f} ms)",
            file=sys.stderr,
        )


def _resolve_batch_size(model: str | None, override: int | None) -> int:
    """Batch size precedence: explicit arg > env var > model config default."""
    if override is not None:
        return override
    env = os.environ.get("WIKIFY_EMBED_BATCH_SIZE")
    if env:
        return int(env)
    return model_config(model or FE_MODEL_DEFAULT).batch_size


def _split_by_tokens(
    text: str, max_tokens: int, tokenizer,
) -> list[str]:
    """Split ``text`` into pieces that each tokenize to <= ``max_tokens``.

    Uses fastembed's exposed HuggingFace-style tokenizer to enforce the
    actual token-length constraint (the thing the embedder OOMs on),
    rather than a char-to-token ratio heuristic that's fragile on
    dense scientific markdown. Reserves 2 tokens per piece for the
    [CLS] / [SEP] special tokens the embedder prepends/appends.
    """
    try:
        encoding = tokenizer.encode(text, add_special_tokens=False)
        ids = list(encoding.ids)
    except Exception:  # noqa: BLE001 - fall back to char-split on any failure
        return _split_by_chars(text, max_tokens)
    if len(ids) <= max_tokens:
        return [text]
    chunk = max_tokens - 2
    pieces: list[str] = []
    for i in range(0, len(ids), chunk):
        pieces.append(tokenizer.decode(ids[i : i + chunk]))
    return [p for p in pieces if p.strip()]


def _split_by_chars(text: str, max_tokens: int) -> list[str]:
    """Fallback when the tokenizer isn't accessible: conservative char split.

    One character can map to at most one token (BPE tokens always consume
    ≥1 char of input), so ``char_cap = max_tokens`` is guaranteed to keep
    every piece within the model's window. Less efficient than token-based
    splitting on typical text (3–4 chars/token usually) but correct without
    tokenizer access.
    """
    if len(text) <= max_tokens:
        return [text]
    return [
        text[i : i + max_tokens] for i in range(0, len(text), max_tokens)
    ]


def _get_tokenizer():
    """Return fastembed's internal HuggingFace tokenizer, or None."""
    try:
        return _fe_model.model.tokenizer  # type: ignore[union-attr]
    except AttributeError:
        return None


def _fe_embed_with(
    model: str | None,
    texts: Sequence[str],
    *,
    batch_size: int | None = None,
) -> np.ndarray:
    _load_fe(model)
    assert _fe_model is not None, "_load_fe must initialise _fe_model"
    cfg = model_config(model or FE_MODEL_DEFAULT)
    if not texts:
        dim = getattr(_fe_model, "embedding_size", cfg.dim) or cfg.dim
        return np.zeros((0, dim), dtype=np.float32)
    # Token-level split-and-mean-pool guard. ``ModelConfig.max_tokens`` is
    # informational for the chunker; it's not enforced by fastembed on the
    # way in. Without this pass, an oversize chunk (8k-char section from a
    # pre-cap ingest, or a dense chunk with low chars/token ratio) gets
    # fed whole to the embedder and OOMs the GPU. We tokenize each input,
    # split any that exceed ``max_tokens`` into consecutive token-windows,
    # embed all pieces in one batched pass, and mean-pool + re-normalise.
    tokenizer = _get_tokenizer()
    pieces: list[str] = []
    owners: list[int] = []
    for i, t in enumerate(texts):
        if tokenizer is not None:
            parts = _split_by_tokens(t, cfg.max_tokens, tokenizer)
        else:
            parts = _split_by_chars(t, cfg.max_tokens)
        for part in parts:
            pieces.append(part)
            owners.append(i)
    bs = _resolve_batch_size(model, batch_size)
    # Eagerly drain the generator but print a batch-progress line every
    # ~2 s so "big embed" runs never look like a hang again. The original
    # one-liner (`list(_fe_model.embed(...))`) produced zero telemetry
    # between submission and completion — a DirectML fallback to CPU
    # was indistinguishable from a deadlock for 2+ hours on a cold
    # long-context model.
    piece_chunks: list[np.ndarray] = []
    total = len(pieces)
    import sys
    import time

    t0 = time.monotonic()
    last_print = t0
    n_done = 0
    emit_progress = total >= bs * 4  # skip chatter on tiny runs
    for emb in _fe_model.embed(pieces, batch_size=bs):
        piece_chunks.append(np.asarray(emb, dtype=np.float32))
        n_done += 1
        if emit_progress:
            now = time.monotonic()
            if n_done == total or (now - last_print) >= 2.0:
                last_print = now
                elapsed = now - t0
                rate = n_done / elapsed if elapsed > 0 else 0.0
                eta = (total - n_done) / rate if rate > 0 else float("inf")
                print(
                    f"[embed] {n_done}/{total} pieces "
                    f"({100.0*n_done/total:.0f}%), "
                    f"{rate:.1f} p/s, eta {eta:.0f}s",
                    file=sys.stderr,
                )
    piece_emb = (
        np.stack(piece_chunks) if piece_chunks
        else np.zeros((0, cfg.dim), dtype=np.float32)
    )
    n = len(texts)
    dim = piece_emb.shape[1]
    out = np.zeros((n, dim), dtype=np.float32)
    counts = np.zeros(n, dtype=np.int32)
    for emb, owner in zip(piece_emb, owners, strict=True):
        out[owner] += emb
        counts[owner] += 1
    out /= counts[:, None].astype(np.float32)
    norms = np.linalg.norm(out, axis=1)
    safe = np.where(norms > 0, norms, 1.0)
    return out / safe[:, None]


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
    path) and ``"hash"``. Older ``vectors.meta.json`` files that record
    ``"sentence_transformers"`` are silently aliased to ``"fastembed"``
    so they keep loading on the drop-in ONNX backend at the same model
    and dimensionality.

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


def probe_embed_stack(*, deep: bool = False) -> dict:
    """Health-probe the runtime query-embedding stack.

    Cheap mode (default): verify onnxruntime imports and exposes its
    provider API. A partially-installed onnxruntime imports as an empty
    namespace package with no ``get_available_providers``, so this catches
    the common silent corruption that leaves stored vectors intact (so
    ``corpus check`` reports ``vectors: True``) while every query embedding
    crashes. ``deep=True`` additionally runs a one-string end-to-end embed.
    """
    out: dict = {"ok": False, "providers": None, "error": None}
    backend = os.environ.get("WIKIFY_EMBEDDER", "fastembed").lower()
    if backend == "hash":
        out.update(ok=True, providers=["hash"])
        return out
    try:
        import onnxruntime as ort

        out["providers"] = list(ort.get_available_providers())
        out["ok"] = True
    except Exception as exc:  # noqa: BLE001 - report any import/attr failure
        out["error"] = f"{type(exc).__name__}: {exc}"
        return out
    if deep:
        try:
            vec = embed_queries(["wikify health check"])
            shape = getattr(vec, "shape", None)
            out["embed_ok"] = bool(shape and shape[0] >= 1 and shape[1] >= 1)
            out["dim"] = int(shape[1]) if shape else None
        except Exception as exc:  # noqa: BLE001
            out["ok"] = False
            out["embed_ok"] = False
            out["error"] = f"{type(exc).__name__}: {exc}"
    return out
