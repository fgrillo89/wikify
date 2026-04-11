"""Wiki graph subsystem.

Owns concept-graph construction, importance scoring, topology metrics,
domain discovery, and domain-aware routing.

Internal layout:

- ``build``      : graph construction + relation extraction + persistence
- ``importance`` : ``score_importance``, ``classify_node_roles``
- ``topology``   : community detection + topology metrics
- ``domains``    : domain discovery and bridge expansion
- ``routing``    : domain-aware retrieval routing
"""

from wikify.wiki.graph.build import (
    build_concept_graph,
    extract_relations,
    save_relations,
    update_concept_importance,
)
from wikify.wiki.graph.domains import (
    FAST_MODEL,
    discover_domains,
    expand_via_bridges,
    get_domain_for_query,
)
from wikify.wiki.graph.importance import classify_node_roles, score_importance
from wikify.wiki.graph.routing import domain_aware_search, get_domain_context
from wikify.wiki.graph.topology import (
    compute_bridge_density,
    compute_community_gini,
    compute_inter_community_edge_ratio,
    compute_modularity,
    compute_spectral_gap,
    detect_communities,
)

__all__ = [
    "FAST_MODEL",
    "build_concept_graph",
    "classify_node_roles",
    "compute_bridge_density",
    "compute_community_gini",
    "compute_inter_community_edge_ratio",
    "compute_modularity",
    "compute_spectral_gap",
    "detect_communities",
    "discover_domains",
    "domain_aware_search",
    "expand_via_bridges",
    "extract_relations",
    "get_domain_context",
    "get_domain_for_query",
    "save_relations",
    "score_importance",
    "update_concept_importance",
]
