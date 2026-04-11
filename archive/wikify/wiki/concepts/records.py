"""Canonical concept record dataclasses and lookups.

Owns the in-memory shape of one discovery run's outputs (``DiscoveryResult``)
and read-only queries over the canonical ``ConceptRecord`` table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlmodel import select

from wikify.core.store.db import get_session
from wikify.core.store.models import ConceptRecord
from wikify.wiki.builder import slugify


@dataclass(slots=True)
class DiscoveryResult:
    """Explicit output of one discovery run."""

    concepts: list[ConceptRecord] = field(default_factory=list)
    rich_extractions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    redirect_map: dict[str, str] = field(default_factory=dict)
    telemetry: dict[str, Any] = field(default_factory=dict)


def get_concept_by_name(name: str) -> ConceptRecord | None:
    """Look up a ``ConceptRecord`` by display name or known alias."""

    slug = slugify(name)
    name_lower = name.lower()

    with get_session() as session:
        record = session.get(ConceptRecord, slug)
        if record is not None:
            return record

        all_records: list[ConceptRecord] = list(session.exec(select(ConceptRecord)).all())
        for rec in all_records:
            if rec.name.lower() == name_lower:
                return rec
            for alias in rec.parsed_aliases:
                if alias.lower() == name_lower:
                    return rec

    return None


def list_concepts(
    domain: str = "",
    min_importance: float = 0.0,
) -> list[ConceptRecord]:
    """Return ``ConceptRecord`` rows filtered by domain and minimum importance."""

    with get_session() as session:
        stmt = select(ConceptRecord).where(ConceptRecord.importance >= min_importance)
        records: list[ConceptRecord] = list(session.exec(stmt).all())

    if domain:
        domain_lower = domain.lower()
        records = [r for r in records if domain_lower in r.domain.lower()]

    records.sort(key=lambda r: r.importance, reverse=True)
    return records
