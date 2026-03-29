"""ScholarForge MCP Server — exposes the knowledge base as tools for LLMs.

Implements the Model Context Protocol (MCP) using FastMCP so that Claude Code
or other MCP-compatible clients can query the literature corpus directly.

Launch via:
    scholarforge mcp [--library <name>]
    python -m scholarforge.mcp_server [--library <name>]
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from scholarforge.agent.tools import (
    _build_corpus_summary,
)
from scholarforge.agent.tools import (
    deep_read as _deep_read,
)
from scholarforge.agent.tools import (
    get_corpus_summary as _get_corpus_summary,
)
from scholarforge.agent.tools import (
    get_graph_metrics as _get_graph_metrics,
)
from scholarforge.agent.tools import (
    get_paper as _get_paper,
)
from scholarforge.agent.tools import (
    get_sections as _get_sections,
)
from scholarforge.agent.tools import (
    ingest_paper as _ingest_paper,
)
from scholarforge.agent.tools import (
    list_papers as _list_papers,
)
from scholarforge.agent.tools import (
    list_topics as _list_topics,
)
from scholarforge.agent.tools import (
    search_papers as _search_papers,
)

mcp = FastMCP(
    "ScholarForge",
    instructions=(
        "ScholarForge knowledge base. Use search_papers to find relevant literature, "
        "get_paper for full details, get_graph_metrics for network analysis, "
        "list_papers/list_topics for browsing, deep_read for full text retrieval, "
        "get_corpus_summary for a high-level corpus overview, "
        "and ingest_paper to add new documents."
    ),
)


# ── Resources ─────────────────────────────────────────────────────────────────


@mcp.resource("scholarforge://corpus")
def corpus_resource() -> str:
    """Corpus summary auto-injected into every session as context."""
    return _build_corpus_summary()


# ── Tools ─────────────────────────────────────────────────────────────────────


@mcp.tool()
def get_corpus_summary() -> str:
    """Get a pre-formatted markdown summary of the entire corpus.

    Returns paper count, year range, top authors, hub/bridge/frontier papers
    from graph metrics, and the topic vocabulary. Optimized for LLM consumption.

    Returns:
        Markdown string with corpus overview.
    """
    return _get_corpus_summary()


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
        Formatted text of relevant paper excerpts followed by a metadata summary line.
    """
    return _search_papers(query, top_k, max_tokens)


@mcp.tool()
def get_paper(
    pattern: str,
) -> str:
    """Get full details for a specific paper by title or author name pattern.

    Performs a case-insensitive substring match against title and author fields.
    Returns the best match with metadata and all chunks grouped by section.

    Args:
        pattern: Substring to match in the paper title or author list.

    Returns:
        Formatted text with paper metadata block followed by chunks grouped by section.
    """
    return _get_paper(pattern)


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
    return _get_graph_metrics()


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
    return _list_papers(limit)


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
    return _list_topics()


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
    return _deep_read(pattern)


@mcp.tool()
def get_sections(
    section_type: str,
    paper_pattern: Optional[str] = None,
) -> str:
    """Retrieve specific section types across papers.

    Enables cross-paper queries like "get all conclusions" or
    "compare methods sections of papers X and Y."

    Valid section types: abstract, introduction, background, methods,
    results, discussion, conclusion, references, acknowledgments,
    appendix, body.

    Args:
        section_type: Canonical section type to retrieve.
        paper_pattern: Optional title/author filter. If None, searches
            all papers.

    Returns:
        Formatted text with sections grouped by paper, or error message.
    """
    return _get_sections(section_type, paper_pattern)


@mcp.tool()
def ingest_paper(file_path: str) -> str:
    """Ingest a document (PDF/DOCX/PPTX) into the knowledge base.

    Parses and persists the document, embeds its summary, runs incremental
    topic extraction and similarity queries, then spawns a background thread
    to refresh cross-paper signals for the whole corpus.

    Args:
        file_path: Absolute path to the document file.

    Returns:
        Status message with paper title, chunk count, and background refresh status.
    """
    return _ingest_paper(file_path)


# ── Entry point ───────────────────────────────────────────────────────────────


def _prewarm() -> None:
    """Pre-load ChromaDB and SentenceTransformer in a background thread.

    This runs on server startup so the first tool call doesn't pay the
    10+ second cold start penalty.  Errors are logged, not swallowed.
    """
    import logging
    import threading

    logger = logging.getLogger(__name__)

    def _load():
        try:
            from scholarforge.store.embeddings import _get_collection

            col = _get_collection()
            logger.info("ChromaDB pre-warmed: %d embeddings", col.count())
        except Exception:
            logger.warning("ChromaDB pre-warm failed", exc_info=True)

        try:
            from scholarforge.store.embeddings import _get_model

            _get_model()
            logger.info("SentenceTransformer pre-warmed")
        except Exception:
            logger.warning("SentenceTransformer pre-warm failed", exc_info=True)

    threading.Thread(target=_load, daemon=True).start()


def run_server(library: str = "default") -> None:
    """Configure library scope and start the MCP stdio server."""
    if library != "default":
        from scholarforge.config import settings

        settings.library = library

    _prewarm()
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
