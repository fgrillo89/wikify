"""Domain membrane module: auto-domain discovery, LLM-vetted community naming,
and cross-domain query routing.

Implements Pass 2b of the Wikipedia/epoch pipeline:

    discover_domains(graph, epoch)
        1. detect_communities       -- Louvain partition of the concept graph
        2. compute_topology_metrics -- pure graph metrics -> TopologySnapshot
        3. validate_community       -- LLM coherence check + label/scope per community
        4. check_community_merge    -- LLM merge decision for adjacent communities
        5. build DomainCluster rows
        6. assign_concepts_to_domains -- write domains/domain back to ConceptRecord
        7. persist everything

Query routing:
    get_domain_for_query    -- cosine similarity -> primary + expansion clusters
    expand_via_bridges      -- follow bridge paths across cluster boundaries
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict

import networkx as nx
import numpy as np
from sqlmodel import select

from wikify.config import settings
from wikify.llm.client import complete_json
from wikify.store.db import get_session
from wikify.store.embeddings import _store
from wikify.store.models import ConceptRecord, DomainCluster, TopologySnapshot
from wikify.wiki.builder import slugify
from wikify.wiki.concept_graph import classify_node_roles, detect_communities
from wikify.wiki.persona import get_or_create_persona

logger = logging.getLogger(__name__)

HAIKU_MODEL = settings.llm_fast_model

# Minimum modularity Q below which we skip per-community LLM validation and
# create a single catch-all domain instead.
_MIN_MODULARITY_FOR_LLM = 0.3


# ---------------------------------------------------------------------------
# 1. LLM validation of a single community
# ---------------------------------------------------------------------------


def validate_community(
    concept_names: list[str],
    definitions: list[str],
    source_titles: list[str],
    model: str | None = None,
) -> dict:
    """Ask an LLM whether a community forms a coherent domain.

    All concept names and their definitions are passed to the LLM so it can
    decide independently what is core vs. peripheral.

    Args:
        concept_names: Display names of every concept in the community.
        definitions:   One-line definitions, parallel-indexed with concept_names.
        source_titles: Titles of papers that most frequently mention these concepts.
        model:         litellm model string. Defaults to HAIKU_MODEL.

    Returns:
        Dict with keys:
            coherent      (bool)
            label         (str)  -- 2-5 word domain label
            scope         (str)  -- one-sentence scope
            core_concepts (list[str])  -- concept names identified as core
            split_proposal (list[dict] | None)
                Each dict: {"label": str, "concepts": list[str]}
    """
    resolved_model = model or HAIKU_MODEL

    # Build the concept table for the prompt
    concept_rows = []
    for name, defn in zip(concept_names, definitions):
        defn_text = defn.strip() if defn else "(no definition)"
        concept_rows.append(f"- {name}: {defn_text}")

    sources_text = "\n".join(f"- {t}" for t in source_titles[:20]) or "(none)"
    concepts_text = "\n".join(concept_rows)

    prompt = (
        "You are a scientific domain expert.\n\n"
        "Below is a list of concepts extracted from a research corpus together with "
        "their definitions. These concepts were grouped together by a graph community "
        "detection algorithm.\n\n"
        f"CONCEPTS ({len(concept_names)}):\n{concepts_text}\n\n"
        f"SOURCE DOCUMENTS (most relevant):\n{sources_text}\n\n"
        "Please evaluate whether these concepts form a single coherent research domain.\n\n"
        "Return a JSON object with exactly these fields:\n"
        "{\n"
        '  "coherent": true or false,\n'
        '  "label": "2-5 word domain label (e.g. \\"ALD Process Engineering\\")",\n'
        '  "scope": "One sentence describing what this domain covers.",\n'
        '  "core_concepts": ["list", "of", "concept", "names", "that", "are", "central"],\n'
        '  "split_proposal": null  // or a list of objects if NOT coherent:\n'
        '  // [{"label": "Sub-domain A", "concepts": ["ConceptName1", ...]}, ...]\n'
        "}\n\n"
        "Rules:\n"
        "- If coherent is true, split_proposal must be null.\n"
        "- If coherent is false, split_proposal must list at least 2 sub-domains with "
        "all concept names distributed among them.\n"
        "- core_concepts must be a subset of the provided concept names.\n"
        "Return ONLY valid JSON, no markdown fences."
    )

    try:
        result = complete_json(
            messages=[{"role": "user", "content": prompt}],
            model=resolved_model,
            temperature=0.2,
            max_tokens=2048,
        )
        if not isinstance(result, dict):
            raise ValueError(f"Expected dict, got {type(result)}")

        # Normalise required fields
        return {
            "coherent": bool(result.get("coherent", True)),
            "label": str(result.get("label", "") or _fallback_label(concept_names)),
            "scope": str(result.get("scope", "")),
            "core_concepts": list(result.get("core_concepts", [])),
            "split_proposal": result.get("split_proposal"),
        }
    except Exception:
        logger.exception(
            "validate_community: LLM call failed for %d concepts; using fallback",
            len(concept_names),
        )
        return {
            "coherent": True,
            "label": _fallback_label(concept_names),
            "scope": "",
            "core_concepts": concept_names[:5],
            "split_proposal": None,
        }


def _fallback_label(concept_names: list[str]) -> str:
    """Generate a generic label from concept names when LLM fails."""
    if not concept_names:
        return "Unknown Domain"
    # Use the first concept name as a seed
    return f"Domain: {concept_names[0]}"


# ---------------------------------------------------------------------------
# 2. LLM merge decision for adjacent communities
# ---------------------------------------------------------------------------


def check_community_merge(
    cluster_a_label: str,
    cluster_a_scope: str,
    cluster_b_label: str,
    cluster_b_scope: str,
    shared_bridges: list[str],
    model: str | None = None,
) -> bool:
    """Ask an LLM whether two adjacent communities should be merged.

    Args:
        cluster_a_label: Label of the first community.
        cluster_a_scope: Scope sentence of the first community.
        cluster_b_label: Label of the second community.
        cluster_b_scope: Scope sentence of the second community.
        shared_bridges:  Names of bridge concepts connecting the communities.
        model:           litellm model string. Defaults to HAIKU_MODEL.

    Returns:
        True if the LLM recommends merging, False otherwise.
    """
    resolved_model = model or HAIKU_MODEL

    bridges_text = ", ".join(shared_bridges[:20]) if shared_bridges else "(none)"

    prompt = (
        "You are a scientific domain expert deciding whether two research sub-domains "
        "are distinct enough to remain separate, or whether they should be merged.\n\n"
        f"Community A: {cluster_a_label}\n"
        f"Scope: {cluster_a_scope or '(no scope)'}\n\n"
        f"Community B: {cluster_b_label}\n"
        f"Scope: {cluster_b_scope or '(no scope)'}\n\n"
        f"Bridge concepts shared between them: {bridges_text}\n\n"
        "Should these two communities be treated as a single domain?\n\n"
        'Return JSON: {"merge": true or false, "reason": "one sentence explanation"}\n'
        "Return ONLY valid JSON, no markdown fences."
    )

    try:
        result = complete_json(
            messages=[{"role": "user", "content": prompt}],
            model=resolved_model,
            temperature=0.2,
            max_tokens=256,
        )
        if not isinstance(result, dict):
            return False
        return bool(result.get("merge", False))
    except Exception:
        logger.exception(
            "check_community_merge: LLM call failed for %r + %r; defaulting to no-merge",
            cluster_a_label,
            cluster_b_label,
        )
        return False


# ---------------------------------------------------------------------------
# 3. Pure graph topology metrics
# ---------------------------------------------------------------------------


def compute_topology_metrics(
    graph: nx.DiGraph,
    communities: dict[str, int],
    epoch: int,
    roles: dict[str, str] | None = None,
) -> TopologySnapshot:
    """Compute graph topology metrics and return a (non-persisted) TopologySnapshot.

    Args:
        graph:       Directed concept graph.
        communities: Mapping concept_id -> community_index from detect_communities().
        epoch:       Current epoch number.
        roles:       Optional pre-computed roles from classify_node_roles().  If None,
                     roles are computed internally.

    Returns:
        A populated TopologySnapshot (id=None, not yet saved to DB).
    """
    n_nodes = graph.number_of_nodes()
    n_edges = graph.number_of_edges()

    if n_nodes == 0:
        return TopologySnapshot(
            epoch=epoch,
            modularity_q=0.0,
            inter_community_edge_ratio=0.0,
            bridge_density=0.0,
            community_gini=0.0,
            spectral_gap=0.0,
            community_count=0,
            total_concepts=0,
            total_edges=0,
        )

    # Build community sets as required by nx.community.modularity
    community_index_to_nodes: dict[int, set[str]] = defaultdict(set)
    for node_id, comm_idx in communities.items():
        community_index_to_nodes[comm_idx].add(node_id)
    community_sets = list(community_index_to_nodes.values())

    undirected = graph.to_undirected()

    # ── Modularity Q ────────────────────────────────────────────────────────────
    modularity_q = 0.0
    if undirected.number_of_edges() > 0 and len(community_sets) > 0:
        try:
            modularity_q = nx.community.modularity(undirected, community_sets)
        except Exception:
            logger.exception("compute_topology_metrics: modularity computation failed")

    # ── Inter-community edge ratio ───────────────────────────────────────────────
    inter_community_edges = 0
    for src, tgt in graph.edges():
        if communities.get(src, -1) != communities.get(tgt, -2):
            inter_community_edges += 1
    inter_community_edge_ratio = inter_community_edges / n_edges if n_edges > 0 else 0.0

    # ── Bridge density ──────────────────────────────────────────────────────────
    if roles is None:
        from wikify.wiki.concept_graph import score_importance

        scores = score_importance(graph)
        roles = classify_node_roles(graph, scores)

    bridge_count = sum(1 for r in roles.values() if r == "bridge")
    bridge_density = bridge_count / n_nodes if n_nodes > 0 else 0.0

    # ── Community Gini coefficient ───────────────────────────────────────────────
    community_sizes = sorted(len(s) for s in community_sets)
    community_gini = _gini(community_sizes)

    # ── Spectral gap ─────────────────────────────────────────────────────────────
    spectral_gap = 0.0
    try:
        eigenvalues = sorted(nx.laplacian_spectrum(undirected))
        if len(eigenvalues) >= 2:
            spectral_gap = float(eigenvalues[1]) - float(eigenvalues[0])
    except Exception:
        logger.warning("compute_topology_metrics: spectral gap computation failed (disconnected?)")

    return TopologySnapshot(
        epoch=epoch,
        modularity_q=modularity_q,
        inter_community_edge_ratio=inter_community_edge_ratio,
        bridge_density=bridge_density,
        community_gini=community_gini,
        spectral_gap=spectral_gap,
        community_count=len(community_sets),
        total_concepts=n_nodes,
        total_edges=n_edges,
    )


def _gini(sizes: list[int]) -> float:
    """Gini coefficient of community sizes.

    Returns 0.0 for empty list or all-equal sizes (perfect balance).
    Uses the formula: G = (2 * sum(rank_i * x_i)) / (n * sum(x_i)) - (n+1)/n
    where rank_i is 1-based rank in ascending order.
    """
    n = len(sizes)
    if n <= 1:
        return 0.0
    total = sum(sizes)
    if total == 0:
        return 0.0
    weighted = sum((i + 1) * x for i, x in enumerate(sizes))
    return (2 * weighted) / (n * total) - (n + 1) / n


# ---------------------------------------------------------------------------
# 4. Community centroid via mean definition embedding
# ---------------------------------------------------------------------------


def _compute_community_centroid(
    concept_ids: list[str],
    session,
) -> list[float]:
    """Return the mean embedding of concept definitions in a community.

    Args:
        concept_ids: Sorted list of concept record IDs.
        session:     Active SQLModel session.

    Returns:
        Mean embedding vector as list of floats, or [] on failure.
    """
    if not concept_ids:
        return []

    texts: list[str] = []
    for cid in sorted(concept_ids):
        row = session.get(ConceptRecord, cid)
        if row is not None and row.definition and row.definition.strip():
            texts.append(row.definition.strip())

    if not texts:
        return []

    try:
        embeddings = _store.model.encode(texts)
        centroid = np.mean(embeddings, axis=0)
        return centroid.tolist()
    except Exception:
        logger.exception("_compute_community_centroid: encoding failed for %d texts", len(texts))
        return []


# ---------------------------------------------------------------------------
# 5. Assign concepts to domains
# ---------------------------------------------------------------------------


def assign_concepts_to_domains(
    communities: dict[str, int],
    roles: dict[str, str],
    clusters: list[DomainCluster],
    graph: nx.DiGraph,
) -> None:
    """Update ConceptRecord.domains (and deprecated .domain) based on community membership.

    Interior concepts (not bridges) are assigned to their single community cluster.
    Bridge concepts are assigned to all clusters they have graph neighbors in.

    Args:
        communities: concept_id -> community_index mapping.
        roles:       concept_id -> "core"|"bridge"|"peripheral" mapping.
        clusters:    The DomainCluster list already built (with correct ids/labels).
        graph:       Directed concept graph used to find bridge concept neighbors.
    """
    # Build a lookup: community_index -> cluster_id
    # Clusters are ordered by community index (0-based, largest first from detect_communities).
    # We match by position in the clusters list.
    comm_index_to_cluster: dict[int, DomainCluster] = {}
    for cluster in clusters:
        # Find which community index this cluster corresponds to by checking its
        # core concepts membership
        core_ids = cluster.parsed_core_concepts
        if core_ids:
            for cid in core_ids:
                idx = communities.get(cid)
                if idx is not None:
                    comm_index_to_cluster[idx] = cluster
                    break

    # Fallback: if any community index is still unmapped, do positional assignment.
    # clusters are ordered by descending community size (community 0 is largest).
    all_comm_indices = sorted({v for v in communities.values()})
    for i, idx in enumerate(all_comm_indices):
        if idx not in comm_index_to_cluster and i < len(clusters):
            comm_index_to_cluster[idx] = clusters[i]

    with get_session() as session:
        for cid in sorted(communities.keys()):
            my_comm = communities[cid]
            my_cluster = comm_index_to_cluster.get(my_comm)
            role = roles.get(cid, "peripheral")

            if role == "bridge":
                # Find all communities that contain neighbors of this bridge concept
                neighbor_comms: set[int] = set()
                for neighbor in graph.successors(cid):
                    nbr_comm = communities.get(neighbor)
                    if nbr_comm is not None:
                        neighbor_comms.add(nbr_comm)
                for neighbor in graph.predecessors(cid):
                    nbr_comm = communities.get(neighbor)
                    if nbr_comm is not None:
                        neighbor_comms.add(nbr_comm)
                # Include own community too
                if my_comm is not None:
                    neighbor_comms.add(my_comm)

                domain_ids: list[str] = []
                for nc in sorted(neighbor_comms):
                    cl = comm_index_to_cluster.get(nc)
                    if cl is not None:
                        domain_ids.append(cl.id)
            else:
                # Interior / peripheral: belongs to own cluster only
                domain_ids = [my_cluster.id] if my_cluster else []

            row = session.get(ConceptRecord, cid)
            if row is None:
                continue

            row.domains = json.dumps(domain_ids)
            # Backward-compat: primary domain label
            if domain_ids:
                primary_cluster = comm_index_to_cluster.get(my_comm)
                row.domain = primary_cluster.label if primary_cluster else domain_ids[0]
            session.add(row)

        session.commit()

    logger.info(
        "assign_concepts_to_domains: updated %d concepts across %d clusters",
        len(communities),
        len(clusters),
    )


# ---------------------------------------------------------------------------
# 6. Main pipeline entry point
# ---------------------------------------------------------------------------


def discover_domains(
    graph: nx.DiGraph,
    epoch: int,
    model: str | None = None,
) -> list[DomainCluster]:
    """Full domain discovery pipeline (Pass 2b).

    Steps:
        1. Louvain community detection.
        2. Topology metrics.
        3. Low-modularity short-circuit: single catch-all domain.
        4. Per-community LLM validation (coherence, label, scope, possible split).
        5. Adjacent-community merge check via LLM.
        6. Build DomainCluster objects (centroid, persona, bridge ids).
        7. Assign concepts to domains.
        8. Persist clusters + topology snapshot to DB.

    Args:
        graph:  Directed concept graph built in Pass 2.
        epoch:  Current epoch number.
        model:  litellm model string. Defaults to HAIKU_MODEL.

    Returns:
        List of DomainCluster objects (persisted).
    """
    resolved_model = model or HAIKU_MODEL

    if graph.number_of_nodes() == 0:
        logger.warning("discover_domains: empty graph — no domains to discover")
        return []

    # ── Pass 1: community detection ──────────────────────────────────────────────
    communities = detect_communities(graph)
    if not communities:
        logger.warning("discover_domains: detect_communities returned empty mapping")
        return []

    # ── Pass 2: roles + topology metrics ────────────────────────────────────────
    from wikify.wiki.concept_graph import score_importance

    scores = score_importance(graph)
    roles = classify_node_roles(graph, scores)

    topology = compute_topology_metrics(graph, communities, epoch, roles=roles)
    logger.info(
        "discover_domains: epoch=%d Q=%.3f communities=%d nodes=%d edges=%d",
        epoch,
        topology.modularity_q,
        topology.community_count,
        topology.total_concepts,
        topology.total_edges,
    )

    # ── Pass 3: low-modularity short-circuit ─────────────────────────────────────
    if topology.modularity_q < _MIN_MODULARITY_FOR_LLM:
        logger.warning(
            "discover_domains: modularity Q=%.3f < %.1f — creating single catch-all domain",
            topology.modularity_q,
            _MIN_MODULARITY_FOR_LLM,
        )
        clusters = _build_single_domain(graph, communities, roles, epoch, resolved_model)
        _persist_clusters(clusters, epoch)
        _persist_topology(topology)
        assign_concepts_to_domains(communities, roles, clusters, graph)
        return clusters

    # ── Pass 4: group concept IDs by community index ─────────────────────────────
    comm_to_concepts: dict[int, list[str]] = defaultdict(list)
    for cid in sorted(communities.keys()):
        comm_to_concepts[communities[cid]].append(cid)

    # Collect source titles used frequently across the whole graph
    source_titles = _get_frequent_source_titles()

    # ── Pass 5: validate each community; collect (community_index, validation) ───
    validated: dict[int, dict] = {}

    with get_session() as session:
        for comm_idx in sorted(comm_to_concepts.keys()):
            cids = comm_to_concepts[comm_idx]
            names: list[str] = []
            defs: list[str] = []
            for cid in cids:
                row = session.get(ConceptRecord, cid)
                if row is not None:
                    names.append(row.name)
                    defs.append(row.definition)

            if not names:
                continue

            validation = validate_community(names, defs, source_titles, model=resolved_model)
            validated[comm_idx] = validation
            logger.debug(
                "discover_domains: community %d -> label=%r coherent=%s",
                comm_idx,
                validation["label"],
                validation["coherent"],
            )

    # ── Pass 6: handle incoherent communities (split proposals) ─────────────────
    # Re-partition concept IDs for communities flagged incoherent.
    final_partitions: list[tuple[list[str], dict]] = []

    for comm_idx in sorted(validated.keys()):
        validation = validated[comm_idx]
        cids = comm_to_concepts[comm_idx]

        if not validation["coherent"] and validation.get("split_proposal"):
            sub_partitions = _apply_split(cids, validation["split_proposal"], communities)
            for sub_cids, sub_val in sub_partitions:
                final_partitions.append((sub_cids, sub_val))
        else:
            final_partitions.append((cids, validation))

    # ── Pass 7: adjacent-community merge check ───────────────────────────────────
    bridge_ids = {cid for cid, role in roles.items() if role == "bridge"}

    # Build adjacency between partition slots via shared bridges
    n_parts = len(final_partitions)
    partition_sets = [set(cids) for cids, _ in final_partitions]

    # Find which partitions share bridge concepts
    merged_flags = [False] * n_parts  # True = absorbed into another partition
    merged_into: dict[int, int] = {}  # child index -> parent index

    # Compute pairwise inter-community edge densities to decide which pairs
    # to call the LLM for.
    pair_ratios: list[tuple[int, int, float]] = []
    total_edges = graph.number_of_edges()

    for i in range(n_parts):
        for j in range(i + 1, n_parts):
            shared_bridges = bridge_ids & partition_sets[i] & partition_sets[j]
            cross_edges = sum(
                1
                for u, v in graph.edges()
                if (u in partition_sets[i] and v in partition_sets[j])
                or (u in partition_sets[j] and v in partition_sets[i])
            )
            ratio = cross_edges / total_edges if total_edges > 0 else 0.0
            if shared_bridges or cross_edges > 0:
                pair_ratios.append((i, j, ratio))

    # Median ratio threshold for LLM call
    if pair_ratios:
        median_ratio = float(np.median([r for _, _, r in pair_ratios]))
    else:
        median_ratio = 0.0

    for i, j, ratio in pair_ratios:
        if merged_flags[i] or merged_flags[j]:
            continue
        if ratio <= median_ratio:
            continue  # not dense enough to bother asking the LLM

        val_i = final_partitions[i][1]
        val_j = final_partitions[j][1]

        # Shared bridge concept names
        shared_bridge_cids = bridge_ids & partition_sets[i] & partition_sets[j]
        shared_bridge_names: list[str] = []
        with get_session() as session:
            for cid in sorted(shared_bridge_cids):
                row = session.get(ConceptRecord, cid)
                if row is not None:
                    shared_bridge_names.append(row.name)

        should_merge = check_community_merge(
            val_i["label"],
            val_i.get("scope", ""),
            val_j["label"],
            val_j.get("scope", ""),
            shared_bridge_names,
            model=resolved_model,
        )

        if should_merge:
            logger.info(
                "discover_domains: merging community %d (%r) into %d (%r)",
                j,
                val_j["label"],
                i,
                val_i["label"],
            )
            # Merge j into i
            partition_sets[i] |= partition_sets[j]
            final_partitions[i] = (
                sorted(partition_sets[i]),
                {
                    "coherent": True,
                    "label": val_i["label"],
                    "scope": val_i.get("scope", ""),
                    "core_concepts": val_i.get("core_concepts", [])
                    + val_j.get("core_concepts", []),
                    "split_proposal": None,
                },
            )
            merged_flags[j] = True
            merged_into[j] = i

    # Retain only non-merged partitions
    active_partitions = [
        (cids, val) for idx, (cids, val) in enumerate(final_partitions) if not merged_flags[idx]
    ]

    # ── Pass 8: build DomainCluster objects ──────────────────────────────────────
    clusters: list[DomainCluster] = []

    with get_session() as session:
        for part_cids, validation in active_partitions:
            if not part_cids:
                continue

            label = validation["label"] or _fallback_label([])
            cluster_id = slugify(label) or f"cluster_{len(clusters)}"

            # Resolve core concept IDs from LLM-provided names
            core_names_set = set(validation.get("core_concepts", []))
            core_ids: list[str] = []
            for cid in sorted(part_cids):
                row = session.get(ConceptRecord, cid)
                if row is not None and row.name in core_names_set:
                    core_ids.append(cid)

            # Bridge concepts within this partition
            bridge_ids_here = sorted(bridge_ids & set(part_cids))

            # Centroid embedding
            centroid = _compute_community_centroid(part_cids, session)

            # Persona
            try:
                persona_text = get_or_create_persona(label, model=resolved_model)
            except Exception:
                logger.exception("discover_domains: persona generation failed for %r", label)
                persona_text = ""

            cluster = DomainCluster(
                id=cluster_id,
                label=label,
                scope=validation.get("scope", ""),
                epoch_created=epoch,
                epoch_last_updated=epoch,
                concept_count=len(part_cids),
                core_concept_ids=json.dumps(core_ids),
                bridge_concept_ids=json.dumps(bridge_ids_here),
                centroid_embedding=json.dumps(centroid),
                modularity_contribution=topology.modularity_q / max(len(active_partitions), 1),
                persona_text=persona_text,
                merged_from=json.dumps([]),
            )
            clusters.append(cluster)

    # ── Pass 9: assign concepts to domains ───────────────────────────────────────
    assign_concepts_to_domains(communities, roles, clusters, graph)

    # ── Pass 10: persist ─────────────────────────────────────────────────────────
    _persist_clusters(clusters, epoch)
    _persist_topology(topology)

    logger.info(
        "discover_domains: epoch=%d created %d domain clusters",
        epoch,
        len(clusters),
    )
    return clusters


# ---------------------------------------------------------------------------
# 7. Query routing
# ---------------------------------------------------------------------------


def get_domain_for_query(
    query_embedding: list[float],
    clusters: list[DomainCluster],
) -> tuple[DomainCluster, list[DomainCluster]]:
    """Route a query embedding to its primary domain and expansion candidates.

    Primary domain is the cluster with highest cosine similarity to the query.
    Expansion candidates are clusters whose similarity is > 0.7 * primary_similarity.

    Args:
        query_embedding: Dense vector representing the query (same space as centroids).
        clusters:        List of DomainCluster objects (must have centroid_embedding set).

    Returns:
        (primary_cluster, expansion_clusters)

    Raises:
        ValueError: If clusters is empty or no cluster has a centroid.
    """
    if not clusters:
        raise ValueError("get_domain_for_query: clusters list is empty")

    q = np.array(query_embedding, dtype=float)
    q_norm = np.linalg.norm(q)
    if q_norm > 0:
        q = q / q_norm

    similarities: list[tuple[float, DomainCluster]] = []
    for cluster in clusters:
        centroid = cluster.parsed_centroid
        if not centroid:
            continue
        c = np.array(centroid, dtype=float)
        c_norm = np.linalg.norm(c)
        if c_norm > 0:
            c = c / c_norm
        sim = float(np.dot(q, c))
        similarities.append((sim, cluster))

    if not similarities:
        # All clusters lack centroids — return first cluster with no expansion
        return clusters[0], []

    similarities.sort(key=lambda x: x[0], reverse=True)
    primary_sim, primary = similarities[0]

    threshold = 0.7 * primary_sim
    expansion = [cl for sim, cl in similarities[1:] if sim > threshold]

    return primary, expansion


# ---------------------------------------------------------------------------
# 8. Bridge-based cross-domain concept expansion
# ---------------------------------------------------------------------------


def expand_via_bridges(
    primary_cluster: DomainCluster,
    expansion_clusters: list[DomainCluster],
    graph: nx.DiGraph,
) -> list[str]:
    """Follow bridge paths between clusters to find relevant cross-domain concepts.

    For each expansion cluster, finds bridge concepts shared with the primary,
    then collects their graph neighbours that belong to the expansion cluster.

    Args:
        primary_cluster:    The primary (highest-similarity) domain cluster.
        expansion_clusters: Candidate clusters for cross-domain expansion.
        graph:              Directed concept graph.

    Returns:
        Deduplicated, sorted list of concept IDs reachable via bridge paths.
    """
    primary_bridges = set(primary_cluster.parsed_bridge_concepts)
    reachable: set[str] = set()

    for exp_cluster in expansion_clusters:
        exp_concept_set = set(exp_cluster.parsed_bridge_concepts)
        exp_core_set = set(exp_cluster.parsed_core_concepts)
        exp_all = exp_concept_set | exp_core_set

        # Bridges shared between primary and expansion cluster
        shared_bridges = primary_bridges & set(exp_cluster.parsed_bridge_concepts)
        # Also consider bridges that simply have edges into both clusters
        for bridge_id in primary_bridges:
            if bridge_id not in graph:
                continue
            neighbors = set(graph.successors(bridge_id)) | set(graph.predecessors(bridge_id))
            if neighbors & exp_all:
                shared_bridges.add(bridge_id)

        # Traverse: for each shared bridge, collect neighbours in expansion cluster
        for bridge_id in shared_bridges:
            if bridge_id not in graph:
                continue
            for neighbor in set(graph.successors(bridge_id)) | set(graph.predecessors(bridge_id)):
                if neighbor in exp_all:
                    reachable.add(neighbor)

    return sorted(reachable)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_frequent_source_titles(limit: int = 20) -> list[str]:
    """Return titles of the most frequently occurring papers in SourceCoverage."""
    from wikify.store.models import Paper, SourceCoverage

    with get_session() as session:
        # Count source_ids in SourceCoverage
        rows = list(session.exec(select(SourceCoverage)).all())

    freq: dict[str, int] = defaultdict(int)
    for row in rows:
        freq[row.source_id] += 1

    top_ids = sorted(freq, key=lambda x: freq[x], reverse=True)[:limit]

    titles: list[str] = []
    with get_session() as session:
        for pid in top_ids:
            paper = session.get(Paper, pid)
            if paper is not None:
                titles.append(paper.title)

    return titles


def _build_single_domain(
    graph: nx.DiGraph,
    communities: dict[str, int],
    roles: dict[str, str],
    epoch: int,
    model: str,
) -> list[DomainCluster]:
    """Build a single catch-all DomainCluster for low-modularity graphs."""
    all_cids = sorted(communities.keys())
    bridge_ids_all = sorted(cid for cid, r in roles.items() if r == "bridge")
    core_ids_all = sorted(cid for cid, r in roles.items() if r == "core")

    with get_session() as session:
        centroid = _compute_community_centroid(all_cids, session)

    try:
        persona_text = get_or_create_persona("General Research Domain", model=model)
    except Exception:
        logger.exception("_build_single_domain: persona generation failed")
        persona_text = ""

    cluster = DomainCluster(
        id="general_domain",
        label="General Research Domain",
        scope="All concepts in the corpus form a single undifferentiated domain.",
        epoch_created=epoch,
        epoch_last_updated=epoch,
        concept_count=len(all_cids),
        core_concept_ids=json.dumps(core_ids_all),
        bridge_concept_ids=json.dumps(bridge_ids_all),
        centroid_embedding=json.dumps(centroid),
        modularity_contribution=0.0,
        persona_text=persona_text,
        merged_from=json.dumps([]),
    )
    return [cluster]


def _apply_split(
    cids: list[str],
    split_proposal: list[dict],
    communities: dict[str, int],
) -> list[tuple[list[str], dict]]:
    """Apply an LLM-proposed split of a community into sub-partitions.

    Each sub-partition in split_proposal contains a list of concept *names*.
    We map names back to IDs using a DB lookup.  Concepts not mentioned in any
    sub-partition are assigned to the largest sub-partition.

    Returns:
        List of (concept_id_list, validation_dict) pairs.
    """
    # Build name -> id mapping for concepts in this community
    name_to_id: dict[str, str] = {}
    with get_session() as session:
        for cid in cids:
            row = session.get(ConceptRecord, cid)
            if row is not None:
                name_to_id[row.name] = cid

    result: list[tuple[list[str], dict]] = []
    assigned: set[str] = set()

    for sub in split_proposal:
        sub_label = str(sub.get("label", "Sub-domain"))
        sub_names: list[str] = sub.get("concepts", [])
        sub_ids: list[str] = []
        for name in sub_names:
            cid = name_to_id.get(name)
            if cid is not None and cid not in assigned:
                sub_ids.append(cid)
                assigned.add(cid)

        if not sub_ids:
            continue

        sub_validation: dict = {
            "coherent": True,
            "label": sub_label,
            "scope": "",
            "core_concepts": sub_names,
            "split_proposal": None,
        }
        result.append((sorted(sub_ids), sub_validation))

    # Remainder goes into the first (largest) sub-partition
    remainder = [cid for cid in cids if cid not in assigned]
    if remainder:
        if result:
            combined_cids = sorted(set(result[0][0]) | set(remainder))
            result[0] = (combined_cids, result[0][1])
        else:
            # No successful splits; return original as a single partition
            result.append(
                (
                    sorted(cids),
                    {
                        "coherent": True,
                        "label": _fallback_label([]),
                        "scope": "",
                        "core_concepts": [],
                        "split_proposal": None,
                    },
                )
            )

    return result


def _persist_clusters(clusters: list[DomainCluster], epoch: int) -> None:
    """Replace DomainCluster rows for the current epoch in a single transaction."""
    if not clusters:
        return

    with get_session() as session:
        # Remove existing clusters that share an ID with new ones
        new_ids = {cl.id for cl in clusters}
        existing = list(session.exec(select(DomainCluster)).all())
        for row in existing:
            if row.id in new_ids:
                session.delete(row)
        session.flush()

        for cl in clusters:
            session.add(cl)
        session.commit()

    logger.info(
        "_persist_clusters: persisted %d DomainCluster rows for epoch=%d",
        len(clusters),
        epoch,
    )


def _persist_topology(snapshot: TopologySnapshot) -> None:
    """Persist a TopologySnapshot (auto-assigned id)."""
    with get_session() as session:
        session.add(snapshot)
        session.commit()

    logger.info(
        "_persist_topology: epoch=%d Q=%.3f communities=%d",
        snapshot.epoch,
        snapshot.modularity_q,
        snapshot.community_count,
    )
