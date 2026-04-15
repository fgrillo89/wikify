"""B1 baseline: retrieve-summarise.

For each topic in the corpus vocabulary, retrieve top-k chunks by
embedding similarity and generate a single-call encyclopedic article.
No evidence markers, no quote validation. Standard RAG-then-summarise.

This is the most common production pattern (Perplexity pages, NotebookLM).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..models import WikiPage

if TYPE_CHECKING:
    from ..citestore.graph import KnowledgeGraph


@dataclass
class B1Config:
    """Configuration for the B1 retrieve-summarise baseline."""

    top_k: int = 20
    max_topics: int | None = None  # None = all topics
    model_id: str = "M"


def load_topics(corpus_root: Path) -> list[str]:
    """Load topic vocabulary from corpus/topics.json."""
    topics_path = corpus_root / "topics.json"
    if not topics_path.exists():
        return []
    data = json.loads(topics_path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [str(t) for t in data]
    if isinstance(data, dict) and "topics" in data:
        return [str(t) for t in data["topics"]]
    return []


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

        # Build a context block for the writer
        context_lines = []
        for h in hits:
            source_id = h.get("source_id", "")
            chunk_id = h.get("id", "")
            # We store chunk_ids as provenance but don't require markers
            context_lines.append(f"[{source_id}] {chunk_id}")

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


def build_b1_prompts(
    pages: list[WikiPage],
    kg: KnowledgeGraph,
    config: B1Config | None = None,
) -> list[dict]:
    """Build writer prompts for B1 pages.

    Returns a list of {page_id, prompt} dicts. Each prompt contains the
    topic and retrieved passages for single-call summarisation.
    """
    cfg = config or B1Config()
    prompts: list[dict] = []

    for page in pages:
        hits = kg.search(page.title, top_k=cfg.top_k)
        passages = []
        for h in hits:
            source_id = h.get("source_id", "")
            # Chunk text is not in the KG node -- it's in the VectorStore.
            # The caller needs to look it up.
            passages.append({
                "chunk_id": h.get("id", ""),
                "source_id": source_id,
                "score": h.get("score", 0.0),
            })

        prompts.append({
            "page_id": page.id,
            "topic": page.title,
            "passages": passages,
            "instruction": (
                f"Write an encyclopedic article about '{page.title}' based on "
                f"the following {len(passages)} passages. Do not include citation "
                f"markers or footnotes. Write connected prose in Wikipedia voice."
            ),
        })

    return prompts
