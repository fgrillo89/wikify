"""Query-driven retrieval strategy — per-section ChromaDB queries.

Uses each section's heading and description as a query to retrieve the
most relevant papers and chunks for that specific section.  Produces
different context per section rather than a single global context.
No LLM calls.

Requires a PaperPlan (call after planning, or falls back to flat).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import select

from scholarforge.retrieve.strategies.base import RetrievalStrategy

if TYPE_CHECKING:
    from scholarforge.graph.metrics import GraphMetrics
    from scholarforge.store.models import PaperPlan


class QueryDrivenStrategy(RetrievalStrategy):
    """Per-section ChromaDB retrieval using section heading + description."""

    name = "query-driven"
    expensive = False
    description = "Per-section retrieval using section descriptions as queries."

    def retrieve(
        self,
        graph_metrics: GraphMetrics | None = None,
        plan: PaperPlan | None = None,
    ):
        from scholarforge.graph.metrics import compute_metrics
        from scholarforge.retrieve.context import RetrievedContext, SectionContext
        from scholarforge.store.db import get_session
        from scholarforge.store.embeddings import _get_collection, _get_model
        from scholarforge.store.models import Chunk, Paper

        metrics = graph_metrics or compute_metrics()

        if not plan:
            # No plan yet — fall back to flat
            from scholarforge.retrieve.strategies.flat import FlatStrategy

            return FlatStrategy(self.config).retrieve(graph_metrics=metrics)

        model = _get_model()
        collection = _get_collection()

        with get_session() as session:
            all_papers = {p.id: p for p in session.exec(select(Paper)).all()}

        section_contexts: dict[str, SectionContext] = {}
        all_chunks: list[Chunk] = []
        seen_chunk_ids: set[str] = set()
        total_tokens = 0

        # Flatten sections
        sections = _flatten_sections(plan.sections)
        per_section_budget = self.config.token_budget // max(len(sections), 1)

        for section in sections:
            query = f"{section.heading}: {section.description}"
            query_embedding = model.encode([query])[0]

            results = collection.query(
                query_embeddings=[query_embedding.tolist()],
                n_results=min(10, collection.count()),
                include=["distances"],
            )

            paper_ids = results["ids"][0] if results["ids"] else []
            sec_chunks: list[Chunk] = []
            sec_tokens = 0

            with get_session() as session:
                for i, pid in enumerate(paper_ids):
                    paper_chunks = session.exec(
                        select(Chunk).where(Chunk.paper_id == pid).order_by(Chunk.chunk_index)
                    ).all()

                    # Top match gets more chunks
                    limit = 5 if i == 0 else self.config.shallow_chunk_count
                    for c in paper_chunks[:limit]:
                        if sec_tokens + c.token_count > per_section_budget:
                            break
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


def _flatten_sections(sections):
    """Flatten nested section plans."""
    result = []
    for s in sections:
        result.append(s)
        if s.subsections:
            result.extend(_flatten_sections(s.subsections))
    return result
