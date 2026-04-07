"""Concept co-occurrence graph, importance scoring, and role classification.

Implements Pass 2 of the Wikipedia/epoch pipeline:

    build_concept_graph  -- Load ConceptRecords + ConceptOccurrence from DB,
                            build a weighted directed graph of co-occurrences.
    score_importance     -- PageRank + degree centrality + source diversity,
                            normalised to [0, 1].
    classify_node_roles  -- Assign "core" | "bridge" | "peripheral" to each node.
    detect_communities   -- Louvain community detection for auto-domain discovery.
    extract_relations    -- Derive ConceptRelation rows from graph edges.
    update_concept_importance -- Persist importance scores back to ConceptRecord.
    save_relations       -- Atomic replace of ConceptRelation rows for an epoch.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict

import networkx as nx
import numpy as np
from sqlmodel import select

from wikify.core.store.db import get_session
from wikify.core.store.models import (
    ConceptEvidence,
    ConceptOccurrence,
    ConceptRecord,
    ConceptRelation,
    RelationEvidence,
    SourceCoverage,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pass 2 — graph construction
# ---------------------------------------------------------------------------


def build_concept_graph(domain: str, epoch: int) -> nx.DiGraph:
    """Build a co-occurrence graph for all ConceptRecords in a domain.

    Nodes are concept slugs (ConceptRecord.id). An edge (A, B) exists when
    concepts A and B were both observed in the same source via
    ConceptOccurrence (preferred), ConceptEvidence (fallback), or legacy
    SourceCoverage rows. RelationEvidence adds extra weight to direct edges.

    Node attributes stored:
        name          -- ConceptRecord.name
        concept_type  -- ConceptRecord.concept_type
        domain        -- ConceptRecord.domain
        article_status -- ConceptRecord.article_status

    Args:
        domain: Domain filter (e.g. "material_science").  Pass "" to include
                all domains.
        epoch:  Current epoch number (stored on ConceptRelation rows later).

    Returns:
        Directed graph.  Edges are bidirectional (A->B and B->A) with the same
        weight so PageRank treats the relationship symmetrically; the caller may
        choose direction later when creating ConceptRelation rows.
    """
    graph = nx.DiGraph()

    # ── Load concepts ────────────────────────────────────────────────────────
    with get_session() as session:
        query = select(ConceptRecord)
        if domain:
            query = query.where(ConceptRecord.domain == domain)
        concepts: list[ConceptRecord] = list(session.exec(query).all())

    if not concepts:
        logger.info("build_concept_graph: no concepts for domain=%r epoch=%d", domain, epoch)
        return graph

    for c in concepts:
        graph.add_node(
            c.id,
            name=c.name,
            concept_type=c.concept_type,
            domain=c.domain,
            article_status=c.article_status,
        )

    concept_ids = {c.id for c in concepts}

    # ── Build co-occurrence counts via SourceCoverage ────────────────────────
    # source_id -> set of concept slugs extracted from that source
    source_to_concepts: dict[str, set[str]] = defaultdict(set)

    with get_session() as session:
        occurrence_rows = list(session.exec(select(ConceptOccurrence)).all())
        if occurrence_rows:
            for row in occurrence_rows:
                if row.concept_id in concept_ids:
                    source_to_concepts[row.paper_id].add(row.concept_id)
        else:
            evidence_rows = list(session.exec(select(ConceptEvidence)).all())
            if evidence_rows:
                for row in evidence_rows:
                    concept_id = getattr(row, "concept_id", "")
                    paper_id = getattr(row, "paper_id", "")
                    if concept_id in concept_ids and paper_id:
                        source_to_concepts[paper_id].add(concept_id)
        if not source_to_concepts and domain:
            coverage_rows = list(
                session.exec(select(SourceCoverage).where(SourceCoverage.domain == domain)).all()
            )
            for row in coverage_rows:
                if row.article_slug in concept_ids:
                    source_to_concepts[row.source_id].add(row.article_slug)
        elif not source_to_concepts:
            coverage_rows = list(session.exec(select(SourceCoverage)).all())
            for row in coverage_rows:
                if row.article_slug in concept_ids:
                    source_to_concepts[row.source_id].add(row.article_slug)

    # co-occurrence weight: number of shared sources
    cooccurrence: dict[tuple[str, str], int] = defaultdict(int)
    for concepts_in_source in source_to_concepts.values():
        concepts_list = sorted(concepts_in_source)
        for i, a in enumerate(concepts_list):
            for b in concepts_list[i + 1 :]:
                cooccurrence[(a, b)] += 1

    relation_weights: dict[tuple[str, str], float] = defaultdict(float)
    with get_session() as session:
        relation_rows = list(session.exec(select(RelationEvidence)).all())
    for row in relation_rows:
        source_concept = getattr(row, "source_concept", "")
        target_concept = getattr(row, "target_concept", "")
        weight = getattr(row, "weight", 1.0)
        if source_concept in concept_ids and target_concept in concept_ids:
            relation_weights[(source_concept, target_concept)] += max(weight, 1.0)

    # Add edges in both directions with equal weight
    for (a, b), weight in cooccurrence.items():
        if a in concept_ids and b in concept_ids:
            graph.add_edge(a, b, weight=float(weight) + relation_weights.get((a, b), 0.0))
            graph.add_edge(b, a, weight=float(weight) + relation_weights.get((b, a), 0.0))

    for (a, b), weight in relation_weights.items():
        if a in concept_ids and b in concept_ids and not graph.has_edge(a, b):
            graph.add_edge(a, b, weight=weight)

    logger.info(
        "build_concept_graph: domain=%r epoch=%d nodes=%d edges=%d",
        domain,
        epoch,
        graph.number_of_nodes(),
        graph.number_of_edges(),
    )
    return graph


# ---------------------------------------------------------------------------
# Pass 2 — importance scoring
# ---------------------------------------------------------------------------


def score_importance(graph: nx.DiGraph) -> dict[str, float]:
    """Compute a blended importance score for each node in [0, 1].

    Formula:
        raw = 0.5 * pagerank + 0.3 * degree_centrality + 0.2 * source_diversity

    where source_diversity is the number of unique source_ids that mention this
    concept (from ConceptOccurrence when available), normalised by the maximum
    across all concepts.

    Final scores are normalised to [0, 1] by dividing by the maximum raw score.

    Args:
        graph: DiGraph returned by :func:`build_concept_graph`.

    Returns:
        Mapping of concept_id -> importance score.  Empty dict if the graph
        has no nodes.
    """
    if graph.number_of_nodes() == 0:
        return {}

    node_ids = list(graph.nodes())

    # ── PageRank ─────────────────────────────────────────────────────────────
    if graph.number_of_edges() > 0:
        pagerank: dict[str, float] = nx.pagerank(graph, weight="weight")
    else:
        # Uniform if no edges
        uniform = 1.0 / len(node_ids)
        pagerank = {n: uniform for n in node_ids}

    # ── Degree centrality (undirected view) ──────────────────────────────────
    degree_cent: dict[str, float] = nx.degree_centrality(graph.to_undirected())

    # ── Source diversity: unique source_ids per concept ──────────────────────
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
    source_diversity: dict[str, float] = {cid: source_counts[cid] / max_sources for cid in node_ids}

    # ── Blend ────────────────────────────────────────────────────────────────
    raw: dict[str, float] = {}
    for cid in node_ids:
        raw[cid] = (
            0.5 * pagerank.get(cid, 0.0)
            + 0.3 * degree_cent.get(cid, 0.0)
            + 0.2 * source_diversity.get(cid, 0.0)
        )

    max_raw = max(raw.values(), default=1.0) or 1.0
    scores: dict[str, float] = {cid: raw[cid] / max_raw for cid in node_ids}
    return scores


# ---------------------------------------------------------------------------
# Pass 2 — role classification
# ---------------------------------------------------------------------------


def classify_node_roles(
    graph: nx.DiGraph,
    scores: dict[str, float],
) -> dict[str, str]:
    """Classify each node as "core", "bridge", or "peripheral".

    Rules (applied in priority order):
        core       -- importance > 0.5 AND degree > median degree
        bridge     -- betweenness centrality > 75th percentile
        peripheral -- everything else

    Args:
        graph:  DiGraph returned by :func:`build_concept_graph`.
        scores: Importance scores from :func:`score_importance`.

    Returns:
        Mapping of concept_id -> role string.  Empty dict if graph is empty.
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


# ---------------------------------------------------------------------------
# Pass 2 — community detection
# ---------------------------------------------------------------------------


def detect_communities(graph: nx.DiGraph) -> dict[str, int]:
    """Detect communities via Louvain algorithm.

    Uses :func:`networkx.community.louvain_communities` on the undirected
    projection of the graph.  Each community is assigned an integer index
    (0-based in descending size order).

    Args:
        graph: DiGraph returned by :func:`build_concept_graph`.

    Returns:
        Mapping of concept_id -> community_index.  Empty dict if graph is empty.
    """
    if graph.number_of_nodes() == 0:
        return {}

    undirected = graph.to_undirected()

    # louvain_communities requires at least one edge to be meaningful
    if undirected.number_of_edges() == 0:
        # All isolated nodes go into community 0
        return {n: 0 for n in graph.nodes()}

    community_sets: list[set] = list(
        nx.community.louvain_communities(undirected, weight="weight", seed=42)
    )

    # Sort communities largest-first so index 0 is the most populous
    community_sets.sort(key=len, reverse=True)

    membership: dict[str, int] = {}
    for idx, members in enumerate(community_sets):
        for node in members:
            membership[node] = idx

    logger.info(
        "detect_communities: %d communities from %d nodes",
        len(community_sets),
        graph.number_of_nodes(),
    )
    return membership


# ---------------------------------------------------------------------------
# Pass 2 — relation extraction
# ---------------------------------------------------------------------------

# Maps (source_type, target_type) pairs to a relation_type string.
# Both keys are concept_type values from ConceptRecord.
_RELATION_MAP: dict[tuple[str, str], str] = {
    ("method", "material"): "USED-IN",
    ("technique", "material"): "USED-IN",
    ("method", "dataset"): "USED-IN",
    ("technique", "dataset"): "USED-IN",
    ("theory", "phenomenon"): "ENABLES",
    ("theory", "method"): "ENABLES",
    ("theory", "technique"): "ENABLES",
}


def _infer_relation_type(src_type: str, tgt_type: str) -> str:
    """Infer a relation type from a pair of concept_type strings.

    Checks the directed pair first, then the reversed pair, then falls back
    to "RELATED-TO".
    """
    direct = _RELATION_MAP.get((src_type, tgt_type))
    if direct:
        return direct
    return "RELATED-TO"


def extract_relations(graph: nx.DiGraph, epoch: int) -> list[ConceptRelation]:
    """Derive ConceptRelation rows from graph edges.

    Each directed edge (u, v) with weight w becomes one ConceptRelation.
    The relation_type is inferred from the concept_type attributes of the
    source and target nodes.

    Args:
        graph: DiGraph returned by :func:`build_concept_graph`.
        epoch: Current epoch number stored on each relation.

    Returns:
        List of (unsaved) ConceptRelation instances.
    """
    relations: list[ConceptRelation] = []
    for src, tgt, edge_data in graph.edges(data=True):
        src_type: str = graph.nodes[src].get("concept_type", "")
        tgt_type: str = graph.nodes[tgt].get("concept_type", "")

        # Same type -> RELATED-TO (covers identical-type pairs)
        if src_type and tgt_type and src_type == tgt_type:
            rel_type = "RELATED-TO"
        else:
            rel_type = _infer_relation_type(src_type, tgt_type)

        relations.append(
            ConceptRelation(
                source_concept=src,
                target_concept=tgt,
                relation_type=rel_type,
                weight=float(edge_data.get("weight", 1.0)),
                epoch=epoch,
            )
        )
    return relations


# ---------------------------------------------------------------------------
# Pass 2 — DB persistence helpers
# ---------------------------------------------------------------------------


def update_concept_importance(scores: dict[str, float]) -> None:
    """Write importance scores back to ConceptRecord rows in the DB.

    Only updates concepts whose id appears in *scores*.  Commits in a single
    transaction.

    Args:
        scores: Mapping of concept_id -> importance score (0-1).
    """
    if not scores:
        return

    with get_session() as session:
        for cid, value in scores.items():
            results = list(session.exec(select(ConceptRecord).where(ConceptRecord.id == cid)).all())
            if results:
                record = results[0]
                record.importance = value
                session.add(record)
        session.commit()

    logger.info("update_concept_importance: updated %d concepts", len(scores))


def save_relations(relations: list[ConceptRelation], epoch: int) -> int:
    """Atomically replace ConceptRelation rows for a given epoch.

    Deletes all existing rows for *epoch*, then bulk-inserts *relations*.

    Args:
        relations: List of ConceptRelation instances to persist.
        epoch:     Epoch number (used for the delete filter).

    Returns:
        Number of rows inserted.
    """
    if not relations:
        logger.info("save_relations: no relations to save for epoch=%d", epoch)
        return 0

    with get_session() as session:
        # Delete existing rows for this epoch
        existing = list(
            session.exec(select(ConceptRelation).where(ConceptRelation.epoch == epoch)).all()
        )
        for row in existing:
            session.delete(row)
        session.flush()

        # Bulk insert
        for rel in relations:
            session.add(rel)
        session.commit()

    logger.info(
        "save_relations: epoch=%d deleted=%d inserted=%d",
        epoch,
        len(existing),
        len(relations),
    )
    return len(relations)


# ---------------------------------------------------------------------------
# Topology metrics
# ---------------------------------------------------------------------------


def compute_modularity(graph: nx.DiGraph, communities: dict[str, int]) -> float:
    """Compute the modularity score of a community partition.

    Converts the flat concept_id -> community_index mapping into the list-of-sets
    format expected by NetworkX, then delegates to
    :func:`networkx.community.modularity` on the undirected projection.

    Args:
        graph:       DiGraph returned by :func:`build_concept_graph`.
        communities: Mapping of concept_id -> community_index from
                     :func:`detect_communities`.

    Returns:
        Modularity score in roughly [0, 1].  Returns 0.0 for empty graphs or
        when no edges exist.
    """
    if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
        return 0.0

    # Build list[set[str]] indexed by community index
    index_to_members: dict[int, set[str]] = defaultdict(set)
    for node, idx in communities.items():
        index_to_members[idx].add(node)
    community_sets: list[set[str]] = list(index_to_members.values())

    if not community_sets:
        return 0.0

    score: float = nx.community.modularity(graph.to_undirected(), community_sets)
    logger.debug("compute_modularity: %.4f from %d communities", score, len(community_sets))
    return score


def compute_inter_community_edge_ratio(
    graph: nx.DiGraph,
    communities: dict[str, int],
) -> float:
    """Return the fraction of edges that cross community boundaries.

    An edge (u, v) is inter-community when u and v belong to different
    community indices.

    Args:
        graph:       DiGraph returned by :func:`build_concept_graph`.
        communities: Mapping of concept_id -> community_index from
                     :func:`detect_communities`.

    Returns:
        Ratio in [0, 1].  Returns 0.0 when there are no edges.
    """
    total_edges = graph.number_of_edges()
    if total_edges == 0:
        return 0.0

    inter = sum(1 for src, tgt in graph.edges() if communities.get(src) != communities.get(tgt))
    ratio = inter / total_edges
    logger.debug(
        "compute_inter_community_edge_ratio: %d/%d inter-community edges (%.4f)",
        inter,
        total_edges,
        ratio,
    )
    return ratio


def compute_bridge_density(roles: dict[str, str]) -> float:
    """Return the proportion of nodes classified as "bridge".

    Args:
        roles: Mapping of concept_id -> role string from
               :func:`classify_node_roles`.

    Returns:
        Bridge density in [0, 1].  Returns 0.0 for empty role maps.
    """
    if not roles:
        return 0.0

    bridge_count = sum(1 for role in roles.values() if role == "bridge")
    density = bridge_count / len(roles)
    logger.debug(
        "compute_bridge_density: %d/%d bridge nodes (%.4f)",
        bridge_count,
        len(roles),
        density,
    )
    return density


def compute_community_gini(communities: dict[str, int]) -> float:
    """Compute the Gini coefficient of community sizes.

    A Gini of 0 means all communities are equally sized; a Gini near 1 means
    nearly all concepts belong to a single community.

    Formula (1-indexed sort):
        G = (2 * sum(i * x_i)) / (n * sum(x_i)) - (n + 1) / n

    Args:
        communities: Mapping of concept_id -> community_index from
                     :func:`detect_communities`.

    Returns:
        Gini coefficient in [0, 1].  Returns 0.0 when there are 0 or 1
        distinct communities.
    """
    if not communities:
        return 0.0

    size_counter: dict[int, int] = defaultdict(int)
    for idx in communities.values():
        size_counter[idx] += 1

    n_communities = len(size_counter)
    if n_communities <= 1:
        return 0.0

    sizes = sorted(size_counter.values())  # ascending
    n = len(sizes)
    total = sum(sizes)
    if total == 0:
        return 0.0

    weighted_sum = sum((i + 1) * x for i, x in enumerate(sizes))
    gini = (2 * weighted_sum) / (n * total) - (n + 1) / n
    # Clamp to [0, 1] to guard against floating-point drift
    gini = max(0.0, min(1.0, gini))
    logger.debug("compute_community_gini: %.4f from %d communities", gini, n_communities)
    return gini


def compute_spectral_gap(graph: nx.DiGraph) -> float:
    """Compute the spectral gap (algebraic connectivity / Fiedler value).

    The spectral gap is the difference between the second-smallest and
    smallest eigenvalues of the graph Laplacian.  A larger gap indicates a
    better-connected, more robust graph structure.

    Args:
        graph: DiGraph returned by :func:`build_concept_graph`.

    Returns:
        Spectral gap >= 0.  Returns 0.0 for graphs with fewer than 2 nodes,
        disconnected graphs, or any numeric error.
    """
    if graph.number_of_nodes() < 2:
        return 0.0

    try:
        eigenvalues = np.sort(nx.laplacian_spectrum(graph.to_undirected()))
        gap = float(eigenvalues[1] - eigenvalues[0])
        logger.debug("compute_spectral_gap: %.6f", gap)
        return max(0.0, gap)
    except Exception:
        logger.debug("compute_spectral_gap: failed to compute eigenvalues, returning 0.0")
        return 0.0
