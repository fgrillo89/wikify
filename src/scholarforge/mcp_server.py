"""ScholarForge MCP Server — exposes the knowledge base as tools for LLMs.

Implements the Model Context Protocol (MCP) using FastMCP so that Claude Code
or other MCP-compatible clients can query the literature corpus directly.

Launch via:
    scholarforge mcp [--library <name>]
    python -m scholarforge.mcp_server [--library <name>]
"""

from __future__ import annotations

import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "ScholarForge",
    instructions=(
        "ScholarForge knowledge base. Use search_papers to find relevant literature, "
        "get_paper for full details, get_graph_metrics for network analysis, "
        "list_papers/list_topics for browsing, and deep_read for full text retrieval."
    ),
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _paper_to_dict(paper) -> dict:
    """Serialize a Paper SQLModel to a plain dict for JSON output."""
    return {
        "id": paper.id,
        "title": paper.title,
        "authors": paper.parsed_authors,
        "year": paper.year,
        "doi": paper.doi,
        "doc_type": paper.doc_type,
        "abstract": paper.abstract,
        "display_name": paper.display_name(),
        "source_path": paper.source_path,
    }


def _chunk_to_dict(chunk) -> dict:
    """Serialize a Chunk SQLModel to a plain dict."""
    return {
        "id": chunk.id,
        "paper_id": chunk.paper_id,
        "section_path": chunk.section_path,
        "content": chunk.content,
        "token_count": chunk.token_count,
        "chunk_index": chunk.chunk_index,
        "has_citations": chunk.has_citations,
        "has_equations": chunk.has_equations,
    }


# ── Tools ─────────────────────────────────────────────────────────────────────


@mcp.tool()
def search_papers(
    query: str,
    top_k: int = 10,
    max_tokens: int = 8000,
) -> str:
    """Semantic search across the literature corpus using embedding similarity.

    Returns paper metadata and relevant text chunks for the given query.
    Results are ranked by embedding distance (most similar first).

    Args:
        query: Natural language search query.
        top_k: Maximum number of papers to return (default 10).
        max_tokens: Token budget for included text chunks (default 8000).

    Returns:
        JSON string with keys:
            - papers: list of matching paper metadata dicts
            - chunks: list of relevant text chunk dicts
            - total_tokens: approximate token count of included chunks
            - error: present only if something went wrong
    """
    try:
        from scholarforge.retrieve.context import retrieve_for_query

        ctx = retrieve_for_query(query, max_papers=top_k, max_tokens=max_tokens)
        return json.dumps(
            {
                "papers": [_paper_to_dict(p) for p in ctx.papers],
                "chunks": [_chunk_to_dict(c) for c in ctx.chunks],
                "total_tokens": ctx.total_tokens,
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc), "papers": [], "chunks": []})


@mcp.tool()
def get_paper(
    pattern: str,
) -> str:
    """Get full details for a specific paper by title or author name pattern.

    Performs a case-insensitive substring match against title and author fields.
    Returns the best match together with all its text chunks.

    Args:
        pattern: Substring to match in the paper title or author list.

    Returns:
        JSON string with keys:
            - paper: paper metadata dict (or null if not found)
            - chunks: all text chunks for the matched paper
            - match_count: number of papers that matched (first one returned)
            - error: present only if something went wrong
    """
    try:
        from sqlmodel import select

        from scholarforge.store.db import get_session
        from scholarforge.store.models import Chunk, Paper

        lower = pattern.lower()
        with get_session() as session:
            all_papers = session.exec(select(Paper)).all()
            matched = [
                p for p in all_papers if lower in p.title.lower() or lower in p.authors.lower()
            ]
            if not matched:
                return json.dumps({"paper": None, "chunks": [], "match_count": 0})

            paper = matched[0]
            chunks = session.exec(
                select(Chunk).where(Chunk.paper_id == paper.id).order_by(Chunk.chunk_index)
            ).all()

        return json.dumps(
            {
                "paper": _paper_to_dict(paper),
                "chunks": [_chunk_to_dict(c) for c in chunks],
                "match_count": len(matched),
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc), "paper": None, "chunks": []})


@mcp.tool()
def get_graph_metrics() -> str:
    """Compute and return graph metrics for the entire corpus.

    Builds the citation + similarity graph and computes PageRank,
    degree centrality, and betweenness centrality. Papers are classified
    as hubs (highly cited), bridges (connect clusters), or frontier (peripheral).

    Returns:
        JSON string with keys:
            - hub_papers: list of {id, display_name, pagerank} for hub papers
            - bridge_papers: list of {id, display_name, betweenness} for bridges
            - frontier_papers: list of {id, display_name} for peripheral papers
            - full_ranking: list of all papers ranked by PageRank descending
            - error: present only if something went wrong
    """
    try:
        from sqlmodel import select

        from scholarforge.graph.metrics import compute_metrics
        from scholarforge.store.db import get_session
        from scholarforge.store.models import Paper

        with get_session() as session:
            papers = session.exec(select(Paper)).all()
        id_to_paper = {p.id: p for p in papers}

        metrics = compute_metrics()

        def paper_entry(pid: str) -> dict:
            p = id_to_paper.get(pid)
            return {
                "id": pid,
                "display_name": p.display_name() if p else pid[:16],
                "title": p.title if p else "",
                "authors": p.parsed_authors if p else [],
                "year": p.year if p else None,
            }

        hub_entries = [
            {**paper_entry(pid), "pagerank": metrics.pagerank.get(pid, 0.0)}
            for pid in metrics.hub_papers
        ]
        bridge_entries = [
            {
                **paper_entry(pid),
                "betweenness": metrics.betweenness_centrality.get(pid, 0.0),
            }
            for pid in metrics.bridge_papers
        ]
        frontier_entries = [paper_entry(pid) for pid in metrics.peripheral_papers]

        sorted_pr = sorted(metrics.pagerank.items(), key=lambda x: x[1], reverse=True)
        full_ranking = [
            {
                **paper_entry(pid),
                "pagerank": pr,
                "degree_centrality": metrics.degree_centrality.get(pid, 0.0),
                "betweenness": metrics.betweenness_centrality.get(pid, 0.0),
                "role": metrics.paper_role(pid),
            }
            for pid, pr in sorted_pr
        ]

        return json.dumps(
            {
                "hub_papers": hub_entries,
                "bridge_papers": bridge_entries,
                "frontier_papers": frontier_entries,
                "full_ranking": full_ranking,
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc), "hub_papers": [], "bridge_papers": []})


@mcp.tool()
def list_papers(
    limit: Optional[int] = None,
) -> str:
    """List all papers in the knowledge base with basic metadata.

    Returns a lightweight listing suitable for browsing before deep-diving
    into specific papers.

    Args:
        limit: Maximum number of papers to return. Returns all if None.

    Returns:
        JSON string with keys:
            - papers: list of paper metadata dicts (no chunks)
            - total: total paper count in the corpus
            - error: present only if something went wrong
    """
    try:
        from sqlmodel import select

        from scholarforge.store.db import get_session
        from scholarforge.store.models import Paper

        with get_session() as session:
            all_papers = session.exec(select(Paper)).all()

        total = len(all_papers)
        subset = all_papers if limit is None else all_papers[:limit]
        return json.dumps(
            {
                "papers": [_paper_to_dict(p) for p in subset],
                "total": total,
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc), "papers": [], "total": 0})


@mcp.tool()
def list_topics() -> str:
    """List all topics extracted from the corpus with their paper counts.

    Topics are stored as tags on vault notes. This tool reads directly from
    the Chunk section_path data to infer topic coverage.

    Returns:
        JSON string with keys:
            - topics: list of {topic, paper_count} dicts sorted by paper_count desc
            - total_papers: total number of papers in the corpus
            - error: present only if something went wrong
    """
    try:
        from sqlmodel import select

        from scholarforge.store.db import get_session
        from scholarforge.store.models import Chunk, Paper

        with get_session() as session:
            papers = session.exec(select(Paper)).all()
            chunks = session.exec(select(Chunk)).all()

        # Build topic → paper_ids mapping from section paths
        # Section paths look like "1.Introduction", "3.Methods.3.2.DataCollection"
        # Extract top-level section names as topic proxies
        topic_papers: dict[str, set[str]] = {}
        for chunk in chunks:
            if chunk.section_path:
                parts = chunk.section_path.split(".")
                # Remove leading numeric parts to get readable section name
                topic_parts = [p for p in parts if not p.isdigit()]
                if topic_parts:
                    topic = topic_parts[0].strip()
                    if topic:
                        topic_papers.setdefault(topic, set()).add(chunk.paper_id)

        topics_list = [
            {"topic": topic, "paper_count": len(pids)}
            for topic, pids in sorted(topic_papers.items(), key=lambda x: len(x[1]), reverse=True)
        ]

        return json.dumps(
            {
                "topics": topics_list,
                "total_papers": len(papers),
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc), "topics": [], "total_papers": 0})


@mcp.tool()
def deep_read(
    pattern: str,
) -> str:
    """Retrieve the complete full text of a paper by title/author pattern.

    Returns ALL chunks for the matched paper in reading order.
    This is expensive — prefer search_papers for exploratory queries.
    Use this when you need to read an entire paper in detail.

    Args:
        pattern: Substring to match in title or author list.

    Returns:
        JSON string with keys:
            - paper: paper metadata dict (or null if not found)
            - full_text: complete paper text as a single string
            - chunks: all text chunks in reading order
            - token_count: total token count
            - match_count: how many papers matched (first one is returned)
            - error: present only if something went wrong
    """
    try:
        from sqlmodel import select

        from scholarforge.store.db import get_session
        from scholarforge.store.models import Chunk, Paper

        lower = pattern.lower()
        with get_session() as session:
            all_papers = session.exec(select(Paper)).all()
            matched = [
                p for p in all_papers if lower in p.title.lower() or lower in p.authors.lower()
            ]
            if not matched:
                return json.dumps(
                    {
                        "paper": None,
                        "full_text": "",
                        "chunks": [],
                        "token_count": 0,
                        "match_count": 0,
                    }
                )

            paper = matched[0]
            chunks = session.exec(
                select(Chunk).where(Chunk.paper_id == paper.id).order_by(Chunk.chunk_index)
            ).all()

        full_text = "\n\n".join(
            f"[{c.section_path}]\n{c.content}" if c.section_path else c.content for c in chunks
        )
        total_tokens = sum(c.token_count for c in chunks)

        return json.dumps(
            {
                "paper": _paper_to_dict(paper),
                "full_text": full_text,
                "chunks": [_chunk_to_dict(c) for c in chunks],
                "token_count": total_tokens,
                "match_count": len(matched),
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc), "paper": None, "full_text": "", "chunks": []})


# ── Entry point ───────────────────────────────────────────────────────────────


def run_server(library: str = "default") -> None:
    """Configure library scope and start the MCP stdio server."""
    if library != "default":
        from scholarforge.config import settings

        settings.library = library

    mcp.run(transport="stdio")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ScholarForge MCP Server")
    parser.add_argument(
        "--library",
        default="default",
        help="Library name (for multi-domain research)",
    )
    args = parser.parse_args()
    run_server(library=args.library)
