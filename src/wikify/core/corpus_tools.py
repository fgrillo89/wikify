"""Corpus-level tool primitives shared by wiki and papers.

These functions expose corpus retrieval, paper digesting, and graph
metrics in clean Python types (dicts, dataclasses, ids) — no JSON
formatting, no reading-log side effects, no agent-specific concerns.
Both the wiki runtime and the papers agent surface call into this
module so the boundary rule (``wiki must not import from papers``)
holds.

The agent-facing JSON wrappers in ``wikify.papers.agent.tools`` are
expected to call these primitives and add their own formatting /
logging on top.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import select

from wikify.core.retrieve.context import retrieve_for_query
from wikify.graph.metrics import compute_metrics
from wikify.store.db import get_session
from wikify.store.models import Chunk, Paper

logger = logging.getLogger(__name__)


# ── Graph metrics ────────────────────────────────────────────────────────────


@dataclass
class CorpusGraphMetrics:
    """Compact view of corpus graph metrics keyed by paper id."""

    by_paper: dict[str, dict[str, Any]] = field(default_factory=dict)
    hub_ids: list[str] = field(default_factory=list)
    bridge_ids: list[str] = field(default_factory=list)
    frontier_ids: list[str] = field(default_factory=list)
    error: str | None = None


def compute_graph_metrics() -> CorpusGraphMetrics:
    """Compute corpus graph metrics in a wiki-friendly shape.

    Returns a ``CorpusGraphMetrics`` whose ``by_paper`` is the dict the
    wiki layer wants: ``paper_id -> {role, pagerank, betweenness, ...}``.
    On any failure, ``error`` is populated and the lists are empty —
    callers should check ``error`` before iterating.
    """

    try:
        metrics = compute_metrics()
    except Exception as exc:  # noqa: BLE001
        logger.warning("compute_graph_metrics: failed: %s", exc)
        return CorpusGraphMetrics(error=str(exc))

    by_paper: dict[str, dict[str, Any]] = {}
    for pid, pr in metrics.pagerank.items():
        by_paper[pid] = {
            "pagerank": pr,
            "degree_centrality": metrics.degree_centrality.get(pid, 0.0),
            "betweenness": metrics.betweenness_centrality.get(pid, 0.0),
            "role": metrics.paper_role(pid),
        }

    return CorpusGraphMetrics(
        by_paper=by_paper,
        hub_ids=list(metrics.hub_papers),
        bridge_ids=list(metrics.bridge_papers),
        frontier_ids=list(metrics.peripheral_papers),
    )


# ── Corpus search ────────────────────────────────────────────────────────────


@dataclass
class CorpusSearchResult:
    paper_ids: list[str]
    text: str
    total_papers: int
    total_chunks: int
    total_tokens: int


def search_corpus(
    query: str,
    *,
    top_k: int = 10,
    max_tokens: int = 8000,
) -> CorpusSearchResult:
    """Embedding-based corpus search returning paper ids + a text bundle."""

    ctx = retrieve_for_query(query, max_papers=top_k, max_tokens=max_tokens)
    paper_ids = [getattr(p, "id", None) or p["id"] for p in ctx.papers]
    paper_ids = [pid for pid in paper_ids if pid]
    return CorpusSearchResult(
        paper_ids=paper_ids,
        text=ctx.as_text(),
        total_papers=len(ctx.papers),
        total_chunks=len(ctx.chunks),
        total_tokens=ctx.total_tokens,
    )


# ── Paper digest ─────────────────────────────────────────────────────────────


def read_paper_digest_text(paper_id: str, *, max_chars: int = 3000) -> str:
    """Return a short markdown digest for one paper id.

    Pure Python; no JSON wrapping, no reading-log side effects. Used by
    both the wiki layer (mapreduce candidate filtering) and by the
    papers agent surface (which adds its own logging on top).
    """

    with get_session() as session:
        paper = session.get(Paper, paper_id)
        if paper is None:
            return f"No paper found with id {paper_id!r}"
        chunks = list(
            session.exec(
                select(Chunk)
                .where(Chunk.paper_id == paper.id)
                .order_by(Chunk.chunk_index)  # type: ignore[arg-type]
            ).all()
        )

    summaries_text = ""
    if getattr(paper, "section_summaries", None):
        try:
            summaries = json.loads(paper.section_summaries)
            if summaries:
                summaries_text = "\n".join(f"**{k}**: {v}" for k, v in summaries.items())
        except (json.JSONDecodeError, TypeError):
            pass

    body_parts: list[str] = []
    char_count = 0
    if not summaries_text:
        priority_sections = ["abstract", "introduction", "conclusion", "results", "discussion"]
        for chunk in chunks:
            section = (chunk.section_path or "").lower()
            if any(s in section for s in priority_sections) and char_count < max_chars:
                text = chunk.content[: max_chars - char_count]
                body_parts.append(f"[{chunk.section_path}] {text}")
                char_count += len(text)

    lines = [
        f"# {paper.title}",
        f"**Authors**: {', '.join(paper.parsed_authors)}",
        f"**Year**: {paper.year}",
        f"**DOI**: {paper.doi or 'N/A'}",
        f"**Display name**: {paper.display_name()}",
        "",
        "## Abstract",
        paper.summary or "",
    ]
    if summaries_text:
        lines.extend(["", "## Section Summaries", summaries_text])
    elif body_parts:
        lines.extend(["", "## Key Sections", "\n\n".join(body_parts)])
    return "\n".join(lines)


__all__ = [
    "CorpusGraphMetrics",
    "CorpusSearchResult",
    "compute_graph_metrics",
    "read_paper_digest_text",
    "search_corpus",
]
