"""Standalone knowledge-base tool functions for the ScholarForge agent loop.

These functions contain the business logic extracted from the MCP server tools.
They can be used directly by agent loops without going through MCP.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx
import numpy as np
from sqlmodel import select

from wikify.core.graph.metrics import build_corpus_graph, compute_metrics
from wikify.core.llm.vision import view_figure
from wikify.core.retrieve.context import retrieve_for_query
from wikify.core.store.corpus import load_corpus_chunks
from wikify.core.store.db import get_session
from wikify.core.store.embeddings import (
    _store,
    get_chunk_embeddings,
    get_paper_vibe_vectors,
)
from wikify.core.store.gc import gc_run, integrity_check
from wikify.core.store.models import (
    Chunk,
    ConceptRecord,
    DomainCluster,
    Figure,
    Paper,
    PaperTopic,
)
from wikify.core.store.precompute import (
    load_concept_links,
    load_divergent_gaps,
    load_kmeans,
)
from wikify.ingest.corpus_refresh import load_corpus_vocabulary
from wikify.ingest.service import ingest_file
from wikify.papers.agent.concept_graph import get_concept_graph
from wikify.papers.agent.reading_log import get_reading_log
from wikify.papers.agent.run_context import get_current_run_context
from wikify.papers.evaluate.coverage import (
    compute_coverage,
    compute_paper_vibes,
    vibe_map_for_llm,
)
from wikify.papers.evaluate.frontier import (
    format_frontier_order_for_agent,
    frontier_exploration_order,
)
from wikify.wiki.builder import slugify
from wikify.wiki.graph.routing import domain_aware_search, get_domain_context
from wikify.wiki.presentation.layout import iter_visible_page_files
from wikify.wiki.runtime import (
    compare_runs,
    export_metrics,
    query_wiki,
    reconcile_state,
    run_campaign,
    run_maintain,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _format_section_toc(tree: dict, indent: int = 0) -> str:
    """Format a section tree dict as an indented table of contents."""
    lines: list[str] = []
    for child in tree.get("children", []):
        title = child.get("title", "")
        if not title:
            continue
        prefix = "  " * indent
        page = child.get("page")
        page_str = f" (p.{page})" if page else ""
        lines.append(f"{prefix}- {title}{page_str}")
        lines.append(_format_section_toc(child, indent + 1))
    return "\n".join(line for line in lines if line)


def _paper_to_dict(paper) -> dict:
    """Serialize a Paper SQLModel to a plain dict for JSON output."""
    return {
        "id": paper.id,
        "title": paper.title,
        "authors": paper.parsed_authors,
        "year": paper.year,
        "doi": paper.doi,
        "doc_type": paper.doc_type,
        "summary": paper.summary,
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


def _tool_json_success(**payload) -> str:
    """Return a standard JSON success envelope for tool outputs."""
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, default=str)


def _tool_json_error(error: str, **payload) -> str:
    """Return a standard JSON error envelope for tool outputs."""
    return json.dumps({"ok": False, "error": error, **payload}, ensure_ascii=False, default=str)


def _normalize_paper_lookup(text: str) -> str:
    """Normalize paper lookup text for resilient matching."""
    lowered = (text or "").casefold().strip()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return " ".join(lowered.split())


def _match_papers_by_pattern(all_papers: list, pattern: str) -> list:
    """Resolve a paper pattern against title, authors, display name, year, and id."""
    raw = (pattern or "").strip()
    if not raw:
        return []

    raw_lower = raw.casefold()
    raw_normalized = _normalize_paper_lookup(raw)
    ranked: list[tuple[int, object]] = []

    for paper in all_papers:
        candidates = [
            (paper.title or "", 40),
            (paper.display_name(), 35),
            (paper.authors or "", 30),
            (str(paper.year or ""), 20),
            (paper.id or "", 10),
        ]

        score = 0
        for candidate, weight in candidates:
            candidate_lower = candidate.casefold()
            candidate_normalized = _normalize_paper_lookup(candidate)

            if raw_lower == candidate_lower:
                score = max(score, 400 + weight)
            elif raw_normalized and raw_normalized == candidate_normalized:
                score = max(score, 380 + weight)
            elif raw_lower in candidate_lower:
                score = max(score, 300 + weight)
            elif raw_normalized and raw_normalized in candidate_normalized:
                score = max(score, 280 + weight)

        if score:
            ranked.append((score, paper))

    ranked.sort(
        key=lambda item: (
            -item[0],
            -(item[1].year or 0),
            (item[1].title or "").casefold(),
        )
    )
    return [paper for _, paper in ranked]


def _build_corpus_summary() -> str:
    """Build a pre-formatted markdown summary of the corpus for LLM consumption."""



    with get_session() as session:
        papers = session.exec(select(Paper)).all()

    if not papers:
        return "## Corpus: 0 papers\n\nNo papers ingested yet."

    # Paper count and year range
    count = len(papers)
    years = [p.year for p in papers if p.year is not None]
    year_range = f"{min(years)}-{max(years)}" if years else "unknown"

    # Top authors (first-author last name, matching display_name() logic)
    author_counts: Counter = Counter()
    for paper in papers:
        authors = paper.parsed_authors
        if authors:
            last_name = authors[0].split()[-1]
            author_counts[last_name] += 1
    top_authors = author_counts.most_common(5)
    authors_str = ", ".join(f"{name} ({cnt})" for name, cnt in top_authors)

    lines = [
        f"## Corpus: {count} papers ({year_range})",
        "",
        f"**Top Authors**: {authors_str}",
    ]

    # Graph metrics (hub / bridge / frontier) — wrapped defensively
    try:

        metrics = compute_metrics()
        id_to_paper = {p.id: p for p in papers}

        if metrics.hub_papers:
            hub_parts = []
            for pid in metrics.hub_papers:
                p = id_to_paper.get(pid)
                name = p.display_name() if p else pid[:16]
                pr = metrics.pagerank.get(pid, 0.0)
                hub_parts.append(f"{name} (PR: {pr:.3f})")
            lines.append(f"**Hub Papers**: {', '.join(hub_parts)}")

        if metrics.bridge_papers:
            bridge_parts = []
            for pid in metrics.bridge_papers:
                p = id_to_paper.get(pid)
                name = p.display_name() if p else pid[:16]
                bc = metrics.betweenness_centrality.get(pid, 0.0)
                bridge_parts.append(f"{name} (BC: {bc:.3f})")
            lines.append(f"**Bridge Papers**: {', '.join(bridge_parts)}")

        if metrics.peripheral_papers:
            frontier_parts = []
            for pid in metrics.peripheral_papers:
                p = id_to_paper.get(pid)
                name = p.display_name() if p else pid[:16]
                frontier_parts.append(name)
            lines.append(f"**Frontier Papers**: {', '.join(frontier_parts)}")
    except Exception:  # noqa: BLE001
        lines.append("**Graph Metrics**: unavailable (run ingest to populate)")

    # Topics from corpus vocabulary
    try:
        vocab = load_corpus_vocabulary()
        if vocab:
            topic_count = len(vocab)
            topic_preview = ", ".join(vocab[:20])
            lines.append(f"**Topics** ({topic_count}): {topic_preview}")
        else:
            lines.append("**Topics**: not yet extracted (run ingest)")
    except Exception:  # noqa: BLE001
        lines.append("**Topics**: unavailable")

    return "\n".join(lines)


# ── Tool functions ─────────────────────────────────────────────────────────────


def get_corpus_summary() -> str:
    """Get a pre-formatted markdown summary of the entire corpus.

    Returns paper count, year range, top authors, hub/bridge/frontier papers
    from graph metrics, and the topic vocabulary. Optimized for LLM consumption.

    Returns:
        Markdown string with corpus overview.
    """
    try:
        return _build_corpus_summary()
    except Exception as exc:  # noqa: BLE001
        return f"Error building corpus summary: {exc}"


def search_papers(
    query: str,
    top_k: int = 10,
    max_tokens: int = 8000,
    reason: str = "",
) -> str:
    """Semantic search across the literature corpus using embedding similarity.

    Returns paper metadata and relevant text chunks for the given query.
    Results are ranked by embedding distance (most similar first).

    Args:
        query: Natural language search query.
        top_k: Maximum number of papers to return (default 10).
        max_tokens: Token budget for included text chunks (default 8000).
        reason: Why you are searching for this (logged for the reading trace).

    Returns:
        Formatted text of relevant paper excerpts followed by a metadata summary line.
    """
    try:

        ctx = retrieve_for_query(query, max_papers=top_k, max_tokens=max_tokens)

        # Log this search
        if reason:

            get_reading_log().log(
                paper=f"[search: {query}]", tool="search_papers", reason=reason, depth="search"
            )

        text = ctx.as_text()
        if not text:
            return f"No results found for query: {query!r}"

        meta = (
            f"\n\n---\nFound {len(ctx.papers)} papers, "
            f"{len(ctx.chunks)} chunks, {ctx.total_tokens} tokens"
        )
        return text + meta
    except Exception as exc:  # noqa: BLE001
        return f"Search error: {exc}"


def search_wiki_domains(query: str, top_k: int = 10) -> str:
    """Search the wiki with domain-aware routing.

    Routes the query to the most relevant domain community, then expands
    across domain boundaries via bridge concepts if needed. Falls back to
    standard search_papers if no domains have been discovered.

    Args:
        query: Natural language search query.
        top_k: Maximum results to return.

    Returns:
        JSON with domain-scoped search results including domain labels.
    """
    try:

        results = domain_aware_search(query, top_k)
        return _tool_json_success(results=results, count=len(results))
    except Exception as exc:  # noqa: BLE001
        return _tool_json_error(str(exc))


def lookup_citation(
    pattern: str,
    max_results: int = 5,
) -> str:
    """Look up citation metadata for papers by title, author, or year.

    Returns display_name (for [REF:...] markers), authors, year, DOI,
    and BibTeX for each match. Lightweight — no abstract or full text.
    Use this when you need to cite a paper but don't need to read it.

    Args:
        pattern: Substring to match in title, author list, or year.
        max_results: Maximum matches to return (default 5).

    Returns:
        Citation metadata for all matches, or error if none found.
    """
    try:


        with get_session() as session:
            all_papers = session.exec(select(Paper)).all()
            matched = _match_papers_by_pattern(all_papers, pattern)
            if not matched:
                return f"No paper found matching: {pattern!r}"

        lines = [f"Found {len(matched)} matches (showing {min(len(matched), max_results)}):", ""]

        for paper in matched[:max_results]:
            authors = paper.parsed_authors
            authors_str = ", ".join(authors) if authors else "Unknown"
            first_author = authors[0].split()[-1] if authors else "Unknown"

            bibtex_key = f"{first_author.lower()}{paper.year or 'YYYY'}"
            bibtex = (
                f"@article{{{bibtex_key},\n"
                f"  author = {{{authors_str}}},\n"
                f"  title = {{{paper.title}}},\n"
                f"  year = {{{paper.year or 'N/A'}}},\n"
                f"  doi = {{{paper.doi or 'N/A'}}},\n"
                f"}}"
            )

            lines += [
                f"### {paper.display_name()}",
                f"  authors: {authors_str}",
                f"  year: {paper.year or 'Unknown'}",
                f"  doi: {paper.doi or 'N/A'}",
                f"  bibtex: {bibtex}",
                "",
            ]

        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Error looking up citation: {exc}"


def get_paper(
    pattern: str,
    reason: str = "",
) -> str:
    """Get metadata and abstract for a specific paper by title, author, or display-name pattern.

    Performs a case-insensitive substring match against title and author fields.
    Returns the best match with metadata and abstract. For full text, use
    deep_read instead.

    Args:
        pattern: Substring to match in the paper title, author list, display name, year, or id.
        reason: Why you are looking up this paper (logged for the reading trace).

    Returns:
        Formatted text with paper metadata and abstract. Use deep_read for full text.
    """
    try:


        with get_session() as session:
            all_papers = session.exec(select(Paper)).all()
            matched = _match_papers_by_pattern(all_papers, pattern)
            if not matched:
                return f"No paper found matching: {pattern!r}"

            paper = matched[0]
            chunk_count = session.exec(select(Chunk).where(Chunk.paper_id == paper.id)).all()

        # Log this read
        if reason:

            get_reading_log().log(
                paper=paper.display_name(), tool="get_paper", reason=reason, depth="metadata"
            )

        authors = paper.parsed_authors
        authors_str = ", ".join(authors) if authors else "Unknown"
        lines = [
            f"# {paper.title}",
            "",
            f"**Authors**: {authors_str}",
            f"**Year**: {paper.year or 'Unknown'}",
            f"**DOI**: {paper.doi or 'N/A'}",
            f"**Type**: {paper.doc_type}",
            f"**Display name**: {paper.display_name()}",
            f"**Chunks**: {len(chunk_count)}",
        ]
        if paper.summary:
            lines += ["", "## Abstract", paper.summary]

        if len(matched) > 1:
            lines += ["", f"*Note: {len(matched)} papers matched; showing first result.*"]
            lines.append("*Use deep_read for full text.*")

        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Error retrieving paper: {exc}"


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

        return _tool_json_success(
            hub_papers=hub_entries,
            bridge_papers=bridge_entries,
            frontier_papers=frontier_entries,
            full_ranking=full_ranking,
        )
    except Exception as exc:  # noqa: BLE001
        return _tool_json_error(
            str(exc),
            hub_papers=[],
            bridge_papers=[],
            frontier_papers=[],
            full_ranking=[],
        )


def scan_all_abstracts(max_papers: int = 50) -> str:
    """Read the top paper abstracts ranked by citation PageRank.

    Returns a compact listing of papers with display name and abstract,
    ordered by citation authority (most influential first). Capped at
    max_papers (default 50) to avoid context bloat. For the full corpus,
    use get_corpus_summary() or list_papers() instead.

    Args:
        max_papers: Maximum number of papers to return (default 50).

    Returns:
        Formatted text with one entry per paper (display_name + abstract),
        ordered by citation PageRank descending.
    """
    try:


        with get_session() as session:
            all_papers = session.exec(select(Paper)).all()

        # Order by citation PageRank (most influential first)
        try:

            metrics = compute_metrics()
            all_papers.sort(key=lambda p: metrics.pagerank.get(p.id, 0), reverse=True)
        except Exception:  # noqa: BLE001
            # Fallback to year ordering if graph metrics unavailable
            all_papers.sort(key=lambda p: p.year or 0, reverse=True)

        total = len(all_papers)
        subset = all_papers[:max_papers] if max_papers else all_papers
        lines = [
            f"## Paper Abstracts (ranked by citation authority, {total} in corpus)",
            "",
        ]
        for p in subset:
            abstract = (p.summary or "").strip()
            if not abstract:
                abstract = "(no abstract)"
            # Truncate very long abstracts
            if len(abstract) > 500:
                abstract = abstract[:500] + "..."
            lines.append(f"### {p.display_name()}")
            lines.append(abstract)
            lines.append("")

        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Error scanning abstracts: {exc}"


def list_papers(
    limit: int | None = None,
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


        with get_session() as session:
            all_papers = session.exec(select(Paper)).all()

        total = len(all_papers)
        subset = all_papers if limit is None else all_papers[:limit]
        return _tool_json_success(
            papers=[_paper_to_dict(p) for p in subset],
            total=total,
        )
    except Exception as exc:  # noqa: BLE001
        return _tool_json_error(str(exc), papers=[], total=0)


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


        with get_session() as session:
            papers = session.exec(select(Paper)).all()
            chunks = session.exec(select(Chunk)).all()

        # Build topic -> paper_ids mapping from section paths
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

        return _tool_json_success(
            topics=topics_list,
            total_papers=len(papers),
        )
    except Exception as exc:  # noqa: BLE001
        return _tool_json_error(str(exc), topics=[], total_papers=0)


def read_paper_digest(
    pattern: str,
    max_chars: int = 3000,
    reason: str = "",
) -> str:
    """Read a condensed digest of a paper: metadata + abstract + key sections.

    Much cheaper than deep_read (~2KB vs ~70KB). Use this for broad coverage,
    and reserve deep_read for the 3-5 most critical papers.

    Args:
        pattern: Substring to match in title, author list, display name, year, or id.
        max_chars: Maximum characters of body text to include (default 3000).
        reason: Why you are reading this paper (logged for the reading trace).

    Returns:
        Formatted markdown digest of the paper.
    """
    try:


        with get_session() as session:
            all_papers = session.exec(select(Paper)).all()
            matched = _match_papers_by_pattern(all_papers, pattern)
            if not matched:
                return f"No paper found matching: {pattern!r}"

            paper = matched[0]
            chunks = session.exec(
                select(Chunk).where(Chunk.paper_id == paper.id).order_by(Chunk.chunk_index)
            ).all()
            topics = session.exec(select(PaperTopic).where(PaperTopic.paper_id == paper.id)).all()

        # Log this read
        if reason:

            get_reading_log().log(
                paper=paper.display_name(), tool="read_paper_digest", reason=reason, depth="digest"
            )

        topic_list = [t.topic for t in topics]

        # Format section tree as TOC if available
        toc_text = ""
        try:
            tree = json.loads(paper.section_tree) if paper.section_tree else {}
            toc_text = _format_section_toc(tree)
        except (json.JSONDecodeError, TypeError):
            pass

        # Use section summaries if available, otherwise fall back to chunks
        section_summaries_text = ""
        try:
            summaries = (
                json.loads(paper.section_summaries)
                if hasattr(paper, "section_summaries") and paper.section_summaries
                else {}
            )
            if summaries and summaries != {}:
                parts = []
                for path, summary in summaries.items():
                    parts.append(f"**{path}**: {summary}")
                section_summaries_text = "\n".join(parts)
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

        # Build digest: abstract + intro + conclusion (most informative sections)
        priority_sections = ["abstract", "introduction", "conclusion", "results", "discussion"]
        abstract_text = paper.summary or ""
        body_parts: list[str] = []
        char_count = 0

        # If we have section summaries, skip raw chunk text (summaries are better)
        if not section_summaries_text:
            for chunk in chunks:
                section = (chunk.section_path or "").lower()
                is_priority = any(s in section for s in priority_sections)
                if is_priority and char_count < max_chars:
                    text = chunk.content[: max_chars - char_count]
                    body_parts.append(f"[{chunk.section_path}] {text}")
                    char_count += len(text)

        lines = [
            f"# {paper.title}",
            f"**Authors**: {', '.join(paper.parsed_authors)}",
            f"**Year**: {paper.year}",
            f"**DOI**: {paper.doi or 'N/A'}",
            f"**Display name**: {paper.display_name()}",
            f"**Topics**: {', '.join(topic_list) if topic_list else 'N/A'}",
            f"**Chunks**: {len(chunks)}",
        ]

        if toc_text:
            lines.extend(["", "## Structure", toc_text])

        lines.extend(["", "## Abstract", abstract_text])

        if section_summaries_text:
            lines.extend(["", "## Section Summaries", section_summaries_text])
        else:
            lines.extend(
                [
                    "",
                    "## Key Sections",
                    "\n\n".join(body_parts) if body_parts else "(no priority sections found)",
                ]
            )

        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Error reading paper: {exc}"


def deep_read(
    pattern: str,
    reason: str = "",
) -> str:
    """Retrieve the complete full text of a paper by title, author, or display-name pattern.

    Returns ALL chunks for the matched paper in reading order.
    This is expensive (~70KB per paper) -- prefer read_paper_digest for
    broad coverage, and reserve deep_read for the 3-5 most critical papers.

    Args:
        pattern: Substring to match in title, author list, display name, year, or id.
        reason: Why you are deep-reading this paper (logged for the reading trace).

    Returns:
        JSON string with keys:
            - paper: paper metadata dict (or null if not found)
            - full_text: complete paper text as a single string
            - chunks: all text chunks in reading order
            - token_count: total token count
            - match_count: how many papers matched (first one is returned)
            - error: present when no paper matched or something went wrong
    """
    try:


        with get_session() as session:
            all_papers = session.exec(select(Paper)).all()
            matched = _match_papers_by_pattern(all_papers, pattern)
            if not matched:
                return _tool_json_error(
                    f"No paper found matching: {pattern!r}",
                    paper=None,
                    full_text="",
                    chunks=[],
                    token_count=0,
                    match_count=0,
                )

            paper = matched[0]
            chunks = session.exec(
                select(Chunk).where(Chunk.paper_id == paper.id).order_by(Chunk.chunk_index)
            ).all()

        # Log this read
        if reason:

            get_reading_log().log(
                paper=paper.display_name(), tool="deep_read", reason=reason, depth="full"
            )

        full_text = "\n\n".join(
            f"[{c.section_path}]\n{c.content}" if c.section_path else c.content for c in chunks
        )
        total_tokens = sum(c.token_count for c in chunks)

        # Return paper metadata (without summary — it's already in the chunks)
        # and full_text only (no chunks array — it duplicates full_text)
        meta = {
            "title": paper.title,
            "authors": paper.parsed_authors,
            "year": paper.year,
            "doi": paper.doi,
            "display_name": paper.display_name(),
        }

        return _tool_json_success(
            paper=meta,
            full_text=full_text,
            token_count=total_tokens,
            match_count=len(matched),
        )
    except Exception as exc:  # noqa: BLE001
        return _tool_json_error(
            str(exc),
            paper=None,
            full_text="",
            chunks=[],
            token_count=0,
            match_count=0,
        )


def read_section(
    pattern: str,
    section: str,
    reason: str = "",
) -> str:
    """Read the full text of a specific section of a paper.

    Targeted retrieval: returns all chunks matching a section path.
    Use after read_paper_digest to drill into a specific section.
    Much cheaper than deep_read (~5KB vs ~70KB).

    Args:
        pattern: Paper title, author, display-name, year, or id substring.
        section: Section path or keyword (e.g., "methods", "3.2", "Results").
        reason: Why you are reading this section (logged for the reading trace).

    Returns:
        Formatted markdown with the section text, or error message.
    """
    try:


        with get_session() as session:
            all_papers = session.exec(select(Paper)).all()
            matched = _match_papers_by_pattern(all_papers, pattern)
            if not matched:
                return f"No paper found matching: {pattern!r}"

            paper = matched[0]
            chunks = session.exec(
                select(Chunk).where(Chunk.paper_id == paper.id).order_by(Chunk.chunk_index)
            ).all()

        # Match section by substring (case-insensitive)
        section_lower = section.lower()
        matching_chunks = [c for c in chunks if section_lower in (c.section_path or "").lower()]

        if not matching_chunks:
            # List available sections to help the user
            available = sorted({c.section_path for c in chunks if c.section_path})
            return (
                f"No section matching '{section}' in {paper.display_name()}.\n"
                f"Available sections: {', '.join(available)}"
            )

        # Log this read
        if reason:

            get_reading_log().log(
                paper=paper.display_name(),
                tool="read_section",
                reason=reason,
                depth="section",
            )

        body = "\n\n".join(f"[{c.section_path}]\n{c.content}" for c in matching_chunks)
        total_tokens = sum(c.token_count for c in matching_chunks)

        return (
            f"# {paper.display_name()} -- {matching_chunks[0].section_path}\n"
            f"**Chunks**: {len(matching_chunks)} | **Tokens**: {total_tokens}\n\n"
            f"{body}"
        )
    except Exception as exc:  # noqa: BLE001
        return f"Error reading section: {exc}"


def get_sections(
    section_type: str,
    paper_pattern: str | None = None,
    reason: str = "",
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
        reason: Why you are reading these sections (logged for the reading trace).

    Returns:
        Formatted text with sections grouped by paper, or error message.
    """
    try:



        valid_types = {
            "abstract",
            "introduction",
            "background",
            "methods",
            "results",
            "discussion",
            "conclusion",
            "references",
            "acknowledgments",
            "appendix",
            "body",
        }
        st = section_type.lower().strip()
        if st not in valid_types:
            return f"Invalid section_type '{section_type}'. Valid: {', '.join(sorted(valid_types))}"

        with get_session() as session:
            query = (
                select(Chunk)
                .where(Chunk.section_type == st)
                .order_by(Chunk.paper_id, Chunk.chunk_index)
            )
            chunks = list(session.exec(query).all())

            # For "results": also include "discussion" chunks from papers that
            # have no results chunks (handles "Results and Discussion" papers
            # where only a standalone "Discussion" section exists).
            if st == "results":
                paper_ids_with_results = {c.paper_id for c in chunks}
                discussion_chunks = list(
                    session.exec(
                        select(Chunk)
                        .where(Chunk.section_type == "discussion")
                        .order_by(Chunk.paper_id, Chunk.chunk_index)
                    ).all()
                )
                for dc in discussion_chunks:
                    if dc.paper_id not in paper_ids_with_results:
                        chunks.append(dc)

            if not chunks:
                return f"No '{st}' sections found in the corpus."

            # For "conclusion": for papers that still have no conclusion chunk,
            # fall back to the last 3 chunks of that paper ordered by chunk_index.
            if st == "conclusion":
                paper_ids_with_conclusion = {c.paper_id for c in chunks}
                all_papers = session.exec(select(Paper)).all()
                missing_paper_ids = [
                    p.id for p in all_papers if p.id not in paper_ids_with_conclusion
                ]
                for pid in missing_paper_ids:
                    fallback = list(
                        session.exec(
                            select(Chunk)
                            .where(Chunk.paper_id == pid)
                            .where(
                                Chunk.section_type.not_in(  # type: ignore[attr-defined]
                                    ["references", "acknowledgments", "appendix", "abstract"]
                                )
                            )
                            .order_by(Chunk.chunk_index.desc())  # type: ignore[attr-defined]
                            .limit(3)
                        ).all()
                    )
                    # Re-sort ascending so content reads in order
                    fallback.sort(key=lambda c: c.chunk_index)
                    chunks.extend(fallback)

            # Filter by paper pattern if given
            paper_ids = {c.paper_id for c in chunks}
            papers = session.exec(
                select(Paper).where(Paper.id.in_(list(paper_ids)))  # type: ignore[attr-defined]
            ).all()

        id_to_paper = {p.id: p for p in papers}

        # Apply paper pattern filter
        if paper_pattern:
            lower = paper_pattern.lower()
            id_to_paper = {
                pid: p
                for pid, p in id_to_paper.items()
                if lower in p.title.lower() or lower in p.authors.lower()
            }
            chunks = [c for c in chunks if c.paper_id in id_to_paper]

        if not chunks:
            return f"No '{st}' sections match pattern '{paper_pattern}'."

        # Log this section read
        if reason:

            label = f"[sections: {st}]"
            if paper_pattern:
                label += f" filter={paper_pattern}"
            get_reading_log().log(paper=label, tool="get_sections", reason=reason, depth="section")

        # Group by paper and format
        by_paper: dict[str, list] = defaultdict(list)
        for c in chunks:
            by_paper[c.paper_id].append(c)

        sections = []
        for pid, paper_chunks in by_paper.items():
            paper = id_to_paper.get(pid)
            if not paper:
                continue
            name = paper.display_name()
            text = "\n\n".join(c.content for c in paper_chunks)
            tokens = sum(c.token_count for c in paper_chunks)
            sections.append(f"### {name}\n\n{text}\n\n*({tokens} tokens)*")

        header = f"## {st.title()} sections"
        if paper_pattern:
            header += f" (filter: '{paper_pattern}')"
        header += f"\n\n{len(sections)} papers, {len(chunks)} chunks\n"

        return header + "\n---\n\n".join(sections)
    except Exception as exc:  # noqa: BLE001
        return f"Error retrieving sections: {exc}"


def _resolve_to_paper_ids(patterns: list[str]) -> list[str]:
    """Resolve display name patterns to paper IDs (internal helper)."""


    with get_session() as session:
        all_papers = session.exec(select(Paper)).all()

    resolved = []
    for pattern in patterns:
        lower = pattern.lower()
        for p in all_papers:
            if lower in p.display_name().lower() or lower in p.title.lower():
                resolved.append(p.id)
                break
    return resolved


def _compute_read_centroid(
    read_ids: list[str],
    vibe_map: dict,
):
    """Token-weighted centroid of already-read papers (internal helper)."""

    vibes = [vibe_map[pid] for pid in read_ids if pid in vibe_map]
    if not vibes:
        return None
    total_weight = sum(v.n_chunks for v in vibes)
    if total_weight == 0:
        return None
    centroid = sum(v.centroid * (v.n_chunks / total_weight) for v in vibes)
    norm = np.linalg.norm(centroid)
    return centroid / norm if norm > 0 else centroid


def suggest_next_papers(
    already_read: list[str],
    max_suggestions: int = 3,
) -> str:
    """Suggest papers to read next based on graph connectivity and semantic orthogonality.

    Finds papers within 2 hops of what you have already read, then ranks
    them by a combined score: 0.7 * orthogonality (semantic distance from
    your read set) + 0.3 * graph proximity. Higher scores mean the paper
    is connected to your reading but covers different ground.

    Args:
        already_read: List of paper display names or title patterns you have read.
        max_suggestions: Maximum number of suggestions to return (default 3).

    Returns:
        Ranked list of suggested papers with scores and rationale.
    """
    try:


        read_ids = _resolve_to_paper_ids(already_read)
        if not read_ids:
            return "Could not resolve any papers from the provided patterns."

        graph = build_corpus_graph()
        undirected = graph.to_undirected()
        all_nodes = set(graph.nodes())
        read_set = set(read_ids)
        unread = all_nodes - read_set

        # Find candidates within 2 hops
        neighbors = set()
        for rid in read_ids:
            if rid not in undirected:
                continue
            for nb in undirected.neighbors(rid):
                if nb in unread:
                    neighbors.add(nb)
            for nb in list(neighbors):
                if nb in undirected:
                    for nb2 in undirected.neighbors(nb):
                        if nb2 in unread:
                            neighbors.add(nb2)

        if not neighbors:
            return "No unread papers found within 2 hops. Try find_jump_target instead."

        # Compute vibes and read centroid
        vibes = compute_paper_vibes()
        vibe_map = {v.paper_id: v for v in vibes}
        read_centroid = _compute_read_centroid(read_ids, vibe_map)

        if read_centroid is None:
            return "Could not compute read centroid (no vibes for read papers)."

        # Score candidates
        scores = []
        for cid in neighbors:
            vibe = vibe_map.get(cid)
            if not vibe:
                continue

            # Orthogonality: cosine distance from read centroid
            sim = float(np.dot(vibe.centroid, read_centroid))
            orthogonality = 1.0 - sim

            # Graph proximity: inverse shortest path from nearest read paper
            min_dist = 999
            for rid in read_ids:
                try:

                    d = nx.shortest_path_length(undirected, source=rid, target=cid)
                    min_dist = min(min_dist, d)
                except Exception:  # noqa: BLE001
                    pass
            proximity = 1.0 / (1.0 + min_dist)

            score = 0.7 * orthogonality + 0.3 * proximity
            scores.append((cid, score, orthogonality, proximity, min_dist, vibe))

        scores.sort(key=lambda x: x[1], reverse=True)
        top = scores[:max_suggestions]

        if not top:
            return "No scoreable candidates found. Try find_jump_target."

        lines = [f"## Suggested Next Papers ({len(top)} candidates)", ""]
        for i, (cid, score, orth, prox, hops, vibe) in enumerate(top, 1):
            lines.append(f"{i}. **{vibe.display_name}**")
            lines.append(f"   Score: {score:.2f} | Orthogonality: {orth:.2f} | Hops: {hops}")
            # Find which read papers it's connected to
            connected_to = []
            for rid in read_ids:
                if rid in undirected and cid in undirected[rid]:
                    rv = vibe_map.get(rid)
                    if rv:
                        connected_to.append(rv.display_name)
            if connected_to:
                lines.append(f"   Connected to: {', '.join(connected_to[:3])}")
            lines.append("")

        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Error suggesting papers: {exc}"


def get_coverage_gaps(
    review_text: str,
    already_read: list[str] | None = None,
    previous_coverage: float = 0.0,
    threshold: float = 0.5,
) -> str:
    """Compute coverage, map gaps to papers, and track improvement delta.

    Call this after each draft revision to measure progress. The delta
    tells you whether to keep iterating or stop.

    Args:
        review_text: Current draft text.
        already_read: Papers you have read (to distinguish read vs unread gaps).
        previous_coverage: Coverage score from the previous iteration (for delta).
        threshold: Cosine distance threshold for "covered" (default 0.5).

    Returns:
        Coverage report with delta, per-paper gaps, and convergence signal.
    """
    try:



        result = compute_coverage(review_text, threshold=threshold)
        delta = result.coverage_ratio - previous_coverage
        significant = abs(delta) >= 0.02

        # Resolve read patterns for gap analysis
        read_names = set()
        if already_read:
            with get_session() as session:
                all_papers = session.exec(select(Paper)).all()
            for pattern in already_read:
                lower = pattern.lower()
                for p in all_papers:
                    if lower in p.display_name().lower() or lower in p.title.lower():
                        read_names.add(p.display_name())
                        break

        lines = [
            "## Coverage Report",
            "",
            f"**Overall coverage**: {result.coverage_ratio:.1%}",
            f"**Delta from previous**: {delta:+.1%}",
            f"**Corpus chunks**: {len(result.distances)}",
            "",
        ]

        # Convergence signal
        if previous_coverage > 0:
            if significant:
                lines.append("**Status**: Significant improvement, continue iterating")
            else:
                lines.append("**Status**: Coverage plateau (<2% gain), consider stopping")
        lines.append("")

        # Gap analysis: count uncovered chunks per paper
        gap_counts: Counter = Counter()
        for gap in result.uncovered_chunks:
            gap_counts[gap["paper"]] += 1

        # Split into read vs unread
        unread_gaps = {n: c for n, c in gap_counts.items() if n not in read_names}
        read_gaps = {n: c for n, c in gap_counts.items() if n in read_names}

        if unread_gaps:
            lines.append("### Unread papers with uncovered content (high priority)")
            for name, count in sorted(unread_gaps.items(), key=lambda x: x[1], reverse=True)[:5]:
                cov = result.paper_coverage.get(name, 0.0)
                lines.append(f"  {count} gaps, {cov:.0%} covered -> **{name}**")
            lines.append("")

        if read_gaps:
            lines.append("### Already-read papers with remaining gaps")
            for name, count in sorted(read_gaps.items(), key=lambda x: x[1], reverse=True)[:3]:
                cov = result.paper_coverage.get(name, 0.0)
                lines.append(f"  {count} gaps, {cov:.0%} covered -> {name}")
            lines.append("")

        # Least covered papers overall
        lines.append("### Least covered papers")
        sorted_papers = sorted(result.paper_coverage.items(), key=lambda x: x[1])[:5]
        for name, cov in sorted_papers:
            tag = " (read)" if name in read_names else " **(unread)**"
            lines.append(f"  {cov:5.1%} {name}{tag}")

        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Error computing coverage gaps: {exc}"


def find_jump_target(
    already_read: list[str],
    review_text: str,
    exhaustion_threshold: float = 0.75,
) -> str:
    """Find a paper in a disconnected graph region to break path dependency.

    Use this when suggest_next_papers returns only high-similarity candidates,
    indicating the local subgraph is exhausted. Identifies papers far from
    your read set that address the biggest coverage gaps.

    Args:
        already_read: Papers you have read (display names or title patterns).
        review_text: Current draft text (for coverage gap analysis).
        exhaustion_threshold: Similarity above which local neighbors are
            considered "exhausted" (default 0.75).

    Returns:
        Jump recommendation with rationale, or message that local
        neighborhood is not yet exhausted.
    """
    try:


        read_ids = _resolve_to_paper_ids(already_read)
        if not read_ids:
            return "Could not resolve any papers from the provided patterns."

        graph = build_corpus_graph()
        undirected = graph.to_undirected()
        read_set = set(read_ids)

        # Find 2-hop neighborhood
        near_set = set(read_ids)
        for rid in read_ids:
            if rid not in undirected:
                continue
            for nb in undirected.neighbors(rid):
                near_set.add(nb)
                for nb2 in undirected.neighbors(nb):
                    near_set.add(nb2)

        # Compute vibes
        vibes = compute_paper_vibes()
        vibe_map = {v.paper_id: v for v in vibes}
        read_centroid = _compute_read_centroid(read_ids, vibe_map)

        if read_centroid is None:
            return "Could not compute read centroid."

        # Check if local neighborhood is exhausted
        local_unread = near_set - read_set
        local_sims = []
        for pid in local_unread:
            vibe = vibe_map.get(pid)
            if vibe is not None:
                sim = float(np.dot(vibe.centroid, read_centroid))
                local_sims.append(sim)

        is_exhausted = len(local_sims) == 0 or min(local_sims) > exhaustion_threshold

        if not is_exhausted and local_sims:
            min_sim = min(local_sims)
            return (
                f"Local neighborhood NOT exhausted (min similarity: {min_sim:.2f}, "
                f"threshold: {exhaustion_threshold}). "
                f"Use suggest_next_papers instead."
            )

        # Find jump targets: papers outside 2-hop neighborhood
        far_papers = set(graph.nodes()) - near_set

        if not far_papers:
            return "No jump targets: all papers are within 2 hops of your read set."

        # Score by coverage gap
        coverage_result = compute_coverage(review_text)
        pid_cov = coverage_result.paper_id_coverage

        jump_candidates = []
        for pid in far_papers:
            vibe = vibe_map.get(pid)
            if not vibe:
                continue
            paper_cov = pid_cov.get(pid, 1.0)
            # Score: uncovered fraction weighted by paper size
            score = (1.0 - paper_cov) * vibe.n_chunks
            sim = float(np.dot(vibe.centroid, read_centroid))
            jump_candidates.append((pid, score, paper_cov, sim, vibe))

        jump_candidates.sort(key=lambda x: x[1], reverse=True)

        if not jump_candidates:
            return "No scoreable jump targets found."

        lines = [
            "## Jump Recommendation",
            "",
            "Local subgraph EXHAUSTED: all 2-hop neighbors are semantically "
            f"similar to your read set (>{exhaustion_threshold:.0%}).",
            "",
        ]

        for i, (pid, score, cov, sim, vibe) in enumerate(jump_candidates[:3], 1):
            marker = " <- RECOMMENDED" if i == 1 else ""
            lines.append(f"{i}. **{vibe.display_name}**{marker}")
            lines.append(
                f"   Coverage: {cov:.0%} | Similarity to read set: {sim:.2f} "
                f"| Chunks: {vibe.n_chunks}"
            )
            lines.append(f"   Gap score: {score:.1f} (higher = more uncovered content)")
            lines.append("")

        lines.append(
            "After reading the jump target, call suggest_next_papers "
            "to explore its local neighborhood."
        )

        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Error finding jump target: {exc}"


def get_frontier_exploration_order(max_papers: int = 15) -> str:
    """Get a reading order that combines coverage seeds with frontier papers.

    Phase 1: 3 greedy seeds (highest marginal coverage gain) for baseline.
    Phase 2: Remaining papers ranked by low embedding density (frontier)
    AND dissimilarity to already-selected papers (anti-greedy).

    This systematically finds papers like "ALD for space applications"
    that live in sparse regions of the embedding space, rather than
    discovering them by random walk.

    Args:
        max_papers: Total papers to select (default 15).

    Returns:
        Ordered list of papers with depth (full/digest) and rationale.
    """
    try:

        order = frontier_exploration_order(max_papers=max_papers)
        return format_frontier_order_for_agent(order)
    except Exception as exc:  # noqa: BLE001
        return f"Error computing frontier order: {exc}"


def find_corpus_gaps() -> str:
    """Find research gaps: papers sharing citation context but diverging in conclusions.

    Returns coupled-but-divergent paper pairs — papers that cite the same
    sources but reach different conclusions. Each gap represents an unreconciled
    divergence in the literature that a review should address.

    Pre-computed at ingest time from the citation graph and conclusion embeddings.
    Falls back to embedding void detection if no cached gaps are available.

    Identifies two types of gaps:
    1. Embedding voids: regions between research clusters where no papers
       exist. These represent unexplored conceptual territory.
    2. Topical intersection gaps: pairs of topics that are semantically
       related but rarely appear together in the same paper.

    Use this during exploration to identify what's MISSING from the
    literature — potential contributions for a review's "future directions"
    section or for identifying novel research questions.

    Returns:
        Formatted report of corpus gaps with actionable descriptions.
    """
    # Try cached divergent gaps first (computed at ingest time)
    try:

        gaps = load_divergent_gaps()
        if gaps:
            lines = [
                "## Research Gaps (Coupled-but-Divergent Pairs)",
                "",
                "Papers sharing the same citation context but reaching different conclusions.",
                "",
            ]
            for g in gaps[:10]:
                lines.append(
                    f"- **{g['paper_a']}** vs **{g['paper_b']}** "
                    f"(coupling={g['coupling_strength']}, "
                    f"conclusion distance={g['conclusion_distance']})"
                )
                lines.append(f"  {g['rationale']}")
            return "\n".join(lines)
    except Exception:  # noqa: BLE001
        pass

    # Fall back to embedding void + topical intersection approach
    try:
        from sklearn.cluster import KMeans


        chunks = load_corpus_chunks()
        if not chunks:
            return "No corpus chunks available."

        all_ids = [c.id for c in chunks]
        stored = get_chunk_embeddings(all_ids)

        corpus_embs = np.array([stored[c.id] for c in chunks if c.id in stored])
        corpus_norms = np.linalg.norm(corpus_embs, axis=1, keepdims=True)
        corpus_norms[corpus_norms == 0] = 1
        corpus_embs = corpus_embs / corpus_norms

        # Try cached KMeans first (computed at ingest time)

        cached = load_kmeans()
        if cached is not None:
            centroids, labels = cached
            n_clusters = len(centroids)
            c_norms = np.linalg.norm(centroids, axis=1, keepdims=True)
            c_norms[c_norms == 0] = 1
            centroids = centroids / c_norms
        else:
            # Fall back to computing KMeans (~20s)
            n_clusters = min(10, len(corpus_embs) // 20)
            kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
            labels = kmeans.fit_predict(corpus_embs)
            centroids = kmeans.cluster_centers_
            c_norms = np.linalg.norm(centroids, axis=1, keepdims=True)
            c_norms[c_norms == 0] = 1
            centroids = centroids / c_norms

        # Get paper info for labeling clusters
        with get_session() as session:
            papers = {p.id: p for p in session.exec(select(Paper)).all()}

        # Label each cluster by its most representative papers
        {c.id: c.paper_id for c in chunks}
        cluster_papers: dict[int, dict[str, int]] = {}
        for idx, c in enumerate(chunks):
            if c.id in stored:
                label = int(labels[idx] if idx < len(labels) else 0)
                pid = c.paper_id
                cluster_papers.setdefault(label, {}).setdefault(pid, 0)
                cluster_papers[label][pid] += 1

        # Find inter-cluster voids
        voids = []
        for i in range(n_clusters):
            for j in range(i + 1, n_clusters):
                midpoint = (centroids[i] + centroids[j]) / 2
                midpoint /= np.linalg.norm(midpoint) + 1e-9
                mid_sims = corpus_embs @ midpoint
                nearest_sim = float(np.max(mid_sims))
                if nearest_sim < 0.55:
                    # Get cluster labels
                    top_papers_i = sorted(
                        cluster_papers.get(i, {}).items(), key=lambda x: x[1], reverse=True
                    )[:2]
                    top_papers_j = sorted(
                        cluster_papers.get(j, {}).items(), key=lambda x: x[1], reverse=True
                    )[:2]
                    label_i = ", ".join(
                        papers[pid].display_name()[:40] for pid, _ in top_papers_i if pid in papers
                    )
                    label_j = ", ".join(
                        papers[pid].display_name()[:40] for pid, _ in top_papers_j if pid in papers
                    )
                    voids.append(
                        {
                            "void_depth": round(1.0 - nearest_sim, 3),
                            "cluster_a": label_i or f"Cluster {i}",
                            "cluster_b": label_j or f"Cluster {j}",
                        }
                    )
        voids.sort(key=lambda v: v["void_depth"], reverse=True)

        # Topical gaps (secondary signal, with plural normalization)
        topical_gaps = []
        try:
            all_topics = session.exec(select(PaperTopic)).all()
            # Normalize topics: merge plurals (synapses→synapse, etc.)
            topic_papers: dict[str, set[str]] = {}
            for t in all_topics:
                if not (3 <= len(t.topic) <= 60) or "<" in t.topic:
                    continue
                # Normalize plural forms
                key = t.topic.strip()
                key_lower = key.lower()
                if key_lower.endswith("ies") and len(key_lower) > 5:
                    key_lower = key_lower[:-3] + "y"
                elif (
                    key_lower.endswith("s")
                    and not key_lower.endswith("ss")
                    and not key_lower.endswith("us")
                    and len(key_lower) > 4
                ):
                    key_lower = key_lower[:-1]
                # Use normalized form as key, title-case for display
                display = key_lower.title() if len(key_lower) > 4 else key_lower.upper()
                topic_papers.setdefault(display, set()).add(t.paper_id)

            sig = {t: p for t, p in topic_papers.items() if len(p) >= 5}
            t_names = sorted(sig.keys())
            if t_names:
                model = _store.model
                t_embs = model.encode(t_names, show_progress_bar=False, batch_size=64)
                t_norms = np.linalg.norm(t_embs, axis=1, keepdims=True) + 1e-9
                t_embs = t_embs / t_norms
                t_sim = t_embs @ t_embs.T
                for ii in range(len(t_names)):
                    for jj in range(ii + 1, len(t_names)):
                        sim = float(t_sim[ii, jj])
                        if sim < 0.3:
                            continue
                        inter = len(sig[t_names[ii]] & sig[t_names[jj]])
                        if inter < 2:
                            topical_gaps.append(
                                {
                                    "topics": f"{t_names[ii]} + {t_names[jj]}",
                                    "papers": f"{len(sig[t_names[ii]])}+{len(sig[t_names[jj]])}",
                                    "overlap": inter,
                                    "similarity": round(sim, 2),
                                }
                            )
                topical_gaps.sort(key=lambda g: g["similarity"], reverse=True)
        except Exception:  # noqa: BLE001
            pass

        lines = ["## Corpus Gaps", ""]
        if voids:
            lines.append("### Embedding Voids (unexplored regions between research clusters)")
            for v in voids[:10]:
                lines.append(
                    f"- **{v['cluster_a']}** <-> **{v['cluster_b']}** "
                    f"(void depth: {v['void_depth']:.2f})"
                )
            lines.append("")

        if topical_gaps:
            lines.append("### Topical Intersection Gaps (related topics rarely studied together)")
            for g in topical_gaps[:10]:
                lines.append(
                    f"- {g['topics']} ({g['papers']} papers, {g['overlap']} overlap, "
                    f"sim: {g['similarity']})"
                )

        if not voids and not topical_gaps:
            lines.append("No significant gaps detected in the corpus.")

        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Error finding gaps: {exc}"


def find_synthesis_opportunities() -> str:
    """Find opportunities for novel synthesis across corpus papers.

    Identifies pairs/groups of papers that are semantically related but
    approach the topic from different angles. These are opportunities for
    a review to create insights that don't exist in any single paper.

    Returns:
        Formatted list of synthesis opportunities with paper pairs and
        their semantic relationship.
    """
    # Try cached concept links first (section-filtered, boilerplate-free)
    try:

        links = load_concept_links()
        if links:
            lines = [
                "## Concept Links (Shared Results/Discussion Content)",
                "",
                "Paper pairs sharing substantive scientific content.",
                "",
            ]
            for link in links[:15]:
                lines.append(
                    f"- **{link['paper_a']}** <-> **{link['paper_b']}** "
                    f"(sim={link['chunk_sim']}): *{link['shared_label']}*"
                )
            return "\n".join(lines)
    except Exception:  # noqa: BLE001
        pass

    # Fall back to vibe-based pair selection
    try:


        vibes = get_paper_vibe_vectors()
        if not vibes:
            return "No paper vibes available."

        with get_session() as session:
            papers = {p.id: p for p in session.exec(select(Paper)).all()}

        pids = list(vibes.keys())
        vibe_matrix = np.array([vibes[pid] for pid in pids])
        sim = vibe_matrix @ vibe_matrix.T

        # Find paper pairs with moderate similarity (0.65-0.80):
        # related enough to synthesize, different enough to add value
        opportunities = []
        for i in range(len(pids)):
            for j in range(i + 1, len(pids)):
                s = float(sim[i, j])
                if 0.65 <= s <= 0.82:
                    pa = papers.get(pids[i])
                    pb = papers.get(pids[j])
                    if pa and pb:
                        opportunities.append(
                            {
                                "paper_a": pa.display_name()[:60],
                                "paper_b": pb.display_name()[:60],
                                "similarity": round(s, 3),
                                "synthesis_potential": round(1.0 - abs(s - 0.73) / 0.1, 3),
                            }
                        )

        opportunities.sort(key=lambda o: o["synthesis_potential"], reverse=True)

        lines = [
            "## Synthesis Opportunities",
            "",
            "Paper pairs with moderate semantic similarity (related but different).",
            "These are good candidates for cross-paper insights.",
            "",
        ]
        for o in opportunities[:15]:
            lines.append(
                f"- **{o['paper_a']}** + **{o['paper_b']}** "
                f"(sim: {o['similarity']}, potential: {o['synthesis_potential']})"
            )

        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Error finding synthesis opportunities: {exc}"


def get_paper_vibes(top_k: int = 5) -> str:
    """Get the semantic "vibe map" of the corpus.

    Computes a weighted centroid embedding for each paper from its chunk
    content, then shows each paper's nearest semantic neighbors. Papers
    with no close neighbors cover unique ground in the corpus.

    Use this to understand which papers are semantically similar, which
    are unique, and where the conceptual clusters are. This helps you
    decide which papers to read and which to skip.

    Args:
        top_k: Number of nearest neighbors to show per paper.

    Returns:
        Markdown-formatted vibe map showing paper similarities.
    """
    try:

        vibes = compute_paper_vibes()
        return vibe_map_for_llm(vibes, top_k=top_k)
    except Exception as exc:  # noqa: BLE001
        return f"Error computing paper vibes: {exc}"


def evaluate_coverage(review_text: str, threshold: float = 0.5) -> str:
    """Evaluate how well a review covers the corpus semantically.

    Embeds both the review and all corpus chunks, then measures what
    fraction of the corpus's semantic content has a nearby counterpart
    in the review. This is an information-theoretic coverage metric,
    not a citation count.

    Args:
        review_text: The full review markdown text.
        threshold: Cosine distance threshold for "covered" (default 0.5).

    Returns:
        Coverage report with overall score, per-paper coverage, gaps, and redundancy.
    """
    try:

        result = compute_coverage(review_text, threshold=threshold)

        lines = [result.summary(), ""]

        # Per-paper coverage (sorted worst to best)
        if result.paper_coverage:
            lines += ["", "### Per-Paper Coverage"]
            sorted_papers = sorted(result.paper_coverage.items(), key=lambda x: x[1])
            for name, cov in sorted_papers:
                bar = "#" * int(cov * 20)
                lines.append(f"  {cov:5.1%} {bar:20s} {name}")

        # Top gaps
        if result.uncovered_chunks:
            lines += ["", "### Biggest Gaps (uncovered corpus content)"]
            for gap in result.uncovered_chunks[:10]:
                lines.append(
                    f"  [{gap['distance']:.2f}] {gap['paper']} / {gap['section']}: "
                    f"{gap['preview'][:60]}..."
                )

        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Error evaluating coverage: {exc}"


# ── Session context: read-once-summarize pattern ─────────────────────────────


def reset_paper_summaries() -> None:
    """Clear the session paper summary store. Call at the start of each run."""

    get_current_run_context().paper_summaries.clear()


def get_paper_summaries() -> list[dict]:
    """Return all recorded paper summaries (internal helper)."""

    return list(get_current_run_context().paper_summaries)


def record_paper_summary(
    paper_name: str,
    key_findings: list[str],
    quantitative_data: list[str],
    relevance: str,
    concept_links: list[dict] | None = None,
    gaps_noted: list[str] | None = None,
    read_depth: str = "full",
    role: str = "standard",
) -> str:
    """Record a structured summary of a paper after reading it.

    Call this IMMEDIATELY after deep_read or read_paper_digest to distill
    findings into compact form. This builds your working memory AND the
    concept graph for citation lookup.

    The concept_links parameter captures HOW concepts in this paper relate
    to each other. Each link is a dict with:
      {"from": "HfO2", "to": "10^4 endurance", "relation": "achieves",
       "evidence": "6nm film, TiN electrodes"}

    These become edges in the concept graph. When you later need a citation
    for "HfO2 endurance," the graph returns this paper's display_name.

    Args:
        paper_name: The paper's display_name as seen in tool results.
        key_findings: 3-5 specific findings (include numbers).
        quantitative_data: Specific values, metrics, or statistics.
        relevance: 1-2 sentences explaining relevance to the review topic.
        concept_links: Relationship triples extracted from the paper.
            Each dict: {"from": str, "to": str, "relation": str, "evidence": str}.
        gaps_noted: Limitations or gaps identified in this paper.
        read_depth: How the paper was read: "full", "digest", or "section".
        role: Why this paper was read: "hub", "frontier", "bridge", "standard".

    Returns:
        Confirmation with paper name, finding count, and concept links added.
    """
    summary = {
        "paper_name": paper_name,
        "key_findings": key_findings or [],
        "quantitative_data": quantitative_data or [],
        "relevance": relevance,
        "gaps_noted": gaps_noted or [],
        "read_depth": read_depth,
        "role": role,
    }

    get_current_run_context().paper_summaries.append(summary)

    # Build concept graph edges
    n_links = 0
    if concept_links:

        graph = get_concept_graph()
        n_links = graph.add_from_summary(paper_name, concept_links)

    # Also log to reading log

    get_reading_log().log(
        paper=paper_name,
        tool="record_paper_summary",
        reason=(
            f"Distilled {len(key_findings)} findings, "
            f"{len(quantitative_data)} data points, {n_links} concept links"
        ),
        depth=read_depth,
    )

    return (
        f"Recorded summary for '{paper_name}': "
        f"{len(key_findings)} findings, {len(quantitative_data)} data points, "
        f"{n_links} concept links, {len(gaps_noted or [])} gaps."
    )


def get_session_context() -> str:
    """Retrieve all recorded paper summaries as compact markdown.

    Call this instead of re-reading papers. Returns a structured overview
    of everything you have read and extracted so far.

    Returns:
        Markdown-formatted session context with all paper summaries.
    """
    summaries = get_paper_summaries()
    if not summaries:
        return "No paper summaries recorded yet. Use record_paper_summary after reading papers."

    lines = [f"## Session Context ({len(summaries)} papers)", ""]
    for i, s in enumerate(summaries, 1):
        role = s.get("role", "standard")
        role_tag = f" ({role})" if role != "standard" else ""
        lines.append(f"### {i}. {s['paper_name']} [{s['read_depth']}{role_tag}]")
        lines.append(f"**Relevance**: {s['relevance']}")
        if s["key_findings"]:
            lines.append("**Findings**: " + "; ".join(s["key_findings"]))
        if s["quantitative_data"]:
            lines.append("**Data**: " + "; ".join(s["quantitative_data"]))
        if s["gaps_noted"]:
            lines.append("**Gaps**: " + "; ".join(s["gaps_noted"]))
        lines.append("")

    return "\n".join(lines)


def query_concept_graph(concept: str) -> str:
    """Query the concept graph for a concept's neighbors and papers.

    Returns all concepts connected to the given concept and the papers
    that establish each connection. Use this to understand how a concept
    fits into the research landscape and which papers to cite.

    Args:
        concept: A concept to look up (e.g., "HfO2", "endurance", "STDP").

    Returns:
        List of connected concepts with papers and evidence.
    """

    graph = get_concept_graph()
    neighbors = graph.neighbors(concept)

    if not neighbors:
        # Try fuzzy: check if concept is a substring of any node
        fuzzy = [n for n in graph.concepts if concept.lower() in n]
        if fuzzy:
            return f"No exact match for '{concept}'. Similar concepts: {', '.join(fuzzy[:5])}"
        return (
            f"Concept '{concept}' not in graph "
            f"({len(graph.concepts)} concepts, {len(graph.edges)} edges)."
        )

    lines = [f"## Concept: {concept}", f"Connected to {len(neighbors)} concepts:", ""]
    for neighbor, relation, paper, evidence in neighbors:
        ev = f" -- {evidence}" if evidence else ""
        lines.append(f"- {concept} --[{relation}]--> {neighbor}{ev}")
        lines.append(f"  Paper: [REF:{paper}]")

    return "\n".join(lines)


def get_concept_domain_context(concept: str) -> str:
    """Get domain membership and cross-domain context for a concept.

    Returns which domain(s) a concept belongs to, whether it's a bridge
    concept, and which concepts in other domains are connected to it.

    Args:
        concept: Concept name or slug to look up.

    Returns:
        JSON with primary_domain, all_domains, is_bridge, neighbors_in_other_domains.
    """
    try:

        context = get_domain_context(slugify(concept))
        return _tool_json_success(**context)
    except Exception as exc:  # noqa: BLE001
        return _tool_json_error(str(exc))


def list_domain_clusters() -> str:
    """List all discovered domain clusters and their key metrics.

    Returns the auto-discovered domains from the concept graph, including
    labels, concept counts, and bridge concepts.
    """
    try:


        with get_session() as session:
            clusters = list(session.exec(select(DomainCluster)).all())

        cluster_dicts = [
            {
                "id": cl.id,
                "label": cl.label,
                "core_concept_count": len(cl.parsed_core_concepts),
                "bridge_concept_count": len(cl.parsed_bridge_concepts),
                "bridge_concepts": cl.parsed_bridge_concepts,
            }
            for cl in clusters
        ]
        return _tool_json_success(clusters=cluster_dicts, count=len(cluster_dicts))
    except Exception as exc:  # noqa: BLE001
        return _tool_json_error(str(exc))


def find_citation_for(claim: str) -> str:
    """Find the best paper to cite for a given claim or concept.

    Searches the concept graph first (exact/substring match on concepts),
    then falls back to embedding similarity search across corpus chunks.
    Returns the display_name ready for a [REF:...] marker.

    Args:
        claim: The claim or concept that needs a citation
            (e.g., "HfO2 endurance exceeds 10^4 cycles").

    Returns:
        Best matching paper with display_name and evidence.
    """

    graph = get_concept_graph()

    # Strategy 1: concept graph lookup (fast, exact)
    # Split claim into words and check each against graph concepts
    claim_words = [w.lower().strip(".,;:") for w in claim.split() if len(w) > 2]
    best_matches: list[tuple[str, str]] = []

    for word in claim_words:
        for concept in graph.concepts:
            if word in concept or concept in word:
                citations = graph.find_citation(concept)
                for paper, evidence in citations:
                    best_matches.append((paper, evidence or concept))

    if best_matches:
        # Deduplicate, prefer matches with evidence
        seen: set[str] = set()
        unique: list[tuple[str, str]] = []
        for paper, ev in best_matches:
            if paper not in seen:
                seen.add(paper)
                unique.append((paper, ev))

        lines = [f"Citations for: '{claim}'", ""]
        for paper, ev in unique[:3]:
            lines.append(f"- [REF:{paper}]")
            lines.append(f"  Evidence: {ev}")
        return "\n".join(lines)

    # Strategy 2: fall back to embedding search across corpus chunks
    try:
        result = search_papers(query=claim, top_k=3, max_tokens=500)
        return f"No concept graph match. Semantic search results:\n{result}"
    except Exception:  # noqa: BLE001
        return f"No citation found for: '{claim}'"


def get_reading_log_text() -> str:
    """Get the current reading log as markdown.

    Returns the trace of all papers read during this session, including
    which tool was used and the reason for each read. Returns empty
    message if no papers have been read yet.
    """

    log = get_reading_log()
    if not log.entries:
        return "No papers have been read yet in this session."
    return log.to_markdown()


def save_reading_log(output_dir: str, basename: str = "reading_log") -> str:
    """Save the reading log to disk alongside the output files.

    Writes both .md (human-readable) and .json (machine-readable) versions.

    Args:
        output_dir: Directory to save the log files.
        basename: Filename stem (default: "reading_log").

    Returns:
        Path to the saved markdown log.
    """

    log = get_reading_log()
    if not log.entries:
        return "No papers have been read yet — nothing to save."
    path = log.save(output_dir, basename)
    return f"Reading log saved: {path} ({len(log.entries)} entries)"


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
    try:



        path = Path(file_path)
        if not path.exists():
            return _tool_json_error(
                f"File not found: {file_path}",
                status="missing_file",
                file_path=file_path,
            )

        supported = {".pdf", ".docx", ".pptx"}
        if path.suffix.lower() not in supported:
            supported_str = ", ".join(sorted(supported))
            return _tool_json_error(
                f"Unsupported format {path.suffix!r}. Supported: {supported_str}",
                status="unsupported_format",
                file_path=file_path,
                supported_formats=sorted(supported),
            )

        # Compute the paper ID (SHA256) before ingestion so we can look it up after
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()

        def _epoch_trigger(_new_paper_ids: list[str]) -> None:
            from wikify.wiki.epoch import run_epoch

            run_epoch(triggered_by="ingest")

        result = ingest_file(
            path,
            background_refresh=True,
            epoch_trigger_hook=_epoch_trigger,
        )

        if result == 0:
            # May have been skipped (already ingested) or failed
            with get_session() as session:
                existing = session.get(Paper, file_hash)
            if existing:
                message = f"Already ingested: {existing.display_name()} (no changes detected)"
                return _tool_json_success(
                    status="already_ingested",
                    message=message,
                    file_path=file_path,
                    paper=_paper_to_dict(existing),
                    chunk_count=None,
                    background_refresh=False,
                )
            return _tool_json_error(
                f"Ingestion failed or skipped for: {path.name}",
                status="skipped",
                file_path=file_path,
            )

        # Retrieve paper details from DB
        with get_session() as session:
            paper = session.get(Paper, file_hash)
            if paper:
                chunks = session.exec(select(Chunk).where(Chunk.paper_id == paper.id)).all()
                n_chunks = len(chunks)
                message = (
                    f"Ingested: {paper.display_name()} "
                    f"({n_chunks} chunks) -- background corpus refresh started"
                )
                return _tool_json_success(
                    status="ingested",
                    message=message,
                    file_path=file_path,
                    paper=_paper_to_dict(paper),
                    chunk_count=n_chunks,
                    background_refresh=True,
                )

        message = f"Ingested: {path.name} -- background corpus refresh started"
        return _tool_json_success(
            status="ingested",
            message=message,
            file_path=file_path,
            paper=None,
            chunk_count=None,
            background_refresh=True,
        )

    except Exception as exc:  # noqa: BLE001
        return _tool_json_error(
            f"Ingestion error: {exc}",
            status="error",
            file_path=file_path,
        )


# ── Wiki tools ──────────────────────────────────────────────────────────────


def check_wiki_health() -> str:
    """Check wiki integrity: DB orphans, broken wikilinks, stale articles.

    Combines DB integrity checks with filesystem wiki health scan.
    Returns a structured JSON health report.
    """



    wiki_dir = Path("data/wiki")

    # DB integrity
    db_report = integrity_check()

    # Filesystem checks
    concept_ids: set[str] = set()
    with get_session() as session:
        for c in session.exec(select(ConceptRecord)).all():
            concept_ids.add(c.id)

    # Count articles on disk
    articles_on_disk = 0
    broken_links: list[str] = []
    visible_pages = iter_visible_page_files(wiki_dir)
    visible_slugs = {path.stem for path in visible_pages}
    for md_file in visible_pages:
        articles_on_disk += 1

        # Check wikilinks
        text = md_file.read_text(encoding="utf-8", errors="replace")
        links = re.findall(r"\[\[([^\]|]+)", text)
        for link in links:

            slug = slugify(link.strip())
            if slug not in visible_slugs:
                broken_links.append(f"{md_file.stem} -> [[{link}]]")

    # Orphan concepts (in DB, no article on disk)
    concepts_with_articles = set(visible_slugs)

    orphan_concepts = [
        cid for cid in concept_ids if cid not in concepts_with_articles and cid in concept_ids
    ]

    # Ghost articles (on disk, not in DB)
    ghost_articles = [stem for stem in concepts_with_articles if stem not in concept_ids]

    report = {
        **db_report,
        "articles_on_disk": articles_on_disk,
        "broken_wikilinks": len(broken_links),
        "broken_wikilinks_sample": broken_links[:10],
        "orphan_concepts_no_article": len(orphan_concepts),
        "ghost_articles_no_db": len(ghost_articles),
    }

    return _tool_json_success(**report)


def search_wiki(query: str, top_k: int = 10) -> str:
    """Search wiki articles using tiered retrieval (cache -> BM25 -> embeddings).

    Returns matching wiki article summaries with wikilinks.
    Useful for /wiki-ask and /wiki-campaign to check existing knowledge.
    """



    # Search the concept definitions via ConceptRecord
    with get_session() as session:
        all_concepts: list[ConceptRecord] = list(session.exec(select(ConceptRecord)).all())

    # Simple text matching against concept names and definitions
    query_lower = query.lower()
    scored: list[tuple[ConceptRecord, float]] = []
    for c in all_concepts:
        score = 0.0
        name_lower = c.name.lower()
        defn_lower = (c.definition or "").lower()

        # Name match
        for word in query_lower.split():
            if word in name_lower:
                score += 2.0
            if word in defn_lower:
                score += 1.0

        if score > 0:
            scored.append((c, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:top_k]

    if not top:
        return _tool_json_success(query=query, results=[], message="No wiki articles match")

    results = []
    wiki_dir = Path("data/wiki")
    visible_slugs = {path.stem for path in iter_visible_page_files(wiki_dir)}
    for concept, score in top:
        has_article = concept.id in visible_slugs

        results.append(
            {
                "concept_id": concept.id,
                "name": concept.name,
                "type": concept.concept_type,
                "definition": concept.definition,
                "importance": concept.importance,
                "has_article": has_article,
                "score": round(score, 2),
            }
        )

    return _tool_json_success(query=query, results=results)


def run_wiki_gc() -> str:
    """Run garbage collection on the wiki database.

    Redirects merged concept references, removes orphaned rows,
    and cleans ChromaDB staging. Safe to run at any time.
    """

    result = gc_run()
    return _tool_json_success(
        message="Garbage collection complete",
        redirected=result["redirected"],
        orphans_removed=result["orphans_removed"],
        staging_cleaned=result["staging_cleaned"],
    )


def reconcile_wiki_state() -> str:
    """Rebuild operational wiki page state from visible markdown files."""


    return _tool_json_success(**reconcile_state(Path("data/wiki")))


def run_wiki_maintain() -> str:
    """Run the maintenance sweep over the visible wiki and operational layer."""


    return _tool_json_success(**run_maintain(Path("data/wiki")))


def export_wiki_metrics(workflow_type: str = "", limit: int = 20) -> str:
    """Export aggregated run telemetry and wiki metrics."""


    return _tool_json_success(
        **export_metrics(Path("data/wiki"), workflow_type=workflow_type, limit=limit)
    )


def compare_wiki_runs(workflow_type: str = "", limit: int = 10) -> str:
    """Compare recent wiki runs on cost, retrieval effort, and outcome metrics."""


    return _tool_json_success(
        **compare_runs(Path("data/wiki"), workflow_type=workflow_type, limit=limit)
    )


def query_wiki_runtime(
    question: str,
    domain: str = "",
    model: str | None = None,
    promote: bool = False,
) -> str:
    """Answer a question from the visible wiki via the shared runtime."""


    return _tool_json_success(
        **query_wiki(
            question,
            wiki_dir=Path("data/wiki"),
            domain=domain,
            model=model,
            promote=promote,
            page_type="query",
        )
    )


def run_wiki_campaign(
    thesis: str,
    name: str = "",
    domain: str = "",
    epochs: int = 1,
    model: str | None = None,
    promote: bool = True,
) -> str:
    """Run a thesis-driven wiki campaign through the shared runtime."""


    return _tool_json_success(
        **run_campaign(
            thesis,
            wiki_dir=Path("data/wiki"),
            name=name,
            domain=domain,
            epochs=epochs,
            model=model,
            promote=promote,
        )
    )


# ── Figure tools ──────────────────────────────────────────────────────────────


def get_figure_details(figure_id: str) -> str:
    """Get figure details including image for LLM viewing.

    Returns metadata, caption, LLM description, and base64 image
    so the writing agent can inspect figures during article writing.

    Args:
        figure_id: The Figure.id (content hash) to look up.

    Returns:
        JSON string with keys: caption, llm_description, paper_title,
        media_type, image_base64 (or error).
    """
    try:

        result = view_figure(figure_id)
        if "error" in result:
            return _tool_json_error(result["error"])
        return _tool_json_success(**result)
    except Exception as exc:  # noqa: BLE001
        return _tool_json_error(str(exc))


def get_paper_figures(paper_id: str) -> str:
    """List all figures and tables for a paper with metadata.

    Args:
        paper_id: The Paper.id to look up figures for.

    Returns:
        JSON string with keys:
            - figures: list of figure metadata dicts
            - total: number of figures found
    """
    try:


        with get_session() as session:
            figures = session.exec(select(Figure).where(Figure.paper_id == paper_id)).all()

        figure_list = []
        for fig in figures:
            figure_list.append(
                {
                    "id": fig.id,
                    "paper_id": fig.paper_id,
                    "caption": fig.caption or "",
                    "figure_number": fig.figure_number or "",
                    "section_path": fig.section_path or "",
                    "width_px": fig.width_px,
                    "height_px": fig.height_px,
                    "format": fig.format,
                    "llm_description": fig.llm_description or "",
                    "has_extracted_data": bool(fig.extracted_data),
                }
            )

        return _tool_json_success(figures=figure_list, total=len(figure_list))
    except Exception as exc:  # noqa: BLE001
        return _tool_json_error(str(exc), figures=[], total=0)
