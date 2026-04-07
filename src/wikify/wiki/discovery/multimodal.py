"""Multimodal extraction-unit construction.

Discovery must be able to interrogate non-text evidence — figures, tables,
slide images, and rendered page regions — alongside text chunks. These
helpers turn parsed media records into typed ``ExtractionUnit`` objects so
DAG nodes can route them to image- or table-aware models.
"""

from __future__ import annotations

from collections.abc import Iterable

from wikify.wiki.discovery.contracts import ExtractionUnit, ModalityKind, UnitKind


def figure_units(document_id: str, figures: Iterable[dict]) -> list[ExtractionUnit]:
    """Build figure units from records like ``{id, caption?, image_path?}``."""

    out: list[ExtractionUnit] = []
    for f in figures:
        out.append(
            ExtractionUnit(
                unit_id=f"{document_id}:figure:{f['id']}",
                document_id=document_id,
                kind=UnitKind.FIGURE,
                modality=ModalityKind.IMAGE,
                payload={"image_path": f.get("image_path"), "caption": f.get("caption", "")},
                section=f.get("section"),
                metadata={"figure_id": f["id"]},
            )
        )
    return out


def table_units(document_id: str, tables: Iterable[dict]) -> list[ExtractionUnit]:
    out: list[ExtractionUnit] = []
    for t in tables:
        out.append(
            ExtractionUnit(
                unit_id=f"{document_id}:table:{t['id']}",
                document_id=document_id,
                kind=UnitKind.TABLE,
                modality=ModalityKind.TABLE,
                payload={"rows": t.get("rows", []), "caption": t.get("caption", "")},
                section=t.get("section"),
                metadata={"table_id": t["id"]},
            )
        )
    return out


def page_image_units(document_id: str, page_images: Iterable[dict]) -> list[ExtractionUnit]:
    out: list[ExtractionUnit] = []
    for p in page_images:
        out.append(
            ExtractionUnit(
                unit_id=f"{document_id}:page_image:{p['page']}",
                document_id=document_id,
                kind=UnitKind.PAGE_IMAGE,
                modality=ModalityKind.IMAGE,
                payload={"image_path": p["image_path"], "page": p["page"]},
            )
        )
    return out
