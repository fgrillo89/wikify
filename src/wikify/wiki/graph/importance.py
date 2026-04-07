"""Importance scoring and node-role classification.

Pure-graph operations: given a ``DiGraph`` produced by ``build.py``,
compute per-node importance and assign each node one of
``core | bridge | peripheral``. ``score_importance`` reads
``ConceptOccurrence`` from the store for source-diversity weighting; it
does not write anything back. ``classify_node_roles`` is fully in-memory.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict

import networkx as nx
from sqlmodel import select

from wikify.core.store.db import get_session
from wikify.core.store.models import ConceptEvidence, ConceptOccurrence, SourceCoverage

logger = logging.getLogger(__name__)


def score_importance(graph: nx.DiGraph) -> dict[str, float]:
    """Compute a blended importance score in [0, 1] for each node.

    ``raw = 0.5 * pagerank + 0.3 * degree_centrality + 0.2 * source_diversity``
    Source diversity is the number of distinct papers that mention the
    concept, normalised by the corpus maximum. Final scores are
    normalised to [0, 1] by the maximum raw value.
    """

    if graph.number_of_nodes() == 0:
        return {}

    node_ids = list(graph.nodes())

    if graph.number_of_edges() > 0:
        pagerank: dict[str, float] = nx.pagerank(graph, weight="weight")
    else:
        uniform = 1.0 / len(node_ids)
        pagerank = {n: uniform for n in node_ids}

    degree_cent: dict[str, float] = nx.degree_centrality(graph.to_undirected())

    source_counts: dict[str, int] = defaultdict(int)
    with get_session() as session:
        occurrence_rows = list(session.exec(select(ConceptOccurrence)).all())
        if occurrence_rows:
            occurrence_map: dict[str, set[str]] = defaultdict(set)
            for row in occurrence_rows:
                if row.concept_id in node_ids:
                    occurrence_map[row.concept_id].add(row.paper_id)
            for cid in node_ids:
                source_counts[cid] = len(occurrence_map.get(cid, set()))
        else:
            evidence_rows = list(session.exec(select(ConceptEvidence)).all())
            if evidence_rows:
                evidence_map: dict[str, set[str]] = defaultdict(set)
                for row in evidence_rows:
                    concept_id = getattr(row, "concept_id", "")
                    paper_id = getattr(row, "paper_id", "")
                    if concept_id in node_ids and paper_id:
                        evidence_map[concept_id].add(paper_id)
                for cid in node_ids:
                    source_counts[cid] = len(evidence_map.get(cid, set()))
            if not source_counts:
                for cid in node_ids:
                    n_sources = len(
                        list(
                            session.exec(
                                select(SourceCoverage).where(SourceCoverage.article_slug == cid)
                            ).all()
                        )
                    )
                    source_counts[cid] = n_sources

    max_sources = max(source_counts.values(), default=1) or 1
    source_diversity: dict[str, float] = {
        cid: source_counts[cid] / max_sources for cid in node_ids
    }

    raw: dict[str, float] = {}
    for cid in node_ids:
        raw[cid] = (
            0.5 * pagerank.get(cid, 0.0)
            + 0.3 * degree_cent.get(cid, 0.0)
            + 0.2 * source_diversity.get(cid, 0.0)
        )

    max_raw = max(raw.values(), default=1.0) or 1.0
    return {cid: raw[cid] / max_raw for cid in node_ids}


def classify_node_roles(
    graph: nx.DiGraph,
    scores: dict[str, float],
) -> dict[str, str]:
    """Classify each node as ``core`` / ``bridge`` / ``peripheral``.

    Rules (priority order):
    - ``core``: importance > 0.5 AND degree > median degree
    - ``bridge``: betweenness centrality > 75th percentile
    - ``peripheral``: everything else
    """

    if graph.number_of_nodes() == 0:
        return {}

    undirected = graph.to_undirected()
    node_ids = list(graph.nodes())

    degrees = [undirected.degree(n) for n in node_ids]
    median_degree = statistics.median(degrees) if degrees else 0.0

    if graph.number_of_edges() > 0:
        betweenness: dict[str, float] = nx.betweenness_centrality(undirected, weight="weight")
    else:
        betweenness = {n: 0.0 for n in node_ids}

    bc_values = sorted(betweenness.values())
    p75_index = int(len(bc_values) * 0.75)
    p75_threshold = bc_values[p75_index] if bc_values else 0.0

    roles: dict[str, str] = {}
    for cid in node_ids:
        node_degree = undirected.degree(cid)
        node_importance = scores.get(cid, 0.0)
        node_bc = betweenness.get(cid, 0.0)

        if node_importance > 0.5 and node_degree > median_degree:
            roles[cid] = "core"
        elif node_bc > p75_threshold:
            roles[cid] = "bridge"
        else:
            roles[cid] = "peripheral"

    return roles


__all__ = ["classify_node_roles", "score_importance"]
