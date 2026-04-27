"""Cached page-body embeddings for a wiki bundle.

The cache lives under the bundle's ``derived/`` directory, NOT under
``wiki/``. Eval is a read-only consumer of ``wiki/``; cached projections
that eval rebuilds belong in ``derived/`` alongside the eval report
and the rendered site.

Invalidation: the cache is stale if any committed page file under
``articles/`` or ``people/`` has been modified more recently than the
cache, OR if ``derived/index.json`` is newer (the commit gate
rebuilds the derived index every time it promotes a page). The page
id list is also encoded in the cache so adding/removing a page is
detected as a mismatch.
"""

from collections.abc import Callable, Sequence
from pathlib import Path

import numpy as np

from .page import Bundle, clean_body

_CACHE_NAME = "_page_embeddings.npz"


def _cache_path(bundle: Bundle) -> Path:
    """Cache path under the ``derived/`` sibling of ``wiki/``.

    The layout is ``<bundle>/wiki/`` and ``<bundle>/derived/`` as
    siblings, so ``bundle.root.parent / "derived"`` resolves the right
    directory.
    """
    return bundle.root.parent / "derived" / _CACHE_NAME


def _max_page_mtime(bundle: Bundle) -> float:
    """Greatest mtime among committed page files. 0.0 if there are none."""
    latest = 0.0
    for sub in ("articles", "people"):
        d = bundle.root / sub
        if not d.is_dir():
            continue
        for f in d.glob("*.md"):
            try:
                m = f.stat().st_mtime
            except OSError:
                continue
            if m > latest:
                latest = m
    derived_index = bundle.root.parent / "derived" / "index.json"
    if derived_index.exists():
        try:
            m = derived_index.stat().st_mtime
            if m > latest:
                latest = m
        except OSError:
            pass
    return latest


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
    cache_path = _cache_path(bundle)

    pages_list = list(pages)
    ids = [p.id for p in pages_list]

    if cache_path.exists():
        try:
            cache_mtime = cache_path.stat().st_mtime
        except OSError:
            cache_mtime = 0.0
        stale = _max_page_mtime(bundle) > cache_mtime
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
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache_path, ids=np.asarray(ids, dtype=object), matrix=matrix)
    except Exception:
        pass
    return ids, matrix
