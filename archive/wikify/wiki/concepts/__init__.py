"""Canonical concept persistence boundary.

Owns canonical concept records, merge/dedup, evidence persistence, and
the agent-native discovery driver. The wiki ``discovery`` subsystem
decides what to read; this package decides how to merge agent-produced
extraction notes into the canonical store.

Internal layout:

- ``records``   : ``DiscoveryResult``, concept lookups
- ``merge``     : merge/dedup, redirect map, ChromaDB staging
- ``evidence``  : evidence/gap/parameter/occurrence/relation persistence
- ``discovery`` : agent-native ``discover_concepts`` driver
"""

from __future__ import annotations

from wikify.wiki.concepts.discovery import discover_concepts
from wikify.wiki.concepts.evidence import (
    fuzzy_match_quote,
    store_evidence,
    store_gaps,
    store_occurrences,
    store_parameters,
    store_relation_evidence,
)
from wikify.wiki.concepts.merge import (
    apply_redirect_map,
    clear_staged_extractions,
    commit_staged_extractions,
    merge_concept_records,
    stage_extractions,
)
from wikify.wiki.concepts.records import (
    DiscoveryResult,
    get_concept_by_name,
    list_concepts,
)

__all__ = [
    "DiscoveryResult",
    "apply_redirect_map",
    "clear_staged_extractions",
    "commit_staged_extractions",
    "discover_concepts",
    "fuzzy_match_quote",
    "get_concept_by_name",
    "list_concepts",
    "merge_concept_records",
    "stage_extractions",
    "store_evidence",
    "store_gaps",
    "store_occurrences",
    "store_parameters",
    "store_relation_evidence",
]
