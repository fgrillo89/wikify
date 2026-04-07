"""Topic-clustered retrieval strategy.

Groups papers by their declared/extracted topics, deep-reads the
highest-PageRank representative per cluster, and shallow-reads the rest.
No LLM calls.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from sqlmodel import select

from wikify.core.retrieve.strategies.base import RetrievalStrategy

if TYPE_CHECKING:
    from wikify.graph.metrics import GraphMetrics
    from wikify.store.models import PaperPlan


class TopicClusteredStrategy(RetrievalStrategy):
    """Group by topic, deep-read representative per cluster. No LLM calls."""

    name = "topic-cluster"
    expensive = False
    description = "Group papers by topic, deep-read the top paper per cluster."

    def retrieve(
        self,
        graph_metrics: GraphMetrics | None = None,
        plan: PaperPlan | None = None,  # noqa: ARG002
    ):
        from wikify.graph.metrics import compute_metrics
        from wikify.core.retrieve.context import RetrievedContext
        from wikify.store.db import get_session
        from wikify.store.models import Chunk, Paper, PaperTopic

        metrics = graph_metrics or compute_metrics()
        pagerank = metrics.pagerank if metrics else {}

        # Group papers by topic
        with get_session() as session:
            all_papers = {p.id: p for p in session.exec(select(Paper)).all()}
            paper_topics = session.exec(select(PaperTopic)).all()

        topic_papers: dict[str, list[str]] = defaultdict(list)
        for pt in paper_topics:
            topic_papers[pt.topic].append(pt.paper_id)

        # Pick the representative (highest PageRank) per topic
        deep_read_ids: set[str] = set()
        for _topic, pids in topic_papers.items():
            if not pids:
                continue
            representative = max(pids, key=lambda pid: pagerank.get(pid, 0.0))
            deep_read_ids.add(representative)

        # Load chunks
        with get_session() as session:
            papers = list(all_papers.values())
            chunks: list[Chunk] = []
            total_tokens = 0

            # Order papers: deep-read first (grouped by topic), then rest
            ordered_ids = list(deep_read_ids) + [
                pid for pid in all_papers if pid not in deep_read_ids
            ]

            for pid in ordered_ids:
                paper_chunks = session.exec(
                    select(Chunk).where(Chunk.paper_id == pid).order_by(Chunk.chunk_index)
                ).all()

                if pid in deep_read_ids:
                    selected = paper_chunks
                else:
                    selected = paper_chunks[: self.config.shallow_chunk_count]

                for c in selected:
                    if total_tokens + c.token_count > self.config.token_budget:
                        break
                    chunks.append(c)
                    total_tokens += c.token_count

        return RetrievedContext(
            papers=papers,
            chunks=chunks,
            total_tokens=total_tokens,
            graph_metrics=metrics,
            strategy_name=self.name,
        )
