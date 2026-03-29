"""Retrieve and assemble context from the knowledge base for LLM generation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlmodel import select

from scholarforge.graph.metrics import GraphMetrics
from scholarforge.store.db import get_session
from scholarforge.store.embeddings import _get_collection, _get_model
from scholarforge.store.models import Chunk, Paper


@dataclass
class RetrievedContext:
    """Context assembled for a generation or chat request."""

    papers: list[Paper] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    total_tokens: int = 0
    graph_metrics: GraphMetrics | None = None

    def as_text(self) -> str:
        """Format context for LLM prompt."""
        sections: list[str] = []
        # Group chunks by paper
        paper_map: dict[str, Paper] = {p.id: p for p in self.papers}
        chunks_by_paper: dict[str, list[Chunk]] = {}
        for chunk in self.chunks:
            chunks_by_paper.setdefault(chunk.paper_id, []).append(chunk)

        for paper_id, paper_chunks in chunks_by_paper.items():
            paper = paper_map.get(paper_id)
            if not paper:
                continue
            authors = json.loads(paper.authors) if paper.authors else []
            header = f"### {paper.title} ({', '.join(authors[:3])}, {paper.year or '?'})"
            body = "\n\n".join(c.content for c in paper_chunks)
            sections.append(f"{header}\n\n{body}")

        return "\n\n---\n\n".join(sections)

    def paper_summaries(self) -> str:
        """Short summaries of all papers for planning."""
        lines = []
        for p in self.papers:
            authors = json.loads(p.authors) if p.authors else []
            first = authors[0].split()[-1] if authors else "Unknown"
            abstract = (p.abstract or "")[:200]
            lines.append(f"- {first} {p.year}: {p.title}\n  {abstract}")
        return "\n".join(lines)


def retrieve_for_query(
    query: str,
    max_papers: int = 20,
    max_tokens: int = 12000,
) -> RetrievedContext:
    """Retrieve relevant papers and chunks for a text query.

    Uses embedding similarity to find papers, then pulls chunks
    up to the token budget.
    """
    model = _get_model()
    collection = _get_collection()

    query_embedding = model.encode([query])[0]

    # Query ChromaDB for similar paper abstracts
    results = collection.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=min(max_papers, collection.count()),
        include=["distances"],
    )

    paper_ids = results["ids"][0] if results["ids"] else []

    if not paper_ids:
        return RetrievedContext()

    # Load papers and chunks from DB
    with get_session() as session:
        papers = [session.get(Paper, pid) for pid in paper_ids]
        papers = [p for p in papers if p is not None]

        # Get chunks for these papers, ordered by relevance
        all_chunks: list[Chunk] = []
        for pid in paper_ids:
            chunks = session.exec(
                select(Chunk).where(Chunk.paper_id == pid).order_by(Chunk.chunk_index)
            ).all()
            all_chunks.extend(chunks)

    # Budget: pack chunks up to token limit
    selected_chunks: list[Chunk] = []
    total = 0
    for chunk in all_chunks:
        if total + chunk.token_count > max_tokens:
            continue
        selected_chunks.append(chunk)
        total += chunk.token_count

    return RetrievedContext(
        papers=papers,
        chunks=selected_chunks,
        total_tokens=total,
    )


def retrieve_all_papers(include_metrics: bool = True) -> RetrievedContext:
    """Load all papers with their abstracts (for review-style generation)."""
    from scholarforge.graph.metrics import compute_metrics

    with get_session() as session:
        papers = session.exec(select(Paper)).all()
        # For review papers, we want abstracts + key chunks, not all chunks
        chunks: list[Chunk] = []
        for paper in papers:
            paper_chunks = session.exec(
                select(Chunk).where(Chunk.paper_id == paper.id).order_by(Chunk.chunk_index)
            ).all()
            # Take first ~3 chunks per paper (abstract + intro + methods overview)
            chunks.extend(paper_chunks[:3])

    total = sum(c.token_count for c in chunks)
    metrics = compute_metrics() if include_metrics else None
    return RetrievedContext(papers=papers, chunks=chunks, total_tokens=total, graph_metrics=metrics)
