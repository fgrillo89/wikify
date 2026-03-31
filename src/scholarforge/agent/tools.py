"""Standalone knowledge-base tool functions for the ScholarForge agent loop.

These functions contain the business logic extracted from the MCP server tools.
They can be used directly by agent loops without going through MCP.
"""

from __future__ import annotations

import json

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


def _build_corpus_summary() -> str:
    """Build a pre-formatted markdown summary of the corpus for LLM consumption."""
    from collections import Counter

    from sqlmodel import select

    from scholarforge.ingest.registry import _load_corpus_vocabulary
    from scholarforge.store.db import get_session
    from scholarforge.store.models import Paper

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
        from scholarforge.graph.metrics import compute_metrics

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
        vocab = _load_corpus_vocabulary()
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
        from scholarforge.retrieve.context import retrieve_for_query

        ctx = retrieve_for_query(query, max_papers=top_k, max_tokens=max_tokens)

        # Log this search
        if reason:
            from scholarforge.agent.reading_log import get_reading_log

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
                return f"No paper found matching: {pattern!r}"

            paper = matched[0]
            chunks = session.exec(
                select(Chunk).where(Chunk.paper_id == paper.id).order_by(Chunk.chunk_index)
            ).all()

        # Build metadata block
        authors = paper.parsed_authors
        authors_str = ", ".join(authors) if authors else "Unknown"
        lines = [
            f"# {paper.title}",
            "",
            f"**Authors**: {authors_str}",
            f"**Year**: {paper.year or 'Unknown'}",
            f"**DOI**: {paper.doi or 'N/A'}",
            f"**Type**: {paper.doc_type}",
        ]
        if paper.summary:
            lines += ["", "## Abstract", paper.summary]

        if len(matched) > 1:
            lines += ["", f"*Note: {len(matched)} papers matched; showing first result.*"]

        # Group chunks by section
        sections: dict[str, list] = {}
        for chunk in chunks:
            key = chunk.section_path or "(Unsectioned)"
            sections.setdefault(key, []).append(chunk)

        lines += ["", "## Full Text"]
        for section_path, section_chunks in sections.items():
            lines += ["", f"### {section_path}"]
            for chunk in section_chunks:
                lines.append(chunk.content)

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


def scan_all_abstracts() -> str:
    """Read all paper abstracts in the corpus — a fast overview of everything.

    Returns a compact listing of every paper's display name and abstract.
    Use this as the FIRST exploration step to understand the full corpus
    before deciding which papers to read deeper. Cost: ~400KB for 200 papers.

    Returns:
        Formatted text with one entry per paper (display_name + abstract).
    """
    try:
        from sqlmodel import select

        from scholarforge.store.db import get_session
        from scholarforge.store.models import Paper

        with get_session() as session:
            all_papers = session.exec(select(Paper).order_by(Paper.year)).all()

        lines = [f"## Corpus Abstracts ({len(all_papers)} papers)", ""]
        for p in all_papers:
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


def read_paper_digest(
    pattern: str,
    max_chars: int = 3000,
    reason: str = "",
) -> str:
    """Read a condensed digest of a paper: metadata + abstract + key sections.

    Much cheaper than deep_read (~2KB vs ~70KB). Use this for broad coverage,
    and reserve deep_read for the 3-5 most critical papers.

    Args:
        pattern: Substring to match in title or author list.
        max_chars: Maximum characters of body text to include (default 3000).
        reason: Why you are reading this paper (logged for the reading trace).

    Returns:
        Formatted markdown digest of the paper.
    """
    try:
        from sqlmodel import select

        from scholarforge.store.db import get_session
        from scholarforge.store.models import Chunk, Paper, PaperTopic

        lower = pattern.lower()
        with get_session() as session:
            all_papers = session.exec(select(Paper)).all()
            matched = [
                p for p in all_papers if lower in p.title.lower() or lower in p.authors.lower()
            ]
            if not matched:
                return f"No paper found matching: {pattern!r}"

            paper = matched[0]
            chunks = session.exec(
                select(Chunk).where(Chunk.paper_id == paper.id).order_by(Chunk.chunk_index)
            ).all()
            topics = session.exec(select(PaperTopic).where(PaperTopic.paper_id == paper.id)).all()

        # Log this read
        if reason:
            from scholarforge.agent.reading_log import get_reading_log

            get_reading_log().log(
                paper=paper.display_name(), tool="read_paper_digest", reason=reason, depth="digest"
            )

        topic_list = [t.topic for t in topics]

        # Build digest: abstract + intro + conclusion (most informative sections)
        priority_sections = ["abstract", "introduction", "conclusion", "results", "discussion"]
        abstract_text = paper.summary or ""
        body_parts: list[str] = []
        char_count = 0

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
            "",
            "## Abstract",
            abstract_text,
            "",
            "## Key Sections",
            "\n\n".join(body_parts) if body_parts else "(no priority sections found)",
        ]

        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        return f"Error reading paper: {exc}"


def deep_read(
    pattern: str,
    reason: str = "",
) -> str:
    """Retrieve the complete full text of a paper by title/author pattern.

    Returns ALL chunks for the matched paper in reading order.
    This is expensive (~70KB per paper) -- prefer read_paper_digest for
    broad coverage, and reserve deep_read for the 3-5 most critical papers.

    Args:
        pattern: Substring to match in title or author list.
        reason: Why you are deep-reading this paper (logged for the reading trace).

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

        # Log this read
        if reason:
            from scholarforge.agent.reading_log import get_reading_log

            get_reading_log().log(
                paper=paper.display_name(), tool="deep_read", reason=reason, depth="full"
            )

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
        from collections import defaultdict

        from sqlmodel import select

        from scholarforge.store.db import get_session
        from scholarforge.store.models import Chunk, Paper

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
            from scholarforge.agent.reading_log import get_reading_log

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
    from sqlmodel import select

    from scholarforge.store.db import get_session
    from scholarforge.store.models import Paper

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
    import numpy as np

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
        import numpy as np

        from scholarforge.evaluate.coverage import compute_paper_vibes
        from scholarforge.graph.metrics import build_corpus_graph

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
                    import networkx as nx

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
        from collections import Counter

        from sqlmodel import select

        from scholarforge.evaluate.coverage import compute_coverage
        from scholarforge.store.db import get_session
        from scholarforge.store.models import Paper

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
        import numpy as np

        from scholarforge.evaluate.coverage import compute_coverage, compute_paper_vibes
        from scholarforge.graph.metrics import build_corpus_graph

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
        from scholarforge.evaluate.frontier import (
            format_frontier_order_for_agent,
            frontier_exploration_order,
        )

        order = frontier_exploration_order(max_papers=max_papers)
        return format_frontier_order_for_agent(order)
    except Exception as exc:  # noqa: BLE001
        return f"Error computing frontier order: {exc}"


def find_corpus_gaps() -> str:
    """Find unexplored gaps in the corpus using embedding analysis.

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
    try:
        import numpy as np
        from sklearn.cluster import KMeans
        from sqlmodel import select

        from scholarforge.evaluate.coverage import load_corpus_chunks
        from scholarforge.store.db import get_session
        from scholarforge.store.embeddings import _store, get_chunk_embeddings
        from scholarforge.store.models import Paper, PaperTopic

        chunks = load_corpus_chunks()
        if not chunks:
            return "No corpus chunks available."

        all_ids = [c.id for c in chunks]
        stored = get_chunk_embeddings(all_ids)

        corpus_embs = np.array([stored[c.id] for c in chunks if c.id in stored])
        corpus_norms = np.linalg.norm(corpus_embs, axis=1, keepdims=True)
        corpus_norms[corpus_norms == 0] = 1
        corpus_embs = corpus_embs / corpus_norms

        # Cluster corpus to find themes
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
                    voids.append({
                        "void_depth": round(1.0 - nearest_sim, 3),
                        "cluster_a": label_i or f"Cluster {i}",
                        "cluster_b": label_j or f"Cluster {j}",
                    })
        voids.sort(key=lambda v: v["void_depth"], reverse=True)

        # Topical gaps (secondary signal)
        topical_gaps = []
        try:
            all_topics = session.exec(select(PaperTopic)).all()
            topic_papers: dict[str, set[str]] = {}
            for t in all_topics:
                if 3 <= len(t.topic) <= 60 and "<" not in t.topic:
                    topic_papers.setdefault(t.topic, set()).add(t.paper_id)

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
                            topical_gaps.append({
                                "topics": f"{t_names[ii]} + {t_names[jj]}",
                                "papers": f"{len(sig[t_names[ii]])}+{len(sig[t_names[jj]])}",
                                "overlap": inter,
                                "similarity": round(sim, 2),
                            })
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
    try:
        import numpy as np
        from sqlmodel import select

        from scholarforge.store.db import get_session
        from scholarforge.store.embeddings import get_paper_vibe_vectors
        from scholarforge.store.models import Paper

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
                        opportunities.append({
                            "paper_a": pa.display_name()[:60],
                            "paper_b": pb.display_name()[:60],
                            "similarity": round(s, 3),
                            "synthesis_potential": round(1.0 - abs(s - 0.73) / 0.1, 3),
                        })

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
        from scholarforge.evaluate.coverage import compute_paper_vibes, vibe_map_for_llm

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
        from scholarforge.evaluate.coverage import compute_coverage

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


def get_reading_log_text() -> str:
    """Get the current reading log as markdown.

    Returns the trace of all papers read during this session, including
    which tool was used and the reason for each read. Returns empty
    message if no papers have been read yet.
    """
    from scholarforge.agent.reading_log import get_reading_log

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
    from scholarforge.agent.reading_log import get_reading_log

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
        import hashlib
        from pathlib import Path

        from sqlmodel import select

        from scholarforge.ingest.registry import _ingest_file
        from scholarforge.store.db import get_session
        from scholarforge.store.models import Chunk, Paper

        path = Path(file_path)
        if not path.exists():
            return f"Error: file not found: {file_path}"

        supported = {".pdf", ".docx", ".pptx"}
        if path.suffix.lower() not in supported:
            supported_str = ", ".join(sorted(supported))
            return f"Error: unsupported format {path.suffix!r}. Supported: {supported_str}"

        # Compute the paper ID (SHA256) before ingestion so we can look it up after
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()

        result = _ingest_file(path, background_refresh=True)

        if result == 0:
            # May have been skipped (already ingested) or failed
            with get_session() as session:
                existing = session.get(Paper, file_hash)
            if existing:
                return f"Already ingested: {existing.display_name()} (no changes detected)"
            return f"Ingestion failed or skipped for: {path.name}"

        # Retrieve paper details from DB
        with get_session() as session:
            paper = session.get(Paper, file_hash)
            if paper:
                chunks = session.exec(select(Chunk).where(Chunk.paper_id == paper.id)).all()
                n_chunks = len(chunks)
                return (
                    f"Ingested: {paper.display_name()} "
                    f"({n_chunks} chunks) -- background corpus refresh started"
                )

        return f"Ingested: {path.name} -- background corpus refresh started"

    except Exception as exc:  # noqa: BLE001
        return f"Ingestion error: {exc}"
