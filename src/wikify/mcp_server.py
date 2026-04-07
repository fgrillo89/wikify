"""ScholarForge MCP Server — exposes the knowledge base as tools for LLMs.

Implements the Model Context Protocol (MCP) using FastMCP so that Codex,
Claude Code, or other MCP-compatible clients can query the literature corpus
directly.

Launch via:
    wikify mcp [--library <name>]
    python -m wikify.mcp_server [--library <name>]
"""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from wikify.agent.tools import (
    _build_corpus_summary,
    check_wiki_health as _check_wiki_health,
    compare_wiki_runs as _compare_wiki_runs,
    deep_read as _deep_read,
    export_wiki_metrics as _export_wiki_metrics,
    get_corpus_summary as _get_corpus_summary,
    get_graph_metrics as _get_graph_metrics,
    get_paper as _get_paper,
    get_sections as _get_sections,
    ingest_paper as _ingest_paper,
    list_papers as _list_papers,
    list_topics as _list_topics,
    query_wiki_runtime as _query_wiki_runtime,
    reconcile_wiki_state as _reconcile_wiki_state,
    run_wiki_campaign as _run_wiki_campaign,
    run_wiki_gc as _run_wiki_gc,
    run_wiki_maintain as _run_wiki_maintain,
    search_papers as _search_papers,
    search_wiki as _search_wiki,
)

mcp = FastMCP(
    "ScholarForge",
    instructions=(
        "Wikify knowledge base. Use search_papers to find relevant literature, "
        "search_wiki to search the wiki, get_paper for full details, "
        "get_graph_metrics for network analysis, check_wiki_health for integrity, "
        "list_papers/list_topics for browsing, deep_read for full text retrieval, "
        "get_corpus_summary for a high-level corpus overview, "
        "run_wiki_gc for database cleanup, run_wiki_maintain and "
        "reconcile_wiki_state for operational upkeep, export_wiki_metrics and "
        "compare_wiki_runs for telemetry and run analysis, query_wiki_runtime "
        "for wiki-first question answering, run_wiki_campaign for thesis-driven "
        "wiki investigations, "
        "and ingest_paper to add new documents."
    ),
)


# ── Resources ─────────────────────────────────────────────────────────────────


@mcp.resource("wikify://corpus")
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


@mcp.tool()
def search_wiki(query: str, top_k: int = 10) -> str:
    """Search wiki articles by concept name and definition.

    Uses tiered matching to find relevant wiki concepts.
    Returns concept metadata and whether an article exists on disk.

    Args:
        query: Natural language search query.
        top_k: Maximum number of results.

    Returns:
        JSON with matching wiki concepts and their metadata.
    """
    return _search_wiki(query, top_k)


@mcp.tool()
def check_wiki_health() -> str:
    """Check wiki integrity: DB orphans, broken wikilinks, stale articles.

    Returns a structured health report with counts of issues.
    Run this before /wiki-maintain to see what needs fixing.
    """
    return _check_wiki_health()


@mcp.tool()
def run_wiki_gc() -> str:
    """Run garbage collection on the wiki database.

    Redirects merged concept references, removes orphaned rows,
    and cleans ChromaDB staging. Safe to run at any time.
    """
    return _run_wiki_gc()


@mcp.tool()
def reconcile_wiki_state() -> str:
    """Rebuild operational wiki page state from visible markdown files."""
    return _reconcile_wiki_state()


@mcp.tool()
def run_wiki_maintain() -> str:
    """Run the maintenance sweep over the visible wiki and operational layer."""
    return _run_wiki_maintain()


@mcp.tool()
def export_wiki_metrics(workflow_type: str = "", limit: int = 20) -> str:
    """Export aggregated run telemetry and wiki metrics."""
    return _export_wiki_metrics(workflow_type=workflow_type, limit=limit)


@mcp.tool()
def compare_wiki_runs(workflow_type: str = "", limit: int = 10) -> str:
    """Compare recent wiki runs on cost, retrieval effort, and outcome metrics."""
    return _compare_wiki_runs(workflow_type=workflow_type, limit=limit)


@mcp.tool()
def query_wiki_runtime(
    question: str,
    domain: str = "",
    model: str = "",
    promote: bool = False,
) -> str:
    """Answer a question from the visible wiki via the shared runtime."""
    return _query_wiki_runtime(
        question=question,
        domain=domain,
        model=model or None,
        promote=promote,
    )


@mcp.tool()
def run_wiki_campaign(
    thesis: str,
    name: str = "",
    domain: str = "",
    epochs: int = 1,
    model: str = "",
    promote: bool = True,
) -> str:
    """Run a thesis-driven campaign over the visible wiki and operational state."""
    return _run_wiki_campaign(
        thesis=thesis,
        name=name,
        domain=domain,
        epochs=epochs,
        model=model or None,
        promote=promote,
    )


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
            from wikify.store.embeddings import _get_collection

            col = _get_collection()
            logger.info("ChromaDB pre-warmed: %d embeddings", col.count())
        except Exception:
            logger.warning("ChromaDB pre-warm failed", exc_info=True)

        try:
            from wikify.store.embeddings import _get_model

            _get_model()
            logger.info("SentenceTransformer pre-warmed")
        except Exception:
            logger.warning("SentenceTransformer pre-warm failed", exc_info=True)

    threading.Thread(target=_load, daemon=True).start()


def run_server(library: str = "default") -> None:
    """Configure library scope and start the MCP stdio server."""
    if library != "default":
        from wikify.config import settings

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
