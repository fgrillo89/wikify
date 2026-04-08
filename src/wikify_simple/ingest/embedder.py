"""Backwards-compatible re-export of the shared embedder.

The canonical implementation lives in ``wikify_simple.infra.embedding``.
"""

from __future__ import annotations

from ..infra.embedding import EMBED_DIM, embed_texts

__all__ = ["EMBED_DIM", "embed_texts"]
