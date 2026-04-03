"""Structured concept vectors for improved similarity and deduplication.

Instead of embedding plain concept names, encodes structure (type,
relations, parameters) into the embedding string. This captures
structural differences that plain name embeddings miss.

Example:
    "Atomic Layer Deposition | type:technique | enables:RRAM,HfO2 | params:growth_rate=1.0_A/cycle"
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlmodel import select

from wikify.store.db import get_session

if TYPE_CHECKING:
    from wikify.store.models import ConceptRecord

logger = logging.getLogger(__name__)


def build_structured_text(concept: ConceptRecord) -> str:
    """Build a structured embedding string for a concept.

    Encodes the concept's type, related concepts (from ConceptRelation),
    and extracted parameters into a single string for embedding.

    Args:
        concept: A ConceptRecord instance.

    Returns:
        Structured string suitable for embedding, e.g.:
        "Atomic Layer Deposition | type:technique | enables:RRAM,HfO2"
    """
    from wikify.store.models import ConceptRelation, ParameterExtraction

    parts = [concept.name]

    # Type
    if concept.concept_type:
        parts.append(f"type:{concept.concept_type}")

    # Definition (compact)
    if concept.definition:
        defn = concept.definition[:80]
        parts.append(f"def:{defn}")

    # Relations (grouped by type)
    try:
        with get_session() as session:
            relations: list[ConceptRelation] = list(
                session.exec(
                    select(ConceptRelation).where(ConceptRelation.source_concept == concept.id)
                ).all()
            )
    except Exception:
        logger.debug(
            "build_structured_text: could not load relations for %s",
            concept.id,
        )
        relations = []

    if relations:
        # Group by relation type
        by_type: dict[str, list[str]] = {}
        for rel in relations:
            rtype = rel.relation_type.lower().replace("-", "_")
            by_type.setdefault(rtype, []).append(rel.target_concept)

        for rtype, targets in sorted(by_type.items()):
            target_str = ",".join(targets[:5])  # cap at 5 per type
            parts.append(f"{rtype}:{target_str}")

    # Parameters (top 3 by uniqueness)
    try:
        with get_session() as session:
            params: list[ParameterExtraction] = list(
                session.exec(
                    select(ParameterExtraction).where(ParameterExtraction.concept_id == concept.id)
                ).all()
            )
    except Exception:
        logger.debug(
            "build_structured_text: could not load params for %s",
            concept.id,
        )
        params = []

    if params:
        seen_params: set[str] = set()
        param_parts: list[str] = []
        for p in params:
            key = p.parameter_name
            if key in seen_params:
                continue
            seen_params.add(key)
            val = f"{p.value}_{p.unit}" if p.unit else p.value
            param_parts.append(f"{key}={val}")
            if len(param_parts) >= 3:
                break
        if param_parts:
            parts.append(f"params:{','.join(param_parts)}")

    return " | ".join(parts)


def build_structured_texts(
    concepts: list[ConceptRecord],
) -> list[str]:
    """Build structured embedding strings for a batch of concepts.

    Args:
        concepts: List of ConceptRecord instances.

    Returns:
        List of structured strings, one per concept, in the same order.
    """
    return [build_structured_text(c) for c in concepts]
