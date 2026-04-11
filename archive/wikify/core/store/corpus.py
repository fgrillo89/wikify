"""Corpus-level paper / chunk accessors.

These helpers filter the SQL store down to the ingested-corpus subset
(``Paper.origin == CORPUS``) so corpus metrics, embeddings, coverage,
and graph computation never accidentally include generated paper-writing
output. Used by both the wiki and papers boundaries — therefore lives
in ``core/store``, not in ``papers/``.
"""

from __future__ import annotations

from sqlmodel import select

from wikify.core.store.db import get_session
from wikify.core.store.models import Chunk, Paper, PaperOrigin


def get_corpus_paper_ids() -> set[str]:
    """Return paper IDs that belong to the ingested corpus.

    Generated paper-writing output (``Paper.origin == GENERATED``) is
    excluded so it never contaminates corpus metrics like coverage,
    vibe vectors, or strategy ordering.
    """

    with get_session() as session:
        papers = session.exec(
            select(Paper).where(Paper.origin == PaperOrigin.CORPUS)
        ).all()
    return {p.id for p in papers}


def load_corpus_chunks() -> list[Chunk]:
    """Load every chunk that belongs to an ingested corpus paper."""

    corpus_pids = get_corpus_paper_ids()
    with get_session() as session:
        chunks = session.exec(
            select(Chunk).order_by(Chunk.paper_id, Chunk.chunk_index)  # type: ignore[arg-type]
        ).all()
    return [c for c in chunks if c.paper_id in corpus_pids]


__all__ = ["get_corpus_paper_ids", "load_corpus_chunks"]
