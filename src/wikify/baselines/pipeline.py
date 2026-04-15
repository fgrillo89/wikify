"""Consolidated baseline pipeline: retrieve-and-summarise with post-hoc citation.

Implements the baseline mode from study-design.md:
1. Topic discovery from KG source titles (no LLM call needed)
2. Retrieve chunks per topic via KG vector search
3. Build stub pages and add post-hoc document-level citations
4. Write results to bundle

This is the full baseline comparator for the autonomy study.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..citestore.graph import KnowledgeGraph
    from ..models import WikiPage
    from ..paths import BundlePaths


@dataclass
class BaselineConfig:
    """Configuration for the baseline pipeline."""

    top_k: int = 20
    max_topics: int = 40


def discover_topics(kg: KnowledgeGraph) -> list[str]:
    """Extract topic vocabulary from corpus source titles.

    Each corpus source contributes its title as a topic. Deduplicated
    and returned in corpus order.
    """
    sources = kg.sources(kind="corpus").collect()
    topics: list[str] = []
    seen: set[str] = set()
    for src in sources:
        title = src.get("title", "")
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        topics.append(title)
    return topics


def run_baseline(
    *,
    kg: KnowledgeGraph,
    bundle: BundlePaths,
    config: BaselineConfig | None = None,
) -> list[WikiPage]:
    """Run the full baseline pipeline.

    1. Discover topics from corpus source titles
    2. Retrieve chunks per topic and build stub pages (B1)
    3. Add post-hoc document-level citations (B2)
    4. Write pages to bundle
    """
    from .post_hoc_cite import add_post_hoc_citations
    from .retrieve_summarise import B1Config, build_b1_pages

    cfg = config or BaselineConfig()

    topics = discover_topics(kg)
    if cfg.max_topics:
        topics = topics[: cfg.max_topics]
    if not topics:
        return []

    b1_cfg = B1Config(top_k=cfg.top_k, max_topics=cfg.max_topics)
    pages = build_b1_pages(topics, kg, b1_cfg)
    pages = add_post_hoc_citations(pages, kg)

    _write_pages_to_bundle(pages, bundle)
    return pages


def _write_pages_to_bundle(pages: list[WikiPage], bundle: BundlePaths) -> None:
    """Persist baseline pages to the bundle index."""
    bundle.ensure()
    index = {}
    for p in pages:
        index[p.id] = {
            "id": p.id,
            "title": p.title,
            "kind": p.kind,
            "body_markdown": p.body_markdown,
            "evidence": [e.model_dump() if hasattr(e, "model_dump") else e for e in p.evidence],
        }
    index_path = bundle.root / "_index.json"
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
