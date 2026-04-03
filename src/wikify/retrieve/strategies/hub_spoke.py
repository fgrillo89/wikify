"""Hub-and-spoke retrieval strategy with parallel subagents.

Identifies hub papers from the graph, dispatches parallel subagents to
explore each hub's neighborhood, and assembles a context from their
dense syntheses and reading recommendations.

This is the only strategy that uses LLM calls (haiku by default).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlmodel import select

from wikify.retrieve.strategies.base import RetrievalStrategy

if TYPE_CHECKING:
    import networkx as nx

    from wikify.graph.metrics import GraphMetrics
    from wikify.retrieve.strategies.base import StrategyConfig
    from wikify.store.models import Chunk, Paper, PaperPlan

logger = logging.getLogger(__name__)

# ── Subagent synthesis prompt ────────────────────────────────────────────────

_SYNTHESIS_PROMPT = """\
You are a research subagent exploring a hub paper and its citation neighborhood.

Hub paper: {hub_title} ({hub_authors}, {hub_year})

Your task: read the excerpts below and produce a DENSE synthesis (200-300 words max) \
structured as follows:

1. **Hypothesis → Test → Result**: What did the hub paper claim, how was it tested, \
what was found?
2. **State of the art**: What does this hub and its neighbors establish as current \
knowledge?
3. **Pitfalls**: Limitations, contradictions, failed approaches, or unresolved issues.
4. **Conclusions**: Key takeaways and open questions.
5. **Reading recommendations**: List which papers from the excerpts the master agent \
should:
   - READ IN FULL (essential, high-value papers)
   - SKIM (useful context but not critical)
   - SKIP (redundant or peripheral)

{focus_instruction}

Be extremely concise and information-dense. No filler.

--- Excerpts ---
{excerpts}
"""


@dataclass
class HubTraversalResult:
    """Result from a single subagent's hub exploration."""

    hub_id: str
    hub_paper: Paper
    synthesis: str
    papers_visited: list[str] = field(default_factory=list)
    recommended_deep_read: list[str] = field(default_factory=list)
    recommended_skip: list[str] = field(default_factory=list)
    chunks_collected: list[Chunk] = field(default_factory=list)
    llm_calls: int = 0


class HubAndSpokeStrategy(RetrievalStrategy):
    """Parallel subagents per hub, each traverses neighbors and synthesizes."""

    name = "hub-spoke"
    expensive = True
    description = (
        "Parallel subagents explore hub neighborhoods, synthesize findings, "
        "and recommend papers to deep-read. Uses haiku LLM calls."
    )

    def retrieve(
        self,
        graph_metrics: GraphMetrics | None = None,
        plan: PaperPlan | None = None,  # noqa: ARG002
    ):
        from wikify.graph.metrics import build_corpus_graph, compute_metrics
        from wikify.retrieve.context import RetrievedContext, SectionContext
        from wikify.store.db import get_session
        from wikify.store.models import Chunk, Paper

        metrics = graph_metrics or compute_metrics()
        if not metrics or not metrics.hub_papers:
            from wikify.retrieve.strategies.flat import FlatStrategy

            return FlatStrategy(self.config).retrieve(graph_metrics=metrics)

        graph = build_corpus_graph()
        hub_ids = metrics.hub_papers[: self.config.deep_read_limit + 1]

        # Load all papers for lookup
        with get_session() as session:
            all_papers = {p.id: p for p in session.exec(select(Paper)).all()}

        # Dispatch subagents in parallel
        results: list[HubTraversalResult] = []
        with ThreadPoolExecutor(max_workers=self.config.parallel_workers) as pool:
            futures = {
                pool.submit(
                    _agent_traverse_hub,
                    hub_id=hid,
                    graph=graph,
                    all_papers=all_papers,
                    config=self.config,
                ): hid
                for hid in hub_ids
                if hid in all_papers
            }
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception:
                    logger.warning("Subagent failed for hub %s", futures[future], exc_info=True)

        # Master: collect syntheses and decide which papers to deep-read
        deep_read_ids: set[str] = set()
        for r in results:
            deep_read_ids.add(r.hub_id)
            deep_read_ids.update(r.recommended_deep_read)

        # Build final context
        with get_session() as session:
            chunks: list[Chunk] = []
            total_tokens = 0

            for pid in list(deep_read_ids):
                paper_chunks = session.exec(
                    select(Chunk).where(Chunk.paper_id == pid).order_by(Chunk.chunk_index)
                ).all()
                for c in paper_chunks:
                    if total_tokens + c.token_count > self.config.token_budget:
                        break
                    chunks.append(c)
                    total_tokens += c.token_count

            # Shallow-read remaining papers
            for pid in all_papers:
                if pid in deep_read_ids:
                    continue
                paper_chunks = session.exec(
                    select(Chunk).where(Chunk.paper_id == pid).order_by(Chunk.chunk_index)
                ).all()
                for c in paper_chunks[: self.config.shallow_chunk_count]:
                    if total_tokens + c.token_count > self.config.token_budget:
                        break
                    chunks.append(c)
                    total_tokens += c.token_count

        # Build a synthesis section context that the writer can use
        combined_synthesis = "\n\n".join(
            f"### Hub: {r.hub_paper.display_name()}\n{r.synthesis}" for r in results
        )
        section_contexts = {
            "_hub_syntheses": SectionContext(
                section_heading="_hub_syntheses",
                synthesis_notes=combined_synthesis,
            )
        }

        return RetrievedContext(
            papers=list(all_papers.values()),
            chunks=chunks,
            total_tokens=total_tokens,
            graph_metrics=metrics,
            section_contexts=section_contexts,
            strategy_name=self.name,
        )

    def estimate_cost(self) -> dict[str, float]:
        n_hubs = self.config.deep_read_limit + 1
        # Haiku: ~$0.25/MTok input, ~$1.25/MTok output
        # Each subagent: ~2K input tokens, ~400 output tokens
        est_usd = n_hubs * (2000 * 0.00000025 + 400 * 0.00000125)
        return {"llm_calls": n_hubs, "est_usd": est_usd}


def _agent_traverse_hub(
    hub_id: str,
    graph: nx.DiGraph,
    all_papers: dict[str, Paper],
    config: StrategyConfig,
) -> HubTraversalResult:
    """Single subagent: deep-read hub, traverse neighbors, synthesize."""
    from wikify.store.db import get_session
    from wikify.store.models import Chunk

    hub_paper = all_papers[hub_id]

    # Traverse neighbors up to depth limit
    visited: list[str] = [hub_id]
    frontier = {hub_id}
    for _depth in range(config.max_traversal_depth):
        next_frontier: set[str] = set()
        for node in frontier:
            if node in graph:
                for nb in graph.neighbors(node):
                    if nb not in set(visited):
                        next_frontier.add(nb)
                        visited.append(nb)
        frontier = next_frontier
        if not frontier:
            break

    # Collect excerpts from visited papers
    excerpts_parts: list[str] = []
    with get_session() as session:
        all_chunks: list[Chunk] = []
        for pid in visited:
            paper = all_papers.get(pid)
            if not paper:
                continue
            paper_chunks = session.exec(
                select(Chunk).where(Chunk.paper_id == pid).order_by(Chunk.chunk_index)
            ).all()
            # Hub gets all chunks, neighbors get first 3
            selected = paper_chunks if pid == hub_id else paper_chunks[:3]
            all_chunks.extend(selected)
            text = "\n".join(c.content for c in selected)
            excerpts_parts.append(f"[{paper.display_name()}]\n{text[:1500]}")

    excerpts = "\n\n---\n\n".join(excerpts_parts)

    # Build focus instruction from user's prompt
    focus = ""
    if config.user_focus:
        focus = (
            f"The user is particularly interested in: {config.user_focus}. Prioritize this angle."
        )

    # Call LLM for synthesis
    from wikify.llm.client import complete

    prompt = _SYNTHESIS_PROMPT.format(
        hub_title=hub_paper.title,
        hub_authors=", ".join(hub_paper.parsed_authors[:3]),
        hub_year=hub_paper.year or "?",
        focus_instruction=focus,
        excerpts=excerpts[:6000],
    )

    synthesis = complete(
        messages=[{"role": "user", "content": prompt}],
        model=config.model_for_synthesis,
        temperature=0.2,
        max_tokens=1024,
    )

    # Parse reading recommendations from synthesis
    recommended_deep = []
    recommended_skip = []
    for pid in visited[1:]:  # exclude hub itself
        paper = all_papers.get(pid)
        if not paper:
            continue
        name = paper.display_name().lower()
        if "read in full" in synthesis.lower() and name in synthesis.lower():
            recommended_deep.append(pid)
        elif "skip" in synthesis.lower() and name in synthesis.lower():
            recommended_skip.append(pid)

    return HubTraversalResult(
        hub_id=hub_id,
        hub_paper=hub_paper,
        synthesis=synthesis,
        papers_visited=visited,
        recommended_deep_read=recommended_deep,
        recommended_skip=recommended_skip,
        chunks_collected=all_chunks,
        llm_calls=1,
    )
