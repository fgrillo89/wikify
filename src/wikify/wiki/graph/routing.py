"""Domain-aware query routing for the wiki.

Wraps the existing ``search_papers`` function with domain scoping and
cross-domain expansion driven by the Louvain community structure stored in
``DomainCluster`` rows.

Public API
----------
search_within_domain(query, domain_cluster, top_k=10) -> list[dict]
search_across_domains(query, primary_cluster, expansion_clusters, graph, top_k=10) -> list[dict]
domain_aware_search(query, top_k=10) -> list[dict]
get_domain_context(concept_id) -> dict
"""

from __future__ import annotations

import logging

from sqlmodel import select

from wikify.core.store.db import get_session
from wikify.core.store.embeddings import _store
from wikify.core.store.models import ConceptRecord, DomainCluster, SourceCoverage
from wikify.wiki.graph.domains import expand_via_bridges, get_domain_for_query

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _source_ids_for_concepts(concept_ids: list[str]) -> list[str]:
    """Return distinct source_ids (Paper.id) that cover any of the given concepts.

    SourceCoverage.article_slug stores the wiki article slug that the source
    contributed to, not a concept id directly.  We need to map concept ->
    article -> source.  However, since SourceCoverage.article_slug is typically
    set to a concept slug, we join via ConceptRecord.article_path comparison
    as a best-effort lookup.

    Strategy (two-pass):
    1. Load ConceptRecord rows for the given ids and collect their article_path
       values (slugs used as SourceCoverage.article_slug).
    2. Query SourceCoverage for those slugs.
    3. Also include direct article_slug matches for concept ids themselves
       (handles the common case where the slug == concept id).
    """
    if not concept_ids:
        return []

    slugs: set[str] = set(concept_ids)  # concept id often == article slug

    with get_session() as session:
        rows = session.exec(
            select(ConceptRecord).where(ConceptRecord.id.in_(concept_ids))  # type: ignore[attr-defined]
        ).all()
        for row in rows:
            if row.article_path:
                # article_path is like "concepts/atomic_layer_deposition.md"
                # strip leading dir and .md extension to get the slug
                path = row.article_path.replace("\\", "/")
                slug = path.split("/")[-1]
                if slug.endswith(".md"):
                    slug = slug[:-3]
                slugs.add(slug)
            # Also treat the concept id as a potential slug
            slugs.add(row.id)

        coverage_rows = session.exec(
            select(SourceCoverage).where(SourceCoverage.article_slug.in_(list(slugs)))  # type: ignore[attr-defined]
        ).all()

    source_ids = list({row.source_id for row in coverage_rows})
    return source_ids


def _search_collection_for_sources(
    query: str,
    source_ids: list[str],
    top_k: int,
) -> list[dict]:
    """Query ChromaDB chunk collection restricted to a set of source_ids.

    Returns list of dicts with keys: source_id, chunk_id, score, content_preview.
    Score is cosine similarity (1 - distance) since ChromaDB uses cosine distance.
    """
    if not source_ids:
        return []

    chunk_collection = _store.chunk_collection
    if chunk_collection.count() == 0:
        return []

    query_embedding = _store.model.encode([query])[0]

    # ChromaDB where filter
    if len(source_ids) == 1:
        where_filter: dict = {"paper_id": source_ids[0]}
    else:
        where_filter = {"paper_id": {"$in": source_ids}}

    try:
        n_results = min(top_k, chunk_collection.count())
        if n_results == 0:
            return []

        raw = chunk_collection.query(
            query_embeddings=[query_embedding.tolist()],
            n_results=n_results,
            include=["distances", "metadatas", "documents"],
            where=where_filter,
        )
    except Exception:
        logger.exception(
            "_search_collection_for_sources: ChromaDB query failed for %d source_ids",
            len(source_ids),
        )
        return []

    chunk_ids: list[str] = raw["ids"][0] if raw.get("ids") else []
    distances: list[float] = raw["distances"][0] if raw.get("distances") else []
    metadatas: list[dict] = raw["metadatas"][0] if raw.get("metadatas") else []
    documents: list[str] = raw["documents"][0] if raw.get("documents") else []

    results: list[dict] = []
    for cid, dist, meta, doc in zip(chunk_ids, distances, metadatas, documents):
        score = 1.0 - float(dist)  # convert cosine distance -> similarity
        results.append(
            {
                "chunk_id": cid,
                "source_id": meta.get("paper_id", ""),
                "score": score,
                "content_preview": (doc[:200] + "...") if len(doc) > 200 else doc,
            }
        )

    return results


def _merge_and_rerank(results: list[dict], top_k: int) -> list[dict]:
    """Merge result lists, deduplicate by chunk_id, and re-rank by score descending."""
    seen: dict[str, dict] = {}
    for item in results:
        cid = item["chunk_id"]
        if cid not in seen or item["score"] > seen[cid]["score"]:
            seen[cid] = item
    ranked = sorted(seen.values(), key=lambda x: x["score"], reverse=True)
    return ranked[:top_k]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search_within_domain(
    query: str,
    domain_cluster: DomainCluster,
    top_k: int = 10,
) -> list[dict]:
    """Search for query-relevant chunks restricted to a single domain cluster.

    Gathers concept IDs belonging to the cluster (core + bridge), resolves
    the source papers that cover those concepts via SourceCoverage, then
    queries the chunk embedding collection restricted to those papers.

    Args:
        query:          Natural language search query.
        domain_cluster: The DomainCluster to search within.
        top_k:          Maximum number of results.

    Returns:
        List of dicts with keys:
            source_id, chunk_id, score, domain, content_preview
    """
    concept_ids = domain_cluster.parsed_core_concepts + domain_cluster.parsed_bridge_concepts
    if not concept_ids:
        logger.warning("search_within_domain: cluster %r has no concept ids", domain_cluster.id)
        return []

    source_ids = _source_ids_for_concepts(concept_ids)
    if not source_ids:
        logger.info(
            "search_within_domain: no SourceCoverage rows for cluster %r", domain_cluster.id
        )
        return []

    raw_results = _search_collection_for_sources(query, source_ids, top_k)

    # Tag each result with the domain
    for item in raw_results:
        item["domain"] = domain_cluster.label

    return raw_results[:top_k]


def search_across_domains(
    query: str,
    primary_cluster: DomainCluster,
    expansion_clusters: list[DomainCluster],
    graph,
    top_k: int = 10,
) -> list[dict]:
    """Search within the primary domain and expand across domain boundaries.

    Process:
    1. Search within primary domain.
    2. Use ``expand_via_bridges`` to find bridge-reachable concepts in expansion
       clusters.
    3. Search sources covering those bridge concepts.
    4. Merge and re-rank all results by score descending.
    5. Tag each result with its originating domain label.

    Args:
        query:              Natural language search query.
        primary_cluster:    Highest-relevance domain cluster.
        expansion_clusters: Candidate clusters for cross-domain expansion.
        graph:              Directed concept graph (networkx.DiGraph).
        top_k:              Maximum number of results to return.

    Returns:
        List of dicts with keys:
            source_id, chunk_id, score, domain, content_preview
    """
    all_results: list[dict] = []

    # Primary domain results
    primary_results = search_within_domain(query, primary_cluster, top_k=top_k)
    all_results.extend(primary_results)

    # Bridge expansion: concept IDs reachable from primary across cluster boundaries
    bridge_concept_ids = expand_via_bridges(primary_cluster, expansion_clusters, graph)

    if bridge_concept_ids:
        # Map each bridge concept back to its owning expansion cluster for labelling
        exp_concept_to_cluster: dict[str, DomainCluster] = {}
        for exp_cluster in expansion_clusters:
            all_exp_concepts = exp_cluster.parsed_core_concepts + exp_cluster.parsed_bridge_concepts
            for cid in all_exp_concepts:
                exp_concept_to_cluster.setdefault(cid, exp_cluster)

        # Group bridge concepts by expansion cluster so we can search per cluster
        cluster_to_bridge_concepts: dict[str, list[str]] = {}
        for cid in bridge_concept_ids:
            cl = exp_concept_to_cluster.get(cid)
            if cl is not None:
                cluster_to_bridge_concepts.setdefault(cl.id, []).append(cid)
            else:
                # Bridge concept doesn't map to a known expansion cluster;
                # search it in isolation under a generic label
                cluster_to_bridge_concepts.setdefault("__bridge__", []).append(cid)

        for cluster_id, cids in cluster_to_bridge_concepts.items():
            source_ids = _source_ids_for_concepts(cids)
            if not source_ids:
                continue
            raw = _search_collection_for_sources(query, source_ids, top_k=top_k)

            # Determine domain label for these results
            if cluster_id == "__bridge__":
                domain_label = "cross-domain bridge"
            else:
                cl = next((ec for ec in expansion_clusters if ec.id == cluster_id), None)
                domain_label = cl.label if cl is not None else cluster_id

            for item in raw:
                item["domain"] = domain_label

            all_results.extend(raw)

    return _merge_and_rerank(all_results, top_k)


def domain_aware_search(
    query: str,
    top_k: int = 10,
) -> list[dict]:
    """Main entry point for domain-scoped wiki search.

    Loads all DomainCluster rows, embeds the query, routes to the most
    relevant domain, and optionally expands across domain boundaries via
    bridge concepts.

    Falls back to unscoped ``search_papers`` when no domain clusters have
    been discovered yet.

    Args:
        query:  Natural language search query.
        top_k:  Maximum number of results.

    Returns:
        List of dicts with keys:
            source_id, chunk_id, score, domain, content_preview

        On fallback to ``search_papers``, returns:
            [{"source_id": "", "chunk_id": "", "score": 0.0,
              "domain": "unscoped", "content_preview": <full text>}]
    """
    # Load all clusters from DB
    with get_session() as session:
        clusters = list(session.exec(select(DomainCluster)).all())

    if not clusters:
        logger.info("domain_aware_search: no domain clusters found — falling back to search_papers")
        return _fallback_search(query, top_k)

    # Embed the query (lazy model init handled by _store.model property)
    try:
        query_embedding = _store.model.encode([query])[0].tolist()
    except Exception:
        logger.exception("domain_aware_search: embedding failed — falling back to search_papers")
        return _fallback_search(query, top_k)

    # Route to primary domain (and potential expansion domains)
    try:
        primary, expansion = get_domain_for_query(query_embedding, clusters)
    except ValueError:
        logger.warning("domain_aware_search: get_domain_for_query raised ValueError — falling back")
        return _fallback_search(query, top_k)

    logger.info(
        "domain_aware_search: routed to primary=%r expansion=%s",
        primary.label,
        [c.label for c in expansion],
    )

    if expansion:
        # Need the concept graph for bridge traversal
        try:
            from wikify.wiki.graph.build import build_concept_graph

            graph = build_concept_graph()
        except Exception:
            logger.exception(
                "domain_aware_search: concept graph load failed — "
                "falling back to primary-only search"
            )
            return search_within_domain(query, primary, top_k=top_k)

        return search_across_domains(query, primary, expansion, graph, top_k=top_k)

    return search_within_domain(query, primary, top_k=top_k)


def get_domain_context(concept_id: str) -> dict:
    """Return domain context metadata for a single concept.

    Useful for the article writer and MCP tools when they need to know
    which domains a concept belongs to and whether it bridges domains.

    Args:
        concept_id: The ConceptRecord.id (slugified name).

    Returns:
        Dict with keys:
            primary_domain (str):               Label of the first domain the concept
                                                belongs to, or "" if unknown.
            all_domains (list[str]):            Labels of all domains this concept
                                                appears in.
            is_bridge (bool):                   True if the concept appears in the
                                                bridge_concept_ids of any cluster.
            neighbors_in_other_domains (list[str]):
                                                concept_ids of graph neighbours that
                                                live in a different domain.
    """
    with get_session() as session:
        concept = session.get(ConceptRecord, concept_id)
        if concept is None:
            logger.warning("get_domain_context: concept_id %r not found", concept_id)
            return {
                "primary_domain": "",
                "all_domains": [],
                "is_bridge": False,
                "neighbors_in_other_domains": [],
            }

        domain_ids: list[str] = concept.parsed_domains

        # Load matching clusters to get human-readable labels
        all_clusters = list(session.exec(select(DomainCluster)).all())

    cluster_by_id: dict[str, DomainCluster] = {cl.id: cl for cl in all_clusters}

    all_domain_labels: list[str] = []
    for did in domain_ids:
        cl = cluster_by_id.get(did)
        if cl is not None:
            all_domain_labels.append(cl.label)
        else:
            all_domain_labels.append(did)  # fall back to raw id

    primary_domain = all_domain_labels[0] if all_domain_labels else ""

    # Check if this concept appears in any cluster's bridge list
    is_bridge = any(concept_id in cl.parsed_bridge_concepts for cl in all_clusters)

    # Find graph neighbours in different domains (requires concept graph)
    neighbors_in_other_domains: list[str] = []
    try:
        from wikify.wiki.graph.build import build_concept_graph

        graph = build_concept_graph()
        if concept_id in graph:
            own_domain_set = set(domain_ids)
            for neighbor in set(graph.successors(concept_id)) | set(graph.predecessors(concept_id)):
                # Lookup neighbour's domains
                nbr_cl = cluster_by_id.get(neighbor)
                if nbr_cl is not None:
                    nbr_domains = {nbr_cl.id}
                else:
                    # Try loading from DB (not already in cluster_by_id means it's a concept id)
                    with get_session() as session:
                        nbr_record = session.get(ConceptRecord, neighbor)
                    nbr_domains = (
                        set(nbr_record.parsed_domains) if nbr_record is not None else set()
                    )

                if nbr_domains and not nbr_domains.issubset(own_domain_set):
                    neighbors_in_other_domains.append(neighbor)
    except Exception:
        logger.exception("get_domain_context: concept graph lookup failed for %r", concept_id)

    return {
        "primary_domain": primary_domain,
        "all_domains": all_domain_labels,
        "is_bridge": is_bridge,
        "neighbors_in_other_domains": sorted(set(neighbors_in_other_domains)),
    }


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


def _fallback_search(query: str, top_k: int) -> list[dict]:
    """Delegate to unscoped corpus search and wrap result in the standard dict shape."""
    try:
        from wikify.core.corpus_tools import search_corpus

        result = search_corpus(query, top_k=top_k)
        return [
            {
                "source_id": "",
                "chunk_id": "",
                "score": 0.0,
                "domain": "unscoped",
                "content_preview": (
                    result.text[:500] + "..." if len(result.text) > 500 else result.text
                ),
            }
        ]
    except Exception:
        logger.exception("_fallback_search: corpus search failed for query %r", query)
        return []
