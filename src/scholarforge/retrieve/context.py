"""Retrieve and assemble context from the knowledge base for LLM generation."""

from __future__ import annotations

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
        """Format context for LLM prompt.

        Each paper header includes its display_name as a [REF:...] anchor
        so the LLM knows the exact citation marker to use.
        """
        from scholarforge.generate.references import ref_marker

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
            marker = ref_marker(paper)
            authors = paper.parsed_authors
            header = (
                f"### [REF:{marker}] {paper.title} ({', '.join(authors[:3])}, {paper.year or '?'})"
            )
            body = "\n\n".join(c.content for c in paper_chunks)
            sections.append(f"{header}\n\n{body}")

        return "\n\n---\n\n".join(sections)

    def paper_summaries(self) -> str:
        """Short summaries of all papers for planning."""
        lines = []
        for p in self.papers:
            authors = p.parsed_authors
            # Handle "Last, First" and "First Last" formats
            raw = authors[0] if authors else "Unknown"
            first = raw.split(",")[0].strip() if "," in raw else raw.split()[-1]
            summary = (p.summary or "")[:200]
            lines.append(f"- {first} {p.year}: {p.title}\n  {summary}")
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

    # Query ChromaDB for similar paper summaries
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


def retrieve_all_papers(
    include_metrics: bool = True,
    deep_read_top_n: int = 3,
) -> RetrievedContext:
    """Load all papers with their summaries (for review-style generation).

    Top N hub papers (by PageRank) get ALL chunks (deep read).
    Remaining papers get first ~3 chunks (shallow read).
    """
    from scholarforge.graph.metrics import compute_metrics

    metrics = compute_metrics() if include_metrics else None

    # Identify hub papers for deep reading
    deep_read_ids: set[str] = set()
    if metrics and metrics.hub_papers:
        deep_read_ids = set(metrics.hub_papers[:deep_read_top_n])

    with get_session() as session:
        papers = session.exec(select(Paper)).all()
        chunks: list[Chunk] = []
        for paper in papers:
            paper_chunks = session.exec(
                select(Chunk).where(Chunk.paper_id == paper.id).order_by(Chunk.chunk_index)
            ).all()
            if paper.id in deep_read_ids:
                chunks.extend(paper_chunks)  # Deep read for hub papers
            else:
                chunks.extend(paper_chunks[:3])  # Shallow for the rest

    total = sum(c.token_count for c in chunks)
    return RetrievedContext(papers=papers, chunks=chunks, total_tokens=total, graph_metrics=metrics)


def retrieve_deep(paper_ids: list[str]) -> RetrievedContext:
    """Load ALL chunks for specific papers (deep read mode).

    Use this when the user explicitly asks to read full papers.
    This is expensive — only use for a small number of papers.
    """
    with get_session() as session:
        papers = [session.get(Paper, pid) for pid in paper_ids]
        papers = [p for p in papers if p is not None]

        chunks: list[Chunk] = []
        for pid in paper_ids:
            paper_chunks = session.exec(
                select(Chunk).where(Chunk.paper_id == pid).order_by(Chunk.chunk_index)
            ).all()
            chunks.extend(paper_chunks)

    total = sum(c.token_count for c in chunks)
    return RetrievedContext(papers=papers, chunks=chunks, total_tokens=total)
