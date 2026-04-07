"""Concept co-occurrence graph construction and persistence.

Pass 2 of the wiki epoch pipeline: build a weighted directed graph of
concept co-occurrences, derive ``ConceptRelation`` rows from edges, and
persist importance scores back to the canonical store.

Sibling modules in ``wiki/graph/``:

- ``importance``: ``score_importance``, ``classify_node_roles``
- ``topology``  : community detection + topology metrics
"""

from __future__ import annotations

import logging
from collections import defaultdict

import networkx as nx
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


def build_concept_graph(domain: str, epoch: int) -> nx.DiGraph:
    """Build a co-occurrence graph for ``ConceptRecord`` rows.

    Nodes are concept slugs. Edge ``(A, B)`` exists when concepts A and B
    appear in the same source via ``ConceptOccurrence`` (preferred),
    ``ConceptEvidence``, or legacy ``SourceCoverage`` rows.
    ``RelationEvidence`` adds extra weight to direct edges.
    """

    graph = nx.DiGraph()

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
                session.exec(
                    select(SourceCoverage).where(SourceCoverage.domain == domain)
                ).all()
            )
            for row in coverage_rows:
                if row.article_slug in concept_ids:
                    source_to_concepts[row.source_id].add(row.article_slug)
        elif not source_to_concepts:
            coverage_rows = list(session.exec(select(SourceCoverage)).all())
            for row in coverage_rows:
                if row.article_slug in concept_ids:
                    source_to_concepts[row.source_id].add(row.article_slug)

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


# ── Relation extraction ──────────────────────────────────────────────────────


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
    direct = _RELATION_MAP.get((src_type, tgt_type))
    if direct:
        return direct
    return "RELATED-TO"


def extract_relations(graph: nx.DiGraph, epoch: int) -> list[ConceptRelation]:
    """Derive ``ConceptRelation`` rows from graph edges."""

    relations: list[ConceptRelation] = []
    for src, tgt, edge_data in graph.edges(data=True):
        src_type: str = graph.nodes[src].get("concept_type", "")
        tgt_type: str = graph.nodes[tgt].get("concept_type", "")
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


# ── Persistence helpers ──────────────────────────────────────────────────────


def update_concept_importance(scores: dict[str, float]) -> None:
    """Persist importance scores back to ``ConceptRecord`` rows."""

    if not scores:
        return

    with get_session() as session:
        for cid, value in scores.items():
            results = list(
                session.exec(select(ConceptRecord).where(ConceptRecord.id == cid)).all()
            )
            if results:
                record = results[0]
                record.importance = value
                session.add(record)
        session.commit()
    logger.info("update_concept_importance: updated %d concepts", len(scores))


def save_relations(relations: list[ConceptRelation], epoch: int) -> int:
    """Atomically replace ``ConceptRelation`` rows for ``epoch``."""

    if not relations:
        logger.info("save_relations: no relations to save for epoch=%d", epoch)
        return 0

    with get_session() as session:
        existing = list(
            session.exec(select(ConceptRelation).where(ConceptRelation.epoch == epoch)).all()
        )
        for row in existing:
            session.delete(row)
        session.flush()
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


__all__ = [
    "build_concept_graph",
    "extract_relations",
    "save_relations",
    "update_concept_importance",
]
