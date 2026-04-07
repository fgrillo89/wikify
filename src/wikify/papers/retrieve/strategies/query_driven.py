"""Query-driven retrieval strategy — per-section semantic chunk queries.

Uses each section's heading and description as a query to retrieve the
most semantically relevant chunks for that specific section.  Produces
different context per section rather than a single global context.
No LLM calls.

Requires a PaperPlan (call after planning, or falls back to flat).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import select

from wikify.papers.retrieve.strategies.base import RetrievalStrategy

if TYPE_CHECKING:
    from wikify.graph.metrics import GraphMetrics
    from wikify.store.models import PaperPlan


class QueryDrivenStrategy(RetrievalStrategy):
    """Per-section semantic chunk retrieval using section heading + description."""

    name = "query-driven"
    expensive = False
    description = "Per-section retrieval using section descriptions as semantic chunk queries."

    def retrieve(
        self,
        graph_metrics: GraphMetrics | None = None,
        plan: PaperPlan | None = None,
    ):
        from wikify.graph.metrics import compute_metrics
        from wikify.papers.retrieve.context import RetrievedContext, SectionContext
        from wikify.store.db import get_session
        from wikify.store.embeddings import _get_collection, _get_model, query_chunks
        from wikify.store.models import Chunk, Paper

        metrics = graph_metrics or compute_metrics()

        if not plan:
            from wikify.papers.retrieve.strategies.flat import FlatStrategy

            return FlatStrategy(self.config).retrieve(graph_metrics=metrics)

        model = _get_model()
        collection = _get_collection()

        with get_session() as session:
            all_papers = {p.id: p for p in session.exec(select(Paper)).all()}

        section_contexts: dict[str, SectionContext] = {}
        all_chunks: list[Chunk] = []
        seen_chunk_ids: set[str] = set()
        total_tokens = 0

        sections = plan.flat_sections()
        per_section_budget = self.config.token_budget // max(len(sections), 1)

        # Step 1: Find relevant papers via paper-level summary embeddings
        # (same as before — paper-level search is the right first filter)
        for section in sections:
            query = f"{section.heading}: {section.description}"
            query_embedding = model.encode([query])[0]

            paper_results = collection.query(
                query_embeddings=[query_embedding.tolist()],
                n_results=min(10, collection.count()),
                include=["distances"],
            )
            paper_ids = paper_results["ids"][0] if paper_results["ids"] else []

            if not paper_ids:
                section_contexts[section.heading] = SectionContext(
                    section_heading=section.heading,
                )
                continue

            # Step 2: Semantic chunk retrieval within matched papers
            # Estimate how many chunks we can fit in the budget (~150 tokens avg)
            max_chunks = max(per_section_budget // 150, 5)
            chunk_results = query_chunks(query, n_results=max_chunks, paper_ids=paper_ids)

            sec_chunks: list[Chunk] = []
            sec_tokens = 0

            if chunk_results:
                chunk_ids = [cid for cid, _ in chunk_results]
                with get_session() as session:
                    db_chunks = session.exec(
                        select(Chunk).where(Chunk.id.in_(chunk_ids))  # type: ignore[union-attr]
                    ).all()
                    chunks_by_id = {c.id: c for c in db_chunks}

                # Maintain similarity order from query_chunks
                for cid, _ in chunk_results:
                    c = chunks_by_id.get(cid)
                    if not c:
                        continue
                    if sec_tokens + c.token_count > per_section_budget:
                        continue  # skip oversized, try next
                    sec_chunks.append(c)
                    sec_tokens += c.token_count
                    if c.id not in seen_chunk_ids:
                        all_chunks.append(c)
                        seen_chunk_ids.add(c.id)
                        total_tokens += c.token_count

            section_contexts[section.heading] = SectionContext(
                section_heading=section.heading,
                chunks=sec_chunks,
                token_count=sec_tokens,
            )

        return RetrievedContext(
            papers=list(all_papers.values()),
            chunks=all_chunks,
            total_tokens=total_tokens,
            graph_metrics=metrics,
            section_contexts=section_contexts,
            strategy_name=self.name,
        )
