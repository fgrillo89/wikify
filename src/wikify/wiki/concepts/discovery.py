"""Agent-native concept discovery driver.

Replaces the legacy LLM-calling extraction pipeline. This module is the
boundary between the wiki runtime and the orchestrating agent: it
gathers chunks, builds extraction units, asks an injected
``AgentExtractor`` to interrogate them, and converts the resulting
notes into canonical ``ConceptRecord`` rows via ``merge``.

No LLM SDK is imported here. The agent driving the wiki runtime
supplies the extractor; the default ``EchoExtractor`` is a deterministic
no-op suitable for tests and for surfacing "no agent wired in" cleanly.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlmodel import select

from wikify.store.db import get_session
from wikify.store.models import Chunk, ConceptRecord
from wikify.wiki.builder import slugify
from wikify.wiki.concepts.merge import (
    apply_redirect_map,
    merge_concept_records,
)
from wikify.wiki.concepts.records import DiscoveryResult
from wikify.wiki.discovery.contracts import (
    DocumentProfile,
    ExtractionNote,
    ExtractionUnit,
    ModalityKind,
    UnitKind,
)
from wikify.wiki.discovery.extractors import AgentExtractor, EchoExtractor

logger = logging.getLogger(__name__)

# Sections that never yield extractable concepts.
_SKIP_SECTIONS = frozenset({"references", "acknowledgments", "appendix"})

_STRATEGY_ID = "agent_native_publication"


def _chunks_for_paper(paper_id: str) -> list[Chunk]:
    with get_session() as session:
        return list(
            session.exec(
                select(Chunk)
                .where(Chunk.paper_id == paper_id)
                .order_by(Chunk.chunk_index)  # type: ignore[arg-type]
            ).all()
        )


def _build_units(paper_id: str, chunks: list[Chunk]) -> list[ExtractionUnit]:
    units: list[ExtractionUnit] = []
    for chunk in chunks:
        if chunk.section_type in _SKIP_SECTIONS:
            continue
        if len(chunk.content) <= 50:
            continue
        units.append(
            ExtractionUnit(
                unit_id=f"{paper_id}:chunk:{chunk.id}",
                document_id=paper_id,
                kind=UnitKind.CHUNK,
                modality=ModalityKind.TEXT,
                payload=chunk.content,
                section=chunk.section_type,
                metadata={"chunk_id": chunk.id, "chunk_index": chunk.chunk_index},
            )
        )
    return units


def _notes_to_concept_records(
    notes: list[ExtractionNote], epoch: int
) -> tuple[list[ConceptRecord], dict[str, list[dict[str, Any]]]]:
    """Translate agent-produced notes into canonical concept records.

    The agent is expected to return notes whose ``content`` includes a
    ``concepts`` list following the schema:

        {"name": str, "type": str, "aliases": [str], "definition": str,
         "evidence": str, "_chunk_id": str, "_chunk_content": str}

    Notes that do not carry a ``concepts`` list are still persisted as
    coverage records by the discovery DAG; they simply do not contribute
    to the canonical concept table.
    """

    records: list[ConceptRecord] = []
    rich: dict[str, list[dict[str, Any]]] = {}
    for note in notes:
        concepts_payload = note.content.get("concepts")
        if not isinstance(concepts_payload, list):
            continue

        chunk_id = ""
        chunk_content = ""
        if note.unit_ids:
            chunk_id = note.content.get("_chunk_id") or note.unit_ids[0]
            chunk_content = note.content.get("_chunk_content", "")

        rich.setdefault(note.document_id, []).append(
            {
                "_chunk_id": chunk_id,
                "_paper_id": note.document_id,
                "_chunk_content": chunk_content,
                "concepts": concepts_payload,
                "parameters": note.content.get("parameters", []),
                "mechanisms": note.content.get("mechanisms", []),
                "relationships": note.content.get("relationships", []),
                "gaps": note.content.get("gaps", []),
            }
        )

        for item in concepts_payload:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip()
            if not name:
                continue
            aliases_raw = item.get("aliases") or []
            aliases = [str(a).strip() for a in aliases_raw if str(a).strip()]
            records.append(
                ConceptRecord(
                    id=slugify(name),
                    name=name,
                    aliases=json.dumps(aliases),
                    definition=(item.get("definition") or "").strip(),
                    concept_type=(item.get("type") or "").strip().lower(),
                    epoch_discovered=epoch,
                    epoch_last_updated=epoch,
                )
            )

    return records, rich


def discover_concepts(
    paper_ids: list[str],
    epoch: int,
    *,
    extractor: AgentExtractor | None = None,
) -> DiscoveryResult:
    """Run agent-native concept discovery for ``paper_ids``.

    The orchestrating agent supplies ``extractor``. When omitted, an
    ``EchoExtractor`` is used; this produces no canonical concepts and
    cleanly surfaces that no agent has been wired into the runtime.
    """

    if not paper_ids:
        logger.info("discover_concepts: no paper_ids provided, nothing to do")
        return DiscoveryResult()

    extractor = extractor or EchoExtractor(agent_label="no-agent-configured")
    logger.info(
        "discover_concepts: epoch %d, %d papers, extractor=%s",
        epoch,
        len(paper_ids),
        type(extractor).__name__,
    )

    all_notes: list[ExtractionNote] = []
    for paper_id in paper_ids:
        chunks = _chunks_for_paper(paper_id)
        units = _build_units(paper_id, chunks)
        if not units:
            continue
        # Profile is informational here; the agent decides how to handle the units.
        _profile = DocumentProfile(document_id=paper_id, document_type="publication")
        notes = extractor.extract(
            units,
            strategy_id=_STRATEGY_ID,
            node_id="discover_concepts",
            modalities=(ModalityKind.TEXT,),
        )
        all_notes.extend(notes)

    records, rich_extractions = _notes_to_concept_records(all_notes, epoch)
    new_count, redirect_map = merge_concept_records(records, epoch)
    if redirect_map:
        apply_redirect_map(rich_extractions, redirect_map)

    with get_session() as session:
        results: list[ConceptRecord] = list(
            session.exec(
                select(ConceptRecord).where(ConceptRecord.epoch_last_updated == epoch)
            ).all()
        )

    logger.info(
        "discover_concepts: epoch %d complete -> %d new, %d total touched",
        epoch,
        new_count,
        len(results),
    )
    return DiscoveryResult(
        concepts=results,
        rich_extractions=rich_extractions,
        redirect_map=redirect_map,
    )


__all__ = ["discover_concepts", "DiscoveryResult"]
