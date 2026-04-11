"""Concept merge / dedup / staging-commit operations.

Owns the canonical merge logic for new concept records, the redirect
map that downstream evidence persistence applies, and the ChromaDB
staging collection used for crash-safe extraction commits.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlmodel import select

from wikify.core.store.db import get_session
from wikify.core.store.embeddings import _store
from wikify.core.store.models import ConceptRecord
from wikify.wiki.builder import slugify

logger = logging.getLogger(__name__)

_STAGING_COLLECTION = "concept_staging"


def apply_redirect_map(
    rich_extractions: dict[str, list[dict[str, Any]]],
    redirect_map: dict[str, str],
) -> None:
    """Patch concept references in a rich extraction payload to canonical slugs.

    After ``merge_concept_records`` reassigns input slugs to existing
    canonical ids, downstream evidence/parameter/relation persistence
    must use the canonical ids to avoid orphaned rows. This function
    annotates each concept/parameter/relation with ``_canonical_id``
    in place.
    """

    for _paper_id, chunk_results in rich_extractions.items():
        for chunk_result in chunk_results:
            for concept in chunk_result.get("concepts", []):
                if not isinstance(concept, dict):
                    continue
                name = (concept.get("name") or "").strip()
                if name:
                    slug = slugify(name)
                    if slug in redirect_map:
                        concept["_canonical_id"] = redirect_map[slug]

            for param in chunk_result.get("parameters", []):
                if not isinstance(param, dict):
                    continue
                cname = (param.get("concept_name") or "").strip()
                if cname:
                    slug = slugify(cname)
                    if slug in redirect_map:
                        param["_canonical_id"] = redirect_map[slug]

            for relation in chunk_result.get("relationships", []):
                if not isinstance(relation, dict):
                    continue
                source_name = (relation.get("source_concept") or "").strip()
                target_name = (relation.get("target_concept") or "").strip()
                if source_name:
                    source_slug = slugify(source_name)
                    if source_slug in redirect_map:
                        relation["_source_canonical_id"] = redirect_map[source_slug]
                if target_name:
                    target_slug = slugify(target_name)
                    if target_slug in redirect_map:
                        relation["_target_canonical_id"] = redirect_map[target_slug]


def merge_concept_records(
    new_records: list[ConceptRecord], epoch: int
) -> tuple[int, dict[str, str]]:
    """Merge a batch of newly extracted ``ConceptRecord`` rows into the database.

    Dedup logic:
    - slug match -> update epoch / extend aliases / backfill empty fields
    - alias overlap -> treat as same concept and emit a redirect
    - no match -> insert as new concept

    Returns ``(new_count, redirect_map)``. ``redirect_map`` maps every
    input slug to its canonical slug (identity for new concepts).
    """

    redirect_map: dict[str, str] = {}
    if not new_records:
        return 0, redirect_map

    new_count = 0

    with get_session() as session:
        existing_all: list[ConceptRecord] = list(session.exec(select(ConceptRecord)).all())
        by_slug: dict[str, ConceptRecord] = {r.id: r for r in existing_all}

        alias_index: dict[str, ConceptRecord] = {}
        for rec in existing_all:
            for alias in rec.parsed_aliases:
                alias_index[alias.lower()] = rec
            alias_index[rec.name.lower()] = rec

        for new_rec in new_records:
            if not new_rec.id:
                continue

            existing: ConceptRecord | None = by_slug.get(new_rec.id)
            if existing is None:
                for alias in new_rec.parsed_aliases:
                    existing = alias_index.get(alias.lower())
                    if existing is not None:
                        break
                if existing is None:
                    existing = alias_index.get(new_rec.name.lower())

            if existing is not None:
                if new_rec.id != existing.id:
                    redirect_map[new_rec.id] = existing.id

                existing.epoch_last_updated = epoch
                existing_aliases = set(existing.parsed_aliases)
                new_aliases = set(new_rec.parsed_aliases)
                merged = sorted(existing_aliases | new_aliases)
                existing.aliases = json.dumps(merged)

                if not existing.definition and new_rec.definition:
                    existing.definition = new_rec.definition
                if not existing.concept_type and new_rec.concept_type:
                    existing.concept_type = new_rec.concept_type

                session.add(existing)
                for alias in merged:
                    alias_index[alias.lower()] = existing
            else:
                redirect_map[new_rec.id] = new_rec.id
                new_rec.epoch_discovered = epoch
                new_rec.epoch_last_updated = epoch
                session.add(new_rec)
                new_count += 1
                by_slug[new_rec.id] = new_rec
                alias_index[new_rec.name.lower()] = new_rec
                for alias in new_rec.parsed_aliases:
                    alias_index[alias.lower()] = new_rec

        session.commit()

    logger.info(
        "merge_concept_records: %d input -> %d new, %d redirected (epoch %d)",
        len(new_records),
        new_count,
        sum(1 for k, v in redirect_map.items() if k != v),
        epoch,
    )
    return new_count, redirect_map


# ── Staging (ChromaDB, crash-safe) ───────────────────────────────────────────


def _get_staging_collection() -> Any:
    _ = _store.collection  # ensure client is initialized
    return _store._client.get_or_create_collection(_STAGING_COLLECTION)


def _build_staging_id(epoch: int, concept_id: str, source_id: str, chunk_index: int) -> str:
    return f"e{epoch}|{source_id[:16]}|{chunk_index}|{concept_id}"


def stage_extractions(
    epoch: int, source_id: str, extractions: list[ConceptRecord]
) -> None:
    """Stage one source's extracted records to the crash-safe collection."""

    if not extractions:
        return

    collection = _get_staging_collection()
    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for i, rec in enumerate(extractions):
        doc_id = _build_staging_id(epoch, rec.id, source_id, i)
        ids.append(doc_id)
        documents.append(rec.definition or rec.name)
        metadatas.append(
            {
                "epoch": epoch,
                "source_id": source_id,
                "concept_id": rec.id,
                "concept_name": rec.name,
                "aliases": rec.aliases,
                "definition": rec.definition,
                "concept_type": rec.concept_type,
                "domain": rec.domain,
            }
        )

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    logger.debug(
        "stage_extractions: staged %d concepts for source %s epoch %d",
        len(extractions),
        source_id[:16],
        epoch,
    )


def commit_staged_extractions(epoch: int) -> tuple[int, dict[str, str]]:
    """Read staged extractions for ``epoch`` and merge into the canonical store."""

    collection = _get_staging_collection()
    try:
        result = collection.get(where={"epoch": epoch}, include=["metadatas"])
    except Exception:
        logger.exception("commit_staged_extractions: failed to query staging collection")
        return 0, {}

    if not result or not result.get("ids"):
        logger.info("commit_staged_extractions: no staged entries for epoch %d", epoch)
        return 0, {}

    metadatas = result.get("metadatas") or []
    new_records: list[ConceptRecord] = []
    for meta in metadatas:
        new_records.append(
            ConceptRecord(
                id=meta.get("concept_id", ""),
                name=meta.get("concept_name", ""),
                aliases=meta.get("aliases", "[]"),
                definition=meta.get("definition", ""),
                concept_type=meta.get("concept_type", ""),
                domain=meta.get("domain", ""),
                epoch_discovered=epoch,
                epoch_last_updated=epoch,
            )
        )

    new_records = [r for r in new_records if r.id]
    count, redirect_map = merge_concept_records(new_records, epoch)
    logger.info(
        "commit_staged_extractions: epoch %d -> %d new concepts committed", epoch, count
    )
    return count, redirect_map


def clear_staged_extractions(epoch: int) -> None:
    """Delete all staging entries for ``epoch``."""

    collection = _get_staging_collection()
    try:
        result = collection.get(where={"epoch": epoch}, include=[])
        ids_to_delete = result.get("ids") or []
        if ids_to_delete:
            collection.delete(ids=ids_to_delete)
            logger.info(
                "clear_staged_extractions: deleted %d entries for epoch %d",
                len(ids_to_delete),
                epoch,
            )
        else:
            logger.debug("clear_staged_extractions: nothing to clear for epoch %d", epoch)
    except Exception:
        logger.exception("clear_staged_extractions: failed to clear epoch %d staging", epoch)


__all__ = [
    "apply_redirect_map",
    "clear_staged_extractions",
    "commit_staged_extractions",
    "merge_concept_records",
    "stage_extractions",
]
