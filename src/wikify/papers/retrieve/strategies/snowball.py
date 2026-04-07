"""Snowball retrieval strategy — BFS traversal from the top PageRank paper.

Starts from the highest-PageRank paper and follows citation/similarity
edges outward.  Papers closer to the seed get deep reads; distant papers
get shallow reads.  No LLM calls.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from sqlmodel import select

from wikify.papers.retrieve.strategies.base import RetrievalStrategy

if TYPE_CHECKING:
    from wikify.graph.metrics import GraphMetrics
    from wikify.store.models import PaperPlan


class SnowballStrategy(RetrievalStrategy):
    """BFS from top-PageRank paper along graph edges until token budget."""

    name = "snowball"
    expensive = False
    description = "BFS traversal from the top-PageRank paper. No LLM calls."

    def retrieve(
        self,
        graph_metrics: GraphMetrics | None = None,
        plan: PaperPlan | None = None,  # noqa: ARG002
    ):
        from wikify.graph.metrics import build_corpus_graph, compute_metrics
        from wikify.papers.retrieve.context import RetrievedContext
        from wikify.store.db import get_session
        from wikify.store.models import Chunk, Paper

        metrics = graph_metrics or compute_metrics()
        if not metrics or not metrics.hub_papers:
            # Fallback to flat
            from wikify.papers.retrieve.strategies.flat import FlatStrategy

            return FlatStrategy(self.config).retrieve(graph_metrics=metrics)

        graph = build_corpus_graph()
        seed = metrics.hub_papers[0]

        # BFS traversal — prioritise citation edges (weight 1.0)
        visited_order: list[str] = []
        visited: set[str] = set()
        queue: deque[str] = deque([seed])
        visited.add(seed)

        while queue:
            node = queue.popleft()
            visited_order.append(node)
            # Sort neighbors by edge weight descending (citations first)
            neighbors = sorted(
                graph.neighbors(node),
                key=lambda n: graph[node][n].get("weight", 0),
                reverse=True,
            )
            for nb in neighbors:
                if nb not in visited:
                    visited.add(nb)
                    queue.append(nb)

        # Load papers and chunks, deep-read early papers, shallow-read later
        with get_session() as session:
            all_papers = {p.id: p for p in session.exec(select(Paper)).all()}
            chunks: list[Chunk] = []
            papers: list[Paper] = []
            total_tokens = 0
            deep_budget = self.config.deep_read_limit

            for pid in visited_order:
                paper = all_papers.get(pid)
                if not paper:
                    continue
                papers.append(paper)

                paper_chunks = session.exec(
                    select(Chunk).where(Chunk.paper_id == pid).order_by(Chunk.chunk_index)
                ).all()

                if deep_budget > 0:
                    selected = paper_chunks  # Deep read
                    deep_budget -= 1
                else:
                    selected = paper_chunks[: self.config.shallow_chunk_count]

                for c in selected:
                    if total_tokens + c.token_count > self.config.token_budget:
                        break
                    chunks.append(c)
                    total_tokens += c.token_count

                if total_tokens >= self.config.token_budget:
                    break

            # Add remaining papers not yet visited (for reference)
            for pid, paper in all_papers.items():
                if pid not in visited:
                    papers.append(paper)

        return RetrievedContext(
            papers=papers,
            chunks=chunks,
            total_tokens=total_tokens,
            graph_metrics=metrics,
            strategy_name=self.name,
        )
