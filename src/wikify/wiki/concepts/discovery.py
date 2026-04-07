"""Agent-native concept discovery driver.

Replaces the legacy LLM-calling extraction pipeline. This module is the
boundary between the wiki runtime and the orchestrating agent: it
gathers chunks, builds extraction units, asks an injected
``AgentExtractor`` to interrogate them, and converts the resulting
notes into canonical ``ConceptRecord`` rows via ``merge``.

It also supports an optional DAG-backed execution path for runtimes
that compile a discovery workflow ahead of time. The workflow path keeps
the same concept-merge behavior while letting a per-document DAG seed
drive note generation.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from sqlmodel import select

from wikify.core.store.db import get_session
from wikify.core.store.models import Chunk, ConceptRecord, Figure, Paper
from wikify.wiki.builder import slugify
from wikify.wiki.concepts.merge import (
    apply_redirect_map,
    merge_concept_records,
)
from wikify.wiki.concepts.records import DiscoveryResult
from wikify.wiki.discovery.contracts import (
    ArtifactRef,
    DagRunSpec,
    ExtractionNote,
    ExtractionUnit,
    ModalityKind,
    UnitKind,
)
from wikify.wiki.discovery.executor import DagExecutor
from wikify.wiki.discovery.extractors import AgentExtractor, EchoExtractor

logger = logging.getLogger(__name__)

# Sections that never yield extractable concepts.
_SKIP_SECTIONS = frozenset({"references", "acknowledgments", "appendix"})

_STRATEGY_ID = "agent_native_publication"
_MIN_CHUNK_CHARS = 50


def _safe_json_loads(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list, tuple)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def _chunks_for_paper(paper_id: str) -> list[Chunk]:
    with get_session() as session:
        return list(
            session.exec(
                select(Chunk)
                .where(Chunk.paper_id == paper_id)
                .order_by(Chunk.chunk_index)  # type: ignore[arg-type]
            ).all()
        )


def _paper_and_related_rows(
    paper_id: str,
) -> tuple[Paper | None, list[Chunk], list[Figure]]:
    with get_session() as session:
        paper = session.get(Paper, paper_id)
        chunks = list(
            session.exec(
                select(Chunk)
                .where(Chunk.paper_id == paper_id)
                .order_by(Chunk.chunk_index)  # type: ignore[arg-type]
            ).all()
        )
        figures = list(
            session.exec(
                select(Figure)
                .where(Figure.paper_id == paper_id)
                .order_by(Figure.page_number, Figure.id)  # type: ignore[arg-type]
            ).all()
        )
    return paper, chunks, figures


def _usable_chunks(chunks: list[Chunk]) -> list[Chunk]:
    usable: list[Chunk] = []
    for chunk in chunks:
        if chunk.section_type in _SKIP_SECTIONS:
            continue
        if len(chunk.content) <= _MIN_CHUNK_CHARS:
            continue
        usable.append(chunk)
    return usable


def _section_paths_from_tree(tree: Any, prefix: tuple[str, ...] = ()) -> list[str]:
    if not isinstance(tree, dict):
        return []

    paths: list[str] = []
    title = str(tree.get("title") or "").strip()
    current_prefix = prefix
    if title:
        current_prefix = prefix + (title,)
        paths.append(".".join(current_prefix))

    children = tree.get("children")
    if isinstance(children, list):
        for child in children:
            paths.extend(_section_paths_from_tree(child, current_prefix))
    return paths


def _unique_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered


def _document_type_for_paper(paper: Paper | None) -> str:
    if paper is None:
        return "unknown"
    raw = str(getattr(paper, "doc_type", "") or "").lower()
    if raw == "presentation":
        return "slide_deck"
    if raw in {"markdown", "web_article", "wiki_article", "repo_readme", "note"}:
        return "markdown"
    if raw in {"paper", "report", "proposal", "other", ""}:
        return "publication" if raw != "other" else "unknown"
    return "publication"


def _section_list(
    paper: Paper | None,
    chunks: list[Chunk],
    section_tree: Any,
    section_summaries: Mapping[str, Any],
) -> list[str]:
    section_paths: list[str] = []
    section_paths.extend(section_summaries.keys())
    section_paths.extend(_section_paths_from_tree(section_tree))
    section_paths.extend(chunk.section_path for chunk in chunks if chunk.section_path)
    if paper is not None and paper.summary and paper.summary.strip():
        section_paths.append("synopsis")
    return _unique_strings(section_paths)


def _synopsis_for_document(
    paper: Paper | None,
    chunks: list[Chunk],
    section_summaries: Mapping[str, Any],
) -> str:
    if paper is not None and paper.summary and paper.summary.strip():
        return paper.summary.strip()

    for key in ("abstract", "summary", "introduction", "overview"):
        value = section_summaries.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for value in section_summaries.values():
        if isinstance(value, str) and value.strip():
            return value.strip()

    for chunk in chunks:
        content = chunk.content.strip()
        if content:
            return content[:3000]

    return ""


def _chunk_payload(chunk: Chunk) -> dict[str, Any]:
    return {
        "id": chunk.id,
        "text": chunk.content,
        "content": chunk.content,
        "section_path": chunk.section_path,
        "section_type": chunk.section_type,
        "chunk_index": chunk.chunk_index,
        "token_count": chunk.token_count,
        "has_citations": chunk.has_citations,
        "has_equations": chunk.has_equations,
        "metadata": {
            "paper_id": chunk.paper_id,
            "section_type": chunk.section_type,
            "chunk_index": chunk.chunk_index,
            "token_count": chunk.token_count,
            "has_citations": chunk.has_citations,
            "has_equations": chunk.has_equations,
        },
    }


def _figure_payload(figure: Figure) -> dict[str, Any]:
    tags = _safe_json_loads(figure.tags, [])
    bbox = _safe_json_loads(figure.bbox, figure.bbox)
    extracted_data = _safe_json_loads(figure.extracted_data, figure.extracted_data)
    return {
        "id": figure.id,
        "caption": figure.caption or "",
        "figure_number": figure.figure_number,
        "section_path": figure.section_path,
        "image_path": figure.image_path,
        "width_px": figure.width_px,
        "height_px": figure.height_px,
        "format": figure.format,
        "tags": tags if isinstance(tags, list) else [tags],
        "extracted_data": extracted_data,
        "reuse_count": figure.reuse_count,
        "media_type": figure.media_type,
        "label": figure.label,
        "page_number": figure.page_number,
        "bbox": bbox,
        "llm_description": figure.llm_description,
    }


def _table_payload(figure: Figure) -> dict[str, Any]:
    payload = _figure_payload(figure)
    extracted_data = payload.get("extracted_data")
    rows: Any = []
    if isinstance(extracted_data, dict):
        rows = extracted_data.get("rows") or extracted_data.get("data_points") or []
    elif isinstance(extracted_data, list):
        rows = extracted_data
    payload["rows"] = rows
    payload["caption"] = figure.caption or ""
    return payload


def _document_payload_for_paper(paper_id: str) -> dict[str, Any]:
    paper, chunks, figures = _paper_and_related_rows(paper_id)
    usable_chunks = _usable_chunks(chunks)
    section_tree = _safe_json_loads(paper.section_tree if paper is not None else None, {})
    section_summaries = _safe_json_loads(
        paper.section_summaries if paper is not None else None,
        {},
    )
    if not isinstance(section_tree, dict):
        section_tree = {}
    if not isinstance(section_summaries, dict):
        section_summaries = {}

    table_figures = [figure for figure in figures if figure.media_type == "table"]
    image_figures = [figure for figure in figures if figure.media_type != "table"]
    synopsis = _synopsis_for_document(paper, usable_chunks, section_summaries)
    sections = _section_list(paper, usable_chunks, section_tree, section_summaries)

    paper_metadata = {
        "paper_id": paper_id,
        "title": paper.title if paper is not None else "",
        "authors": paper.parsed_authors if paper is not None else [],
        "year": paper.year if paper is not None else None,
        "doi": paper.doi if paper is not None else None,
        "source_path": paper.source_path if paper is not None else "",
        "file_hash": paper.file_hash if paper is not None else "",
        "doc_type": str(paper.doc_type) if paper is not None else "unknown",
        "origin": str(paper.origin) if paper is not None else "",
        "zotero_key": paper.zotero_key if paper is not None else None,
    }

    return {
        "id": paper_id,
        "type": _document_type_for_paper(paper),
        "parser_confidence": 1.0 if paper is not None else 0.0,
        "sections": sections,
        "modalities": _unique_strings(
            [
                "text" if usable_chunks or synopsis else "",
                "image" if image_figures else "",
                "table" if table_figures else "",
            ]
        ),
        "synopsis": synopsis,
        "chunks": [_chunk_payload(chunk) for chunk in usable_chunks],
        "figures": [_figure_payload(figure) for figure in image_figures],
        "tables": [_table_payload(figure) for figure in table_figures],
        "metadata": {
            "paper": paper_metadata,
            "section_tree": section_tree,
            "section_summaries": section_summaries,
            "chunk_count": len(usable_chunks),
            "figure_count": len(image_figures),
            "table_count": len(table_figures),
        },
    }


def _runtime_spec_for_document(
    spec: DagRunSpec,
    *,
    document_id: str,
    extractor: AgentExtractor,
    epoch: int,
) -> DagRunSpec:
    runtime_params = {
        "extractor": extractor,
        "epoch": epoch,
        "document_id": document_id,
        "strategy_id": spec.strategy_id,
    }
    nodes = tuple(
        replace(
            node,
            params={
                **dict(spec.params),
                **dict(node.params),
                **runtime_params,
            },
        )
        for node in spec.nodes
    )
    return replace(
        spec,
        nodes=nodes,
        params={**dict(spec.params), **runtime_params},
    )


def _artifact_snapshot(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    artifacts = getattr(result, "artifacts", None)
    if isinstance(artifacts, dict):
        return artifacts
    if isinstance(result, Mapping):
        return dict(result)
    return {}


def _coerce_note(raw: Any) -> ExtractionNote | None:
    if isinstance(raw, ExtractionNote):
        return raw
    if isinstance(raw, Mapping):
        data = dict(raw)
    else:
        fields = (
            "note_id",
            "document_id",
            "unit_ids",
            "strategy_id",
            "node_id",
            "content",
            "confidence",
            "model",
            "created_at",
        )
        if not all(hasattr(raw, field) for field in fields):
            return None
        data = {field: getattr(raw, field) for field in fields}

    unit_ids_raw = data.get("unit_ids") or []
    if isinstance(unit_ids_raw, str):
        unit_ids_raw = [unit_ids_raw]
    elif not isinstance(unit_ids_raw, list):
        unit_ids_raw = list(unit_ids_raw)
    content_raw = data.get("content") or {}
    if not isinstance(content_raw, Mapping):
        return None
    try:
        confidence = float(data.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    try:
        created_at = float(data.get("created_at", 0.0) or 0.0)
    except (TypeError, ValueError):
        created_at = 0.0

    return ExtractionNote(
        note_id=str(data.get("note_id", "")),
        document_id=str(data.get("document_id", "")),
        unit_ids=[str(uid) for uid in unit_ids_raw],
        strategy_id=str(data.get("strategy_id", "")),
        node_id=str(data.get("node_id", "")),
        content=dict(content_raw),
        confidence=confidence,
        model=data.get("model"),
        created_at=created_at,
    )


def _notes_from_artifacts(artifacts: Mapping[str, Any]) -> list[ExtractionNote]:
    if "all_notes" in artifacts:
        raw_notes = artifacts.get("all_notes") or []
        candidates = raw_notes if isinstance(raw_notes, list) else list(raw_notes)
    else:
        candidates = []
        for key, value in artifacts.items():
            if key == "all_notes":
                continue
            if not isinstance(value, (list, tuple)):
                continue
            note_items = [_coerce_note(item) for item in value]
            if not note_items or any(note is None for note in note_items):
                continue
            candidates.extend(note_items)
        return _unique_notes(candidates)

    notes = [_coerce_note(item) for item in candidates]
    return _unique_notes([note for note in notes if note is not None])


def _unique_notes(notes: list[ExtractionNote]) -> list[ExtractionNote]:
    seen: set[str] = set()
    unique: list[ExtractionNote] = []
    for note in notes:
        if note.note_id in seen:
            continue
        seen.add(note.note_id)
        unique.append(note)
    return unique


def _coerce_id_set(raw: Any) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, str):
        cleaned = raw.strip()
        return {cleaned} if cleaned else set()
    if isinstance(raw, (list, tuple, set)):
        return {str(item).strip() for item in raw if str(item).strip()}
    return set()


def _iter_result_timings(result: Any) -> list[tuple[str, float]]:
    timings_raw = getattr(result, "timings", None)
    if not timings_raw:
        return []

    rows: list[tuple[str, float]] = []
    for item in timings_raw:
        node_id = getattr(item, "node_id", None)
        duration = getattr(item, "duration_s", None)
        if node_id is None and isinstance(item, Mapping):
            node_id = item.get("node_id")
            duration = item.get("duration_s")
        if not node_id:
            continue
        try:
            duration_s = float(duration or 0.0)
        except (TypeError, ValueError):
            duration_s = 0.0
        rows.append((str(node_id), duration_s))
    return rows


def _unit_id(raw: Any) -> str:
    if isinstance(raw, ExtractionUnit):
        return raw.unit_id.strip()
    if isinstance(raw, Mapping):
        unit_id = raw.get("unit_id")
        if unit_id is None:
            unit_id = raw.get("id")
        return str(unit_id or "").strip()
    unit_id = getattr(raw, "unit_id", None)
    if unit_id is None:
        unit_id = getattr(raw, "id", None)
    return str(unit_id or "").strip()


def _planned_unit_ids_from_artifacts(
    artifacts: Mapping[str, Any],
    runtime_spec: DagRunSpec,
) -> set[str]:
    unit_keys = {
        ref.key
        for node in runtime_spec.nodes
        for ref in node.outputs.values()
        if ref.kind == "units"
    }
    planned_ids: set[str] = set()
    for key in unit_keys:
        raw_units = artifacts.get(key)
        if not isinstance(raw_units, (list, tuple, set)):
            continue
        for raw in raw_units:
            unit_id = _unit_id(raw)
            if unit_id:
                planned_ids.add(unit_id)
    return planned_ids


def _coverage_sets_from_artifacts(
    artifacts: Mapping[str, Any],
    notes: list[ExtractionNote],
    planned_ids: set[str],
) -> tuple[set[str], set[str], set[str]]:
    coverage = artifacts.get("coverage")
    processed_ids: set[str] = set()
    deferred_ids: set[str] = set()
    failed_ids: set[str] = set()

    if coverage is not None:
        if isinstance(coverage, Mapping):
            processed_ids = _coerce_id_set(coverage.get("processed_unit_ids"))
            deferred_ids = _coerce_id_set(coverage.get("deferred_unit_ids"))
            failed_ids = _coerce_id_set(coverage.get("failed_unit_ids"))
        else:
            processed_ids = _coerce_id_set(getattr(coverage, "processed_unit_ids", None))
            deferred_ids = _coerce_id_set(getattr(coverage, "deferred_unit_ids", None))
            failed_ids = _coerce_id_set(getattr(coverage, "failed_unit_ids", None))

    if not processed_ids:
        for note in notes:
            processed_ids.update(uid for uid in note.unit_ids if uid)

    if planned_ids:
        deferred_ids = deferred_ids | (planned_ids - processed_ids - failed_ids)

    return processed_ids, deferred_ids, failed_ids


def _init_discovery_telemetry(mode: str, total_documents: int) -> dict[str, Any]:
    return {
        "mode": mode,
        "documents_total": total_documents,
        "documents_processed": 0,
        "documents_skipped": 0,
        "documents_with_deferred": 0,
        "units_planned": 0,
        "units_processed": 0,
        "units_deferred": 0,
        "units_failed": 0,
        "node_runs": {},
        "node_timing_s": {},
    }


def _record_node_timings(telemetry: dict[str, Any], result: Any) -> None:
    node_runs = telemetry["node_runs"]
    node_timing_s = telemetry["node_timing_s"]
    for node_id, duration_s in _iter_result_timings(result):
        node_runs[node_id] = int(node_runs.get(node_id, 0)) + 1
        node_timing_s[node_id] = float(node_timing_s.get(node_id, 0.0)) + duration_s


def _build_units(paper_id: str, chunks: list[Chunk]) -> list[ExtractionUnit]:
    units: list[ExtractionUnit] = []
    for chunk in _usable_chunks(chunks):
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
    allow_echo_extractor: bool = False,
    workflow_spec: DagRunSpec | None = None,
    dag_executor: DagExecutor | None = None,
) -> DiscoveryResult:
    """Run agent-native concept discovery for ``paper_ids``.

    The orchestrating agent supplies ``extractor``. By default this
    function fails fast when no extractor is provided, so production
    runs cannot silently no-op concept discovery. Tests and dry-runs can
    opt into ``EchoExtractor`` via ``allow_echo_extractor=True``.
    """

    if not paper_ids:
        logger.info("discover_concepts: no paper_ids provided, nothing to do")
        return DiscoveryResult()

    if extractor is None:
        if not allow_echo_extractor:
            raise RuntimeError(
                "discover_concepts requires an explicit extractor; "
                "pass extractor=... or allow_echo_extractor=True for tests/dry-run."
            )
        extractor = EchoExtractor(agent_label="no-agent-configured")

    if (workflow_spec is None) != (dag_executor is None):
        raise RuntimeError(
            "discover_concepts requires both workflow_spec and dag_executor "
            "when DAG-backed execution is enabled."
        )

    logger.info(
        "discover_concepts: epoch %d, %d papers, extractor=%s, workflow=%s",
        epoch,
        len(paper_ids),
        type(extractor).__name__,
        workflow_spec.workflow_id if workflow_spec is not None else "<fallback>",
    )

    discovery_telemetry = _init_discovery_telemetry(
        mode="dag" if workflow_spec is not None else "extractor",
        total_documents=len(paper_ids),
    )
    all_notes: list[ExtractionNote] = []
    for paper_id in paper_ids:
        if workflow_spec is None or dag_executor is None:
            chunks = _chunks_for_paper(paper_id)
            units = _build_units(paper_id, chunks)
            if not units:
                discovery_telemetry["documents_skipped"] += 1
                continue
            notes = extractor.extract(
                units,
                strategy_id=_STRATEGY_ID,
                node_id="discover_concepts",
                modalities=(ModalityKind.TEXT,),
            )
            all_notes.extend(notes)
            discovery_telemetry["documents_processed"] += 1
            unit_count = len(units)
            discovery_telemetry["units_planned"] += unit_count
            discovery_telemetry["units_processed"] += unit_count
            continue

        document_payload = _document_payload_for_paper(paper_id)
        runtime_spec = _runtime_spec_for_document(
            workflow_spec,
            document_id=paper_id,
            extractor=extractor,
            epoch=epoch,
        )
        result = dag_executor.run(
            runtime_spec,
            seed_artifacts={
                "document": (ArtifactRef("document", "document"), document_payload)
            },
        )
        artifacts = _artifact_snapshot(result)
        notes = _notes_from_artifacts(artifacts)
        all_notes.extend(notes)
        discovery_telemetry["documents_processed"] += 1
        _record_node_timings(discovery_telemetry, result)

        planned_ids = _planned_unit_ids_from_artifacts(artifacts, runtime_spec)
        processed_ids, deferred_ids, failed_ids = _coverage_sets_from_artifacts(
            artifacts,
            notes,
            planned_ids,
        )
        if not planned_ids and not processed_ids and not deferred_ids and not failed_ids:
            discovery_telemetry["documents_skipped"] += 1
            continue
        if deferred_ids:
            discovery_telemetry["documents_with_deferred"] += 1
        discovery_telemetry["units_planned"] += len(planned_ids)
        discovery_telemetry["units_processed"] += len(processed_ids)
        discovery_telemetry["units_deferred"] += len(deferred_ids)
        discovery_telemetry["units_failed"] += len(failed_ids)

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
        "discover_concepts: epoch %d complete -> %d new, %d total touched "
        "(mode=%s, units processed=%d deferred=%d)",
        epoch,
        new_count,
        len(results),
        discovery_telemetry.get("mode", ""),
        int(discovery_telemetry.get("units_processed", 0)),
        int(discovery_telemetry.get("units_deferred", 0)),
    )
    return DiscoveryResult(
        concepts=results,
        rich_extractions=rich_extractions,
        redirect_map=redirect_map,
        telemetry=discovery_telemetry,
    )


__all__ = ["discover_concepts", "DiscoveryResult"]
