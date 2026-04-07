"""Built-in discovery node implementations.

These nodes are deliberately small and side-effect free so they are easy
to test and compose. They cover the default discovery shape:

- ``profile_document``    -> ``DocumentProfile``
- ``plan_units``          -> ``list[ExtractionUnit]``
- ``extract_text``        -> ``list[ExtractionNote]`` (text/chunk units)
- ``extract_multimodal``  -> ``list[ExtractionNote]`` (figure/table/slide units)
- ``resolve_candidates``  -> ``list[CandidateConcept]``
- ``persist_notes``       -> coverage update via injected sink

The extract nodes do not call any LLM SDK. They delegate to an
``AgentExtractor`` supplied through ``params["extractor"]``. In an
agentic runtime the orchestrating agent provides the extractor; in
tests, ``EchoExtractor`` is used.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from wikify.wiki.discovery.contracts import (
    CandidateConcept,
    CoverageRecord,
    DocumentProfile,
    ExtractionNote,
    ExtractionUnit,
    ModalityKind,
)
from wikify.wiki.discovery.extractors import AgentExtractor, EchoExtractor
from wikify.wiki.discovery.units import plan_units_for_profile


def _profile_document(inputs: Mapping[str, Any], params: Mapping[str, Any]) -> Mapping[str, Any]:
    doc = inputs["document"]
    profile = DocumentProfile(
        document_id=doc["id"],
        document_type=doc.get("type", "unknown"),
        parser_confidence=float(doc.get("parser_confidence", 1.0)),
        structural_sections=list(doc.get("sections", [])),
        modalities=[ModalityKind(m) for m in doc.get("modalities", ["text"])],
        token_budget_hint=doc.get("token_budget_hint"),
        priority=float(doc.get("priority", 0.0)),
        metadata=dict(doc.get("metadata", {})),
    )
    return {"profile": profile}


def _plan_units(inputs: Mapping[str, Any], params: Mapping[str, Any]) -> Mapping[str, Any]:
    profile: DocumentProfile = inputs["profile"]
    doc = inputs["document"]
    units = plan_units_for_profile(
        profile,
        chunks=doc.get("chunks"),
        figures=doc.get("figures"),
        tables=doc.get("tables"),
        slides=doc.get("slides"),
        synopsis=doc.get("synopsis"),
    )
    chunk_budget = int(params.get("chunk_budget", 0))
    if chunk_budget > 0:
        text_units = [u for u in units if u.modality == ModalityKind.TEXT][:chunk_budget]
        other = [u for u in units if u.modality != ModalityKind.TEXT]
        units = text_units + other
    return {"units": units}


def _resolve_extractor(params: Mapping[str, Any]) -> AgentExtractor:
    return params.get("extractor") or EchoExtractor()


def _extract_text(inputs: Mapping[str, Any], params: Mapping[str, Any]) -> Mapping[str, Any]:
    units: list[ExtractionUnit] = list(inputs["units"])
    extractor = _resolve_extractor(params)
    notes = extractor.extract(
        units,
        strategy_id=str(params.get("strategy_id", "default")),
        node_id=str(params.get("node_id", "extract_text")),
        modalities=(ModalityKind.TEXT,),
    )
    return {"text_notes": notes}


def _extract_multimodal(inputs: Mapping[str, Any], params: Mapping[str, Any]) -> Mapping[str, Any]:
    units: list[ExtractionUnit] = list(inputs["units"])
    extractor = _resolve_extractor(params)
    notes = extractor.extract(
        units,
        strategy_id=str(params.get("strategy_id", "default")),
        node_id=str(params.get("node_id", "extract_multimodal")),
        modalities=(ModalityKind.IMAGE, ModalityKind.TABLE),
    )
    return {"multimodal_notes": notes}


def _resolve_candidates(inputs: Mapping[str, Any], params: Mapping[str, Any]) -> Mapping[str, Any]:
    text_notes: list[ExtractionNote] = list(inputs.get("text_notes", []))
    mm_notes: list[ExtractionNote] = list(inputs.get("multimodal_notes", []))
    all_notes = text_notes + mm_notes
    candidates: list[CandidateConcept] = []
    for note in all_notes:
        wi = note.content.get("work_item") or {}
        candidates.append(
            CandidateConcept(
                name=note.content.get("name", note.note_id),
                kind=wi.get("unit_kind", "concept"),
                document_id=note.document_id,
                unit_ids=list(note.unit_ids),
                note_ids=[note.note_id],
                confidence=note.confidence,
            )
        )
    return {"candidates": candidates, "all_notes": all_notes}


def _persist_notes(inputs: Mapping[str, Any], params: Mapping[str, Any]) -> Mapping[str, Any]:
    notes: list[ExtractionNote] = list(inputs.get("all_notes", []))
    sink = params.get("sink")
    if sink is not None:
        sink.write_many(notes)

    coverage = CoverageRecord(
        document_id=str(params.get("document_id", "")),
        strategy_id=str(params.get("strategy_id", "default")),
        epoch=int(params.get("epoch", 0)),
        last_touched=time.time(),
    )
    for note in notes:
        for uid in note.unit_ids:
            coverage.mark_processed(uid)
    return {"coverage": coverage}


def register_builtin_nodes(registry) -> None:
    registry.register("profile_document", _profile_document)
    registry.register("plan_units", _plan_units)
    registry.register("extract_text", _extract_text)
    registry.register("extract_multimodal", _extract_multimodal)
    registry.register("resolve_candidates", _resolve_candidates)
    registry.register("persist_notes", _persist_notes)
