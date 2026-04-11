"""Helpers for constructing ``ExtractionUnit`` collections from documents.

Document-type aware: each builder takes a parsed document representation and
emits the units a strategy can plan over. The builders here are framework
neutral and accept plain dictionaries so they remain testable without the
full ingest stack.
"""

from __future__ import annotations

from collections.abc import Iterable

from wikify.wiki.discovery.contracts import (
    DocumentProfile,
    ExtractionUnit,
    ModalityKind,
    UnitKind,
)


def chunk_units(
    document_id: str,
    chunks: Iterable[dict],
) -> list[ExtractionUnit]:
    """Build text chunk units from chunk dicts ``{id, text, section?}``."""

    out: list[ExtractionUnit] = []
    for c in chunks:
        out.append(
            ExtractionUnit(
                unit_id=f"{document_id}:chunk:{c['id']}",
                document_id=document_id,
                kind=UnitKind.CHUNK,
                modality=ModalityKind.TEXT,
                payload=c.get("text", ""),
                section=c.get("section"),
                metadata={k: v for k, v in c.items() if k not in {"id", "text", "section"}},
            )
        )
    return out


def synopsis_unit(document_id: str, synopsis: str) -> ExtractionUnit:
    return ExtractionUnit(
        unit_id=f"{document_id}:synopsis",
        document_id=document_id,
        kind=UnitKind.SYNOPSIS,
        modality=ModalityKind.TEXT,
        payload=synopsis,
    )


def slide_units(document_id: str, slides: Iterable[dict]) -> list[ExtractionUnit]:
    out: list[ExtractionUnit] = []
    for s in slides:
        out.append(
            ExtractionUnit(
                unit_id=f"{document_id}:slide:{s['index']}",
                document_id=document_id,
                kind=UnitKind.SLIDE,
                modality=ModalityKind.IMAGE if s.get("image_path") else ModalityKind.TEXT,
                payload=s.get("image_path") or s.get("text", ""),
                metadata={"slide_index": s["index"]},
            )
        )
    return out


def plan_units_for_profile(
    profile: DocumentProfile,
    *,
    chunks: Iterable[dict] | None = None,
    figures: Iterable[dict] | None = None,
    tables: Iterable[dict] | None = None,
    slides: Iterable[dict] | None = None,
    synopsis: str | None = None,
) -> list[ExtractionUnit]:
    """Document-type aware unit planner.

    Returns the natural set of extraction units for ``profile.document_type``.
    Callers may post-filter for budgets/strategy.
    """

    from wikify.wiki.discovery.multimodal import figure_units, table_units

    units: list[ExtractionUnit] = []
    if synopsis:
        units.append(synopsis_unit(profile.document_id, synopsis))

    dtype = profile.document_type
    if dtype in {"publication", "markdown", "html_note", "mixed", "unknown"}:
        if chunks:
            units.extend(chunk_units(profile.document_id, chunks))
    if dtype == "slide_deck" and slides:
        units.extend(slide_units(profile.document_id, slides))
    if figures:
        units.extend(figure_units(profile.document_id, figures))
    if tables:
        units.extend(table_units(profile.document_id, tables))
    return units
