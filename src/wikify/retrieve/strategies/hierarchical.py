"""Hierarchical retrieval strategy -- three-level cascade.

Inspired by PageIndex's tree navigation, but using embeddings at each
level rather than LLM-driven tree search (cheaper, deterministic).

For each section in the PaperPlan:
1. Paper-level: query document_summaries -> top 10 papers
2. Section-level: query section_summaries (scoped to those papers) -> top 5 sections
3. Chunk-level: query chunk_embeddings (scoped to those sections) -> fill budget

Falls back gracefully when section summaries are unavailable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlmodel import select

from wikify.retrieve.strategies.base import RetrievalStrategy

if TYPE_CHECKING:
    from wikify.graph.metrics import GraphMetrics
    from wikify.store.models import PaperPlan


class HierarchicalStrategy(RetrievalStrategy):
    """Three-level retrieval: paper -> section -> chunk."""

    name = "hierarchical"
    expensive = False
    description = "Three-level cascade: paper summaries -> section summaries -> chunks."

    def retrieve(
        self,
        graph_metrics: GraphMetrics | None = None,
        plan: PaperPlan | None = None,
    ):
        from wikify.graph.metrics import compute_metrics
        from wikify.retrieve.context import RetrievedContext, SectionContext
        from wikify.store.db import get_session
        from wikify.store.embeddings import (
            _get_collection,
            _get_model,
            query_chunks,
            query_sections,
        )
        from wikify.store.models import Chunk, Paper

        metrics = graph_metrics or compute_metrics()

        if not plan:
            from wikify.retrieve.strategies.flat import FlatStrategy

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

        for section in sections:
            query = f"{section.heading}: {section.description}"

            # Level 1: Paper-level search
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

            # Level 2: Section-level search (scoped to matched papers)
            section_results = query_sections(query, n_results=5, paper_ids=paper_ids)

            # Level 3: Chunk-level search
            sec_chunks: list[Chunk] = []
            sec_tokens = 0

            if section_results:
                # Narrow to papers+sections found at level 2
                target_papers = list({pid for pid, _, _ in section_results})
                target_sections = {(pid, spath) for pid, spath, _ in section_results}

                # Query chunks scoped to the papers found at section level
                max_chunks = max(per_section_budget // 150, 5)
                chunk_results = query_chunks(query, n_results=max_chunks, paper_ids=target_papers)

                if chunk_results:
                    chunk_ids = [cid for cid, _ in chunk_results]
                    with get_session() as session:
                        db_chunks = session.exec(
                            select(Chunk).where(
                                Chunk.id.in_(chunk_ids)  # type: ignore[union-attr]
                            )
                        ).all()
                        chunks_by_id = {c.id: c for c in db_chunks}

                    # Prefer chunks from matched sections, then any relevant chunks
                    prioritized = []
                    other = []
                    for cid, dist in chunk_results:
                        c = chunks_by_id.get(cid)
                        if not c:
                            continue
                        if (c.paper_id, c.section_path) in target_sections:
                            prioritized.append((c, dist))
                        else:
                            other.append((c, dist))

                    for c, _ in prioritized + other:
                        if sec_tokens + c.token_count > per_section_budget:
                            continue
                        sec_chunks.append(c)
                        sec_tokens += c.token_count
                        if c.id not in seen_chunk_ids:
                            all_chunks.append(c)
                            seen_chunk_ids.add(c.id)
                            total_tokens += c.token_count
            else:
                # Fallback: no section summaries available, go paper -> chunk directly
                max_chunks = max(per_section_budget // 150, 5)
                chunk_results = query_chunks(query, n_results=max_chunks, paper_ids=paper_ids)
                if chunk_results:
                    chunk_ids = [cid for cid, _ in chunk_results]
                    with get_session() as session:
                        db_chunks = session.exec(
                            select(Chunk).where(
                                Chunk.id.in_(chunk_ids)  # type: ignore[union-attr]
                            )
                        ).all()
                        chunks_by_id = {c.id: c for c in db_chunks}

                    for cid, _ in chunk_results:
                        c = chunks_by_id.get(cid)
                        if not c:
                            continue
                        if sec_tokens + c.token_count > per_section_budget:
                            continue
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
