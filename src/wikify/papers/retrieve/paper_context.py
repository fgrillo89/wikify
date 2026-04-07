"""Paper-writing-specific retrieval helpers.

These build on the corpus-level ``RetrievedContext`` from
``wikify.core.retrieve.context`` and add the section-shaped, deep-read,
and corpus-wide bundle types that the paper-generation pipeline needs.
The wiki layer should not import from this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlmodel import select

from wikify.core.retrieve.context import RetrievedContext
from wikify.core.store.db import get_session
from wikify.core.store.models import Chunk, Paper


@dataclass
class SectionContext:
    """Context tailored for one section of a generated paper."""

    section_heading: str = ""
    chunks: list[Chunk] = field(default_factory=list)
    synthesis_notes: str = ""  # LLM-generated synthesis (for agent strategies)
    token_count: int = 0

    def as_text(self, paper_map: dict[str, Paper] | None = None) -> str:
        """Format section-specific context for the LLM prompt."""

        parts: list[str] = []
        if self.synthesis_notes:
            parts.append(f"--- Synthesis ---\n{self.synthesis_notes}")

        chunks_by_paper: dict[str, list[Chunk]] = {}
        for c in self.chunks:
            chunks_by_paper.setdefault(c.paper_id, []).append(c)

        if chunks_by_paper:
            parts.append("--- Source excerpts ---")
            for pid, pchunks in chunks_by_paper.items():
                paper = paper_map.get(pid) if paper_map else None
                if paper:
                    marker = paper.display_name()
                    parts.append(f"[REF:{marker}] ({paper.year or '?'})")
                body = "\n\n".join(c.content for c in pchunks)
                parts.append(body)

        return "\n\n".join(parts)


def retrieve_all_papers(
    include_metrics: bool = True,
    deep_read_top_n: int = 3,
) -> RetrievedContext:
    """Load all papers with their summaries (for review-style generation).

    Top N hub papers (by PageRank) get every chunk (deep read); the
    rest get the first ~3 chunks (shallow read).
    """

    from wikify.core.graph.metrics import compute_metrics

    metrics = compute_metrics() if include_metrics else None

    deep_read_ids: set[str] = set()
    if metrics and metrics.hub_papers:
        deep_read_ids = set(metrics.hub_papers[:deep_read_top_n])

    with get_session() as session:
        papers = session.exec(select(Paper)).all()
        chunks: list[Chunk] = []
        for paper in papers:
            paper_chunks = session.exec(
                select(Chunk)
                .where(Chunk.paper_id == paper.id)
                .order_by(Chunk.chunk_index)  # type: ignore[arg-type]
            ).all()
            if paper.id in deep_read_ids:
                chunks.extend(paper_chunks)
            else:
                chunks.extend(paper_chunks[:3])

    total = sum(c.token_count for c in chunks)
    return RetrievedContext(
        papers=papers,
        chunks=chunks,
        total_tokens=total,
        graph_metrics=metrics,
    )


def retrieve_deep(paper_ids: list[str]) -> RetrievedContext:
    """Load every chunk for specific papers (deep read mode).

    Expensive — only use for a small number of papers when the writer
    explicitly asks for full-paper context.
    """

    with get_session() as session:
        papers = [session.get(Paper, pid) for pid in paper_ids]
        papers = [p for p in papers if p is not None]

        chunks: list[Chunk] = []
        for pid in paper_ids:
            paper_chunks = session.exec(
                select(Chunk)
                .where(Chunk.paper_id == pid)
                .order_by(Chunk.chunk_index)  # type: ignore[arg-type]
            ).all()
            chunks.extend(paper_chunks)

    total = sum(c.token_count for c in chunks)
    return RetrievedContext(papers=papers, chunks=chunks, total_tokens=total)


__all__ = ["SectionContext", "retrieve_all_papers", "retrieve_deep"]
