"""Retrieve and assemble context from the knowledge base for LLM generation.

Tiered retrieval (inspired by ByteRover):
    Tier 0: Exact query cache (hash match)           -- ~0ms
    Tier 1: Jaccard fuzzy cache (>= 0.6 overlap)     -- ~1ms
    Tier 2: BM25 lexical search (if confident)        -- ~100ms
    Tier 3: ChromaDB embedding search (full semantic)  -- ~500ms
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlmodel import select

from wikify.graph.metrics import GraphMetrics
from wikify.store.db import get_session
from wikify.store.embeddings import _get_collection, _get_model
from wikify.store.models import Chunk, Paper

logger = logging.getLogger(__name__)


@dataclass
class SectionContext:
    """Context tailored for a specific section of a generated paper."""

    section_heading: str = ""
    chunks: list[Chunk] = field(default_factory=list)
    synthesis_notes: str = ""  # LLM-generated synthesis (for agent strategies)
    token_count: int = 0

    def as_text(self, paper_map: dict[str, Paper] | None = None) -> str:
        """Format section-specific context for the LLM prompt.

        Args:
            paper_map: If provided, emit [REF:display_name] headers per paper
                       so the writer can cite sources. Backwards-compatible.
        """
        parts: list[str] = []
        if self.synthesis_notes:
            parts.append(f"--- Synthesis ---\n{self.synthesis_notes}")

        # Group chunks by paper
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


@dataclass
class RetrievedContext:
    """Context assembled for a generation or chat request."""

    papers: list[Paper] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    total_tokens: int = 0
    graph_metrics: GraphMetrics | None = None
    section_contexts: dict[str, SectionContext] = field(default_factory=dict)
    strategy_name: str = "flat"

    def as_text(self) -> str:
        """Format context for LLM prompt.

        Each paper header includes its display_name as a [REF:...] anchor
        so the LLM knows the exact citation marker to use.
        """
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
            marker = paper.display_name()
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


def _load_chunks_by_ids(
    chunk_ids: list[str],
    max_tokens: int,
) -> tuple[list[Chunk], list[Paper], int]:
    """Load chunks and their papers from DB, respecting token budget.

    Args:
        chunk_ids: Ordered list of chunk IDs (best first).
        max_tokens: Token budget.

    Returns:
        (selected_chunks, papers, total_tokens)
    """
    if not chunk_ids:
        return [], [], 0

    with get_session() as session:
        db_chunks = session.exec(
            select(Chunk).where(Chunk.id.in_(chunk_ids))  # type: ignore[union-attr]
        ).all()
        chunks_by_id = {c.id: c for c in db_chunks}

    selected: list[Chunk] = []
    total = 0
    paper_ids: set[str] = set()

    for cid in chunk_ids:
        chunk = chunks_by_id.get(cid)
        if not chunk:
            continue
        if total + chunk.token_count > max_tokens:
            continue
        selected.append(chunk)
        total += chunk.token_count
        paper_ids.add(chunk.paper_id)

    with get_session() as session:
        papers = [p for pid in paper_ids if (p := session.get(Paper, pid)) is not None]

    return selected, papers, total


def retrieve_for_query(
    query: str,
    max_papers: int = 20,
    max_tokens: int = 12000,
) -> RetrievedContext:
    """Retrieve relevant papers and chunks for a text query.

    Uses tiered retrieval for speed:
        Tier 0: Exact query cache hit
        Tier 1: Jaccard fuzzy cache hit (>= 0.6 token overlap)
        Tier 2: BM25 lexical search (if confident match)
        Tier 3: ChromaDB embedding search (full semantic)

    Falls through tiers until a confident result is found.
    """
    from wikify.retrieve.bm25 import bm25_is_confident, bm25_search
    from wikify.retrieve.cache import get_query_cache
    from wikify.store.embeddings import query_chunks

    cache = get_query_cache()

    # ── Tier 0/1: Cache lookup ───────────────────────────────────────────
    cached_result, tier = cache.get(query)
    if cached_result is not None:
        logger.debug("retrieve_for_query: cache %s hit for %r", tier, query[:40])
        return cached_result

    # ── Tier 2: BM25 lexical search ──────────────────────────────────────
    max_chunks = max(max_tokens // 150, 20)
    bm25_results = bm25_search(query, n_results=max_chunks)

    if bm25_results and bm25_is_confident(bm25_results):
        chunk_ids = [cid for cid, _ in bm25_results]
        chunks, papers, total = _load_chunks_by_ids(chunk_ids, max_tokens)

        if chunks:
            ctx = RetrievedContext(
                papers=papers,
                chunks=chunks,
                total_tokens=total,
                strategy_name="bm25",
            )
            cache.put(query, ctx)
            logger.info(
                "retrieve_for_query: BM25 confident hit (%d chunks, %d tokens, top=%.2f)",
                len(chunks),
                total,
                bm25_results[0][1],
            )
            return ctx

    # ── Tier 3: ChromaDB embedding search (existing path) ────────────────
    model = _get_model()
    collection = _get_collection()

    query_embedding = model.encode([query])[0]

    # Find relevant papers via summary embeddings
    results = collection.query(
        query_embeddings=[query_embedding.tolist()],
        n_results=min(max_papers, collection.count()),
        include=["distances"],
    )

    paper_ids = results["ids"][0] if results["ids"] else []

    if not paper_ids:
        return RetrievedContext()

    # Load papers
    with get_session() as session:
        papers = [session.get(Paper, pid) for pid in paper_ids]
        papers = [p for p in papers if p is not None]

    # Semantic chunk retrieval within matched papers
    chunk_results = query_chunks(query, n_results=max_chunks, paper_ids=paper_ids)

    if not chunk_results:
        ctx = RetrievedContext(papers=papers, chunks=[], total_tokens=0)
        cache.put(query, ctx)
        return ctx

    # Load chunks by ID, preserving similarity order
    chunk_ids = [cid for cid, _ in chunk_results]
    chunks, _, total = _load_chunks_by_ids(chunk_ids, max_tokens)

    ctx = RetrievedContext(
        papers=papers,
        chunks=chunks,
        total_tokens=total,
    )
    cache.put(query, ctx)

    logger.debug(
        "retrieve_for_query: embedding search -> %d chunks, %d tokens",
        len(chunks),
        total,
    )
    return ctx


def retrieve_all_papers(
    include_metrics: bool = True,
    deep_read_top_n: int = 3,
) -> RetrievedContext:
    """Load all papers with their summaries (for review-style generation).

    Top N hub papers (by PageRank) get ALL chunks (deep read).
    Remaining papers get first ~3 chunks (shallow read).
    """
    from wikify.graph.metrics import compute_metrics

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
