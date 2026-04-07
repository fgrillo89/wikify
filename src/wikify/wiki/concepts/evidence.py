"""Evidence, gap, parameter, occurrence, and relation persistence.

These functions take an agent-produced "rich extraction" payload and
persist its evidentiary side into the canonical SQL store. The payload
shape is intentionally a plain mapping so any extractor (Python or
agent) can produce it without depending on this module.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from wikify.store.db import get_session
from wikify.store.models import (
    ConceptEvidence,
    ConceptOccurrence,
    ExtractionGap,
    ParameterExtraction,
    RelationEvidence,
)
from wikify.wiki.builder import slugify

logger = logging.getLogger(__name__)


def fuzzy_match_quote(quote: str, source_text: str) -> bool:
    """Return True if ``quote`` appears in ``source_text`` modulo whitespace/punctuation."""

    if not quote or not source_text:
        return False

    def _normalize(text: str) -> str:
        text = text.lower()
        text = re.sub(r"[^\w\s]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    norm_quote = _normalize(quote)
    norm_source = _normalize(source_text)
    if not norm_quote:
        return False
    return norm_quote in norm_source


# Tests still patch the underscore-prefixed name; expose it as an alias.
_fuzzy_match_quote = fuzzy_match_quote


def store_evidence(
    rich_extractions: dict[str, list[dict[str, Any]]],
    epoch: int,
) -> int:
    """Persist evidence quotes from rich extractions, verifying against source text."""

    evidence_rows: list[ConceptEvidence] = []
    verified_count = 0
    unverified_count = 0

    for paper_id, chunk_results in rich_extractions.items():
        for chunk_result in chunk_results:
            chunk_id = chunk_result.get("_chunk_id", "")
            chunk_content = chunk_result.get("_chunk_content", "")

            for concept in chunk_result.get("concepts", []):
                if not isinstance(concept, dict):
                    continue
                name = (concept.get("name") or "").strip()
                if not name:
                    continue
                evidence = (concept.get("evidence") or "").strip()
                if not evidence:
                    continue

                verified = fuzzy_match_quote(evidence, chunk_content)
                if verified:
                    verified_count += 1
                else:
                    unverified_count += 1
                    logger.debug(
                        "store_evidence: unverified quote for %r in chunk %s",
                        name,
                        chunk_id[:16],
                    )

                canonical_id = concept.get("_canonical_id", slugify(name))
                evidence_rows.append(
                    ConceptEvidence(
                        concept_id=canonical_id,
                        paper_id=paper_id,
                        chunk_id=chunk_id,
                        evidence_quote=evidence,
                        epoch_extracted=epoch,
                        verified=verified,
                    )
                )

    if evidence_rows:
        with get_session() as session:
            for row in evidence_rows:
                session.add(row)
            session.commit()

    logger.info(
        "store_evidence: epoch %d -> %d evidence rows (%d verified, %d unverified)",
        epoch,
        len(evidence_rows),
        verified_count,
        unverified_count,
    )
    return len(evidence_rows)


def store_gaps(
    rich_extractions: dict[str, list[dict[str, Any]]],
    epoch: int,
) -> int:
    """Persist extraction gaps from rich extractions."""

    gap_rows: list[ExtractionGap] = []
    for paper_id, chunk_results in rich_extractions.items():
        for chunk_result in chunk_results:
            chunk_id = chunk_result.get("_chunk_id", "")
            for gap in chunk_result.get("gaps", []):
                if not isinstance(gap, dict):
                    continue
                description = (gap.get("description") or "").strip()
                if not description:
                    continue
                suggested_type = (gap.get("suggested_type") or "").strip()
                gap_rows.append(
                    ExtractionGap(
                        description=description,
                        suggested_type=suggested_type,
                        paper_id=paper_id,
                        chunk_id=chunk_id,
                        epoch=epoch,
                    )
                )

    if gap_rows:
        with get_session() as session:
            for row in gap_rows:
                session.add(row)
            session.commit()

    logger.info("store_gaps: epoch %d -> %d gap rows stored", epoch, len(gap_rows))
    return len(gap_rows)


def store_parameters(
    rich_extractions: dict[str, list[dict[str, Any]]],
    epoch: int,
) -> int:
    """Persist quantitative parameters from rich extractions."""

    param_rows: list[ParameterExtraction] = []
    for paper_id, chunk_results in rich_extractions.items():
        for chunk_result in chunk_results:
            for param in chunk_result.get("parameters", []):
                if not isinstance(param, dict):
                    continue
                concept_name = (param.get("concept_name") or "").strip()
                param_name = (param.get("parameter_name") or "").strip()
                value = (param.get("value") or "").strip()
                if not param_name or not value:
                    continue
                canonical_id = param.get(
                    "_canonical_id",
                    slugify(concept_name) if concept_name else "",
                )
                param_rows.append(
                    ParameterExtraction(
                        concept_id=canonical_id,
                        paper_id=paper_id,
                        parameter_name=param_name,
                        value=value,
                        unit=(param.get("unit") or "").strip(),
                        conditions=(param.get("conditions") or "").strip(),
                        evidence=(param.get("evidence") or "").strip(),
                        epoch_extracted=epoch,
                    )
                )

    if param_rows:
        with get_session() as session:
            for row in param_rows:
                session.add(row)
            session.commit()

    logger.info(
        "store_parameters: epoch %d -> %d parameter rows stored", epoch, len(param_rows)
    )
    return len(param_rows)


def store_occurrences(
    rich_extractions: dict[str, list[dict[str, Any]]],
    epoch: int,
) -> int:
    """Persist chunk-level concept mentions for graphing and routing."""

    rows: list[ConceptOccurrence] = []
    for paper_id, chunk_results in rich_extractions.items():
        for chunk_result in chunk_results:
            chunk_id = str(chunk_result.get("_chunk_id") or "")
            for concept in chunk_result.get("concepts", []):
                if not isinstance(concept, dict):
                    continue
                name = (concept.get("name") or "").strip()
                if not name:
                    continue
                canonical_id = concept.get("_canonical_id", slugify(name))
                mention_text = (
                    concept.get("evidence") or concept.get("definition") or name
                ).strip()
                rows.append(
                    ConceptOccurrence(
                        concept_id=canonical_id,
                        paper_id=paper_id,
                        chunk_id=chunk_id,
                        mention_text=mention_text,
                        weight=1.0,
                        epoch=epoch,
                    )
                )

    if rows:
        with get_session() as session:
            for row in rows:
                session.add(row)
            session.commit()

    logger.info(
        "store_occurrences: epoch %d -> %d occurrence rows stored", epoch, len(rows)
    )
    return len(rows)


def store_relation_evidence(
    rich_extractions: dict[str, list[dict[str, Any]]],
    epoch: int,
) -> int:
    """Persist evidence-backed relation candidates from rich extractions."""

    rows: list[RelationEvidence] = []
    for paper_id, chunk_results in rich_extractions.items():
        for chunk_result in chunk_results:
            chunk_id = str(chunk_result.get("_chunk_id") or "")
            for relation in chunk_result.get("relationships", []):
                if not isinstance(relation, dict):
                    continue
                source_name = (relation.get("source_concept") or "").strip()
                target_name = (relation.get("target_concept") or "").strip()
                if not source_name or not target_name:
                    continue
                rows.append(
                    RelationEvidence(
                        source_concept=relation.get(
                            "_source_canonical_id", slugify(source_name)
                        ),
                        target_concept=relation.get(
                            "_target_canonical_id", slugify(target_name)
                        ),
                        paper_id=paper_id,
                        chunk_id=chunk_id,
                        relation_type=(relation.get("relation_type") or "").strip(),
                        evidence_quote=(relation.get("evidence") or "").strip(),
                        weight=1.0,
                        epoch=epoch,
                    )
                )

    if rows:
        with get_session() as session:
            for row in rows:
                session.add(row)
            session.commit()

    logger.info(
        "store_relation_evidence: epoch %d -> %d relation evidence rows stored",
        epoch,
        len(rows),
    )
    return len(rows)


__all__ = [
    "_fuzzy_match_quote",
    "fuzzy_match_quote",
    "store_evidence",
    "store_gaps",
    "store_occurrences",
    "store_parameters",
    "store_relation_evidence",
]
