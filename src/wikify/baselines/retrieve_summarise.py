"""B1 baseline: retrieve-summarise.

For each topic in the corpus vocabulary, retrieve top-k chunks by
embedding similarity and generate a single-call encyclopedic article.
No evidence markers, no quote validation. Standard RAG-then-summarise.

This is the most common production pattern (Perplexity pages, NotebookLM).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models import WikiPage

if TYPE_CHECKING:
    from ..citestore.graph import KnowledgeGraph


@dataclass
class B1Config:
    """Configuration for the B1 retrieve-summarise baseline."""

    top_k: int = 20
    max_topics: int | None = None  # None = all topics


def build_b1_pages(
    topics: list[str],
    kg: KnowledgeGraph,
    config: B1Config | None = None,
) -> list[WikiPage]:
    """Build stub WikiPages from topic retrieval.

    Each page has the topic as title and the retrieved chunks as evidence
    context (but no evidence markers -- the writer produces plain prose).
    The actual LLM call happens in the writer; this function prepares
    the input.
    """
    cfg = config or B1Config()
    pages: list[WikiPage] = []
    cap = cfg.max_topics or len(topics)

    for topic in topics[:cap]:
        hits = kg.search(topic, top_k=cfg.top_k)
        if not hits:
            continue

        pages.append(WikiPage(
            id=topic,
            kind="article",
            title=topic,
            aliases=[],
            body_markdown="",  # Writer fills this
            evidence=[],       # No evidence markers for B1
            provenance={"condition": "B1", "top_k": cfg.top_k},
        ))

    return pages
