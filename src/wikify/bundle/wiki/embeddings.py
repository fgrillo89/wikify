"""Cached page-body embeddings for a wiki bundle.

The cache is a single ``_page_embeddings.npz`` beside the bundle's
``_index.json``. Invalidation is a simple mtime check against the index.
"""

from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np

from .page import Bundle, clean_body

_CACHE_NAME = "_page_embeddings.npz"


def _bundle_root(bundle: Bundle) -> Path:
    return bundle.root


def load_or_compute(
    bundle: Bundle,
    pages,
    embed: Callable[[Sequence[str]], np.ndarray],
) -> tuple[list[str], np.ndarray]:
    """Return (ids, matrix) of unit-norm page-body embeddings.

    ``pages`` is an iterable of objects with ``.id`` and ``.body_clean``
    (or ``.body_markdown``; we also pass through ``clean_body`` if the
    caller hands us unclean bodies).

    The caller must pass the corpus's own embedder (the one whose backend
    matches ``vectors.meta.json``) — not a generic ``embed_texts``. Eval
    code should construct it via ``infra.embedding.embedder_for(...)``.
    """
    root = _bundle_root(bundle)
    cache_path = root / _CACHE_NAME
    index_path = root / "_index.json"

    pages_list = list(pages)
    ids = [p.id for p in pages_list]

    if cache_path.exists():
        stale = False
        if index_path.exists():
            stale = index_path.stat().st_mtime > cache_path.stat().st_mtime
        if not stale:
            try:
                data = np.load(cache_path, allow_pickle=False)
                cached_ids = list(data["ids"])
                if cached_ids == ids:
                    return cached_ids, np.asarray(data["matrix"], dtype=np.float32)
            except Exception:
                pass  # fall through and recompute

    texts: list[str] = []
    for p in pages_list:
        body = getattr(p, "body_clean", None)
        if body is None:
            body = clean_body(getattr(p, "body_markdown", "") or "")
        texts.append(body)
    matrix = embed(texts) if texts else np.zeros((0, 0), dtype=np.float32)
    try:
        root.mkdir(parents=True, exist_ok=True)
        np.savez(cache_path, ids=np.asarray(ids, dtype=object), matrix=matrix)
    except Exception:
        pass
    return ids, matrix
