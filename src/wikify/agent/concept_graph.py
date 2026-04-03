"""Concept graph — a knowledge-centric citation index built during exploration.

Unlike ResearchNotes (paper-centric: what did each paper find?), the concept
graph is knowledge-centric: how do concepts relate to each other, and which
papers back each relationship?

Structure:
    Nodes = concepts (materials, phenomena, metrics, methods)
    Edges = relationships backed by papers
    Edge labels = (relation_type, display_name)

Usage:
    - Built incrementally during record_paper_summary calls
    - Queryable via query_concept_graph(concept) -> neighbors + papers
    - Serializable to compact text for session context injection (~2-3KB)
    - Saved to disk per run, reloadable for follow-up outputs

Lifecycle:
    - Created per writing session (not per corpus)
    - Saved to data/output/{run_id}/concept_graph.json
    - Never stored in SQLite or ChromaDB (those are corpus data)
    - Reusable: load a prior graph to build on previous exploration
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from wikify.agent.run_context import get_current_run_context

logger = logging.getLogger(__name__)


@dataclass
class ConceptEdge:
    """A relationship between two concepts, backed by a paper."""

    source: str  # concept A
    target: str  # concept B
    relation: str  # "achieves", "causes", "enables", "contradicts", etc.
    paper: str  # display_name of the paper that establishes this link
    evidence: str = ""  # optional one-liner evidence (e.g., "10^4 cycles at 3V")


@dataclass
class ConceptGraph:
    """A graph of concept relationships built during corpus exploration."""

    edges: list[ConceptEdge] = field(default_factory=list)

    # Index for fast lookup (built lazily)
    _adjacency: dict[str, list[ConceptEdge]] | None = field(default=None, repr=False)

    def add_edge(
        self,
        source: str,
        target: str,
        relation: str,
        paper: str,
        evidence: str = "",
    ) -> None:
        """Add a concept relationship backed by a paper."""
        # Normalize concept names (lowercase, strip)
        source = source.strip().lower()
        target = target.strip().lower()
        self.edges.append(
            ConceptEdge(
                source=source,
                target=target,
                relation=relation,
                paper=paper,
                evidence=evidence,
            )
        )
        self._adjacency = None  # invalidate cache

    def add_from_summary(
        self,
        paper_name: str,
        concept_links: list[dict],
    ) -> int:
        """Add edges from a record_paper_summary call.

        Args:
            paper_name: The paper's display_name.
            concept_links: List of {"from": str, "to": str, "relation": str,
                "evidence": str} dicts extracted by the LLM.

        Returns:
            Number of edges added.
        """
        count = 0
        for link in concept_links:
            if "from" in link and "to" in link:
                self.add_edge(
                    source=link["from"],
                    target=link["to"],
                    relation=link.get("relation", "relates to"),
                    paper=paper_name,
                    evidence=link.get("evidence", ""),
                )
                count += 1
        return count

    def _build_adjacency(self) -> dict[str, list[ConceptEdge]]:
        """Build adjacency index for fast neighbor lookup."""
        adj: dict[str, list[ConceptEdge]] = {}
        for edge in self.edges:
            adj.setdefault(edge.source, []).append(edge)
            adj.setdefault(edge.target, []).append(edge)
        return adj

    @property
    def adjacency(self) -> dict[str, list[ConceptEdge]]:
        if self._adjacency is None:
            self._adjacency = self._build_adjacency()
        return self._adjacency

    def neighbors(self, concept: str) -> list[tuple[str, str, str, str]]:
        """Get all concepts connected to the given concept.

        Returns list of (neighbor, relation, paper, evidence) tuples.
        """
        concept = concept.strip().lower()
        results = []
        for edge in self.adjacency.get(concept, []):
            if edge.source == concept:
                results.append((edge.target, edge.relation, edge.paper, edge.evidence))
            else:
                results.append(
                    (edge.source, f"(reverse) {edge.relation}", edge.paper, edge.evidence)
                )
        return results

    def find_citation(self, concept: str) -> list[tuple[str, str]]:
        """Find papers that discuss a concept.

        Returns list of (display_name, evidence) tuples, deduplicated.
        """
        concept = concept.strip().lower()
        seen: set[str] = set()
        results: list[tuple[str, str]] = []
        for edge in self.adjacency.get(concept, []):
            if edge.paper not in seen:
                seen.add(edge.paper)
                results.append((edge.paper, edge.evidence))
        return results

    @property
    def concepts(self) -> list[str]:
        """All unique concepts in the graph."""
        nodes: set[str] = set()
        for edge in self.edges:
            nodes.add(edge.source)
            nodes.add(edge.target)
        return sorted(nodes)

    def to_compact_text(self) -> str:
        """Serialize as compact text for session context injection.

        Groups edges by paper for readability. ~50 bytes per edge.
        """
        if not self.edges:
            return "Concept graph: empty (no relationships recorded yet)"

        lines = [
            f"## Concept Graph ({len(self.edges)} relationships, {len(self.concepts)} concepts)",
            "",
        ]

        # Group by paper
        by_paper: dict[str, list[ConceptEdge]] = {}
        for edge in self.edges:
            by_paper.setdefault(edge.paper, []).append(edge)

        for paper, paper_edges in by_paper.items():
            lines.append(f"**{paper}**:")
            for e in paper_edges:
                ev = f" ({e.evidence})" if e.evidence else ""
                lines.append(f"  {e.source} --[{e.relation}]--> {e.target}{ev}")
            lines.append("")

        return "\n".join(lines)

    def to_json(self) -> str:
        """Serialize to JSON for disk persistence."""
        return json.dumps(
            [
                {
                    "source": e.source,
                    "target": e.target,
                    "relation": e.relation,
                    "paper": e.paper,
                    "evidence": e.evidence,
                }
                for e in self.edges
            ],
            indent=2,
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, json_str: str) -> ConceptGraph:
        """Load from JSON string."""
        data = json.loads(json_str)
        graph = cls()
        for item in data:
            graph.add_edge(
                source=item["source"],
                target=item["target"],
                relation=item.get("relation", "relates to"),
                paper=item["paper"],
                evidence=item.get("evidence", ""),
            )
        return graph

    def save(self, path: str | Path) -> Path:
        """Save to a JSON file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json(), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path) -> ConceptGraph:
        """Load from a JSON file."""
        p = Path(path)
        return cls.from_json(p.read_text(encoding="utf-8"))


# ── Module-level session graph ───────────────────────────────────────────────


def get_concept_graph() -> ConceptGraph:
    """Get the current run's concept graph."""
    return get_current_run_context().concept_graph


def reset_concept_graph() -> ConceptGraph:
    """Start a fresh concept graph for the current run."""
    ctx = get_current_run_context()
    ctx.concept_graph = ConceptGraph()
    return ctx.concept_graph
