"""Flat retrieval strategy — the original ScholarForge approach.

Top-N hub papers (by PageRank) get all chunks (deep read).
Remaining papers get first K chunks (shallow read). No LLM calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import select

from scholarforge.retrieve.strategies.base import RetrievalStrategy

if TYPE_CHECKING:
    from scholarforge.graph.metrics import GraphMetrics
    from scholarforge.store.models import PaperPlan


class FlatStrategy(RetrievalStrategy):
    """Top-N deep read + shallow rest. Simple, cheap, deterministic."""

    name = "flat"
    expensive = False
    description = "Top-N hub papers deep-read, rest shallow. No LLM calls."

    def retrieve(
        self,
        graph_metrics: GraphMetrics | None = None,
        plan: PaperPlan | None = None,  # noqa: ARG002
    ):
        from scholarforge.graph.metrics import compute_metrics
        from scholarforge.retrieve.context import RetrievedContext
        from scholarforge.store.db import get_session
        from scholarforge.store.models import Chunk, Paper

        metrics = graph_metrics or compute_metrics()

        deep_read_ids: set[str] = set()
        if metrics and metrics.hub_papers:
            deep_read_ids = set(metrics.hub_papers[: self.config.deep_read_limit])

        with get_session() as session:
            papers = session.exec(select(Paper)).all()
            chunks: list[Chunk] = []
            for paper in papers:
                paper_chunks = session.exec(
                    select(Chunk).where(Chunk.paper_id == paper.id).order_by(Chunk.chunk_index)
                ).all()
                if paper.id in deep_read_ids:
                    chunks.extend(paper_chunks)
                else:
                    chunks.extend(paper_chunks[: self.config.shallow_chunk_count])

        total = sum(c.token_count for c in chunks)
        return RetrievedContext(
            papers=papers,
            chunks=chunks,
            total_tokens=total,
            graph_metrics=metrics,
            strategy_name=self.name,
        )
