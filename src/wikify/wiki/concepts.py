"""Haiku-based concept discovery pipeline for the Wikipedia/epoch model.

Pass 1 of the epoch pipeline: scan corpus chunks with claude-haiku to extract
named concepts (technique, material, phenomenon, method, theory, dataset).

Pipeline:
  discover_concepts()
      -> extract_concepts_from_source()   [per paper, threads context across chunks]
          -> _extract_from_chunk()        [single haiku call, returns ConceptRecord list]
      -> stage_extractions()              [write raw results to ChromaDB staging]
      -> commit_staged_extractions()      [merge staging into SQLite ConceptRecord table]

Staging uses a ChromaDB collection named "concept_staging" so that crash recovery
is possible between the extraction and commit phases. Call clear_staged_extractions()
at the start of each new epoch before running Pass 1.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlmodel import select

from wikify.llm.client import complete_json
from wikify.store.db import get_session
from wikify.store.embeddings import _store
from wikify.store.models import Chunk, ConceptRecord, Paper
from wikify.wiki.builder import slugify

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

HAIKU_MODEL = "claude-haiku-4-5-20251001"
_STAGING_COLLECTION = "concept_staging"
_VALID_CONCEPT_TYPES = frozenset(
    {"technique", "material", "phenomenon", "method", "theory", "dataset"}
)


# ── Internal helpers ───────────────────────────────────────────────────────────


def _get_staging_collection() -> Any:
    """Return (creating if absent) the ChromaDB staging collection."""
    return _store.client.get_or_create_collection(_STAGING_COLLECTION)


def _build_staging_id(epoch: int, concept_id: str, source_id: str, chunk_index: int) -> str:
    """Construct a deterministic ChromaDB document ID for a staged extraction."""
    return f"e{epoch}|{source_id[:16]}|{chunk_index}|{concept_id}"


# ── Core extraction ────────────────────────────────────────────────────────────


def _extract_from_chunk(
    chunk: Chunk,
    prior_context: list[str],
    model: str,
) -> list[ConceptRecord]:
    """Call haiku to extract named concepts from a single chunk.

    Args:
        chunk: The Chunk object whose content will be analysed.
        prior_context: Concept names already seen in earlier chunks of the same
            source.  Passed to the prompt so haiku avoids redundant re-extractions.
        model: litellm model string (normally HAIKU_MODEL).

    Returns:
        List of ConceptRecord objects (not yet persisted to DB).
    """
    prior_str = ", ".join(prior_context) if prior_context else "none"

    user_msg = (
        "Extract named concepts from the following text excerpt.\n\n"
        f"Previously extracted concepts from earlier sections of this source: {prior_str}. "
        "Do not re-extract these unless this section adds new information about them.\n\n"
        "Return a JSON array — and ONLY the JSON array, no prose — where each element has:\n"
        '  "name":       canonical display name (e.g. "Atomic Layer Deposition")\n'
        '  "type":       one of: technique | material | phenomenon | method | theory | dataset\n'
        '  "aliases":    list of abbreviations / alternate names (may be empty list)\n'
        '  "definition": one-sentence definition (max 25 words)\n\n'
        "Include only concepts that are clearly named and domain-specific. "
        "Skip generic terms like 'experiment', 'data', 'result'.\n\n"
        "--- TEXT ---\n"
        f"{chunk.content}\n"
        "--- END TEXT ---"
    )

    try:
        raw = complete_json(
            messages=[{"role": "user", "content": user_msg}],
            model=model,
            temperature=0.1,
            max_tokens=1024,
        )
    except Exception:
        logger.exception("_extract_from_chunk: LLM call failed for chunk %s", chunk.id)
        return []

    if not isinstance(raw, list):
        logger.warning(
            "_extract_from_chunk: expected list, got %s for chunk %s",
            type(raw).__name__,
            chunk.id,
        )
        return []

    records: list[ConceptRecord] = []
    for item in raw:
        if not isinstance(item, dict):
            continue

        name = (item.get("name") or "").strip()
        if not name:
            continue

        concept_type = (item.get("type") or "").strip().lower()
        if concept_type not in _VALID_CONCEPT_TYPES:
            concept_type = ""

        aliases_raw = item.get("aliases") or []
        if not isinstance(aliases_raw, list):
            aliases_raw = []
        aliases = [str(a).strip() for a in aliases_raw if str(a).strip()]

        definition = (item.get("definition") or "").strip()

        records.append(
            ConceptRecord(
                id=slugify(name),
                name=name,
                aliases=json.dumps(aliases),
                definition=definition,
                concept_type=concept_type,
            )
        )

    return records


def extract_concepts_from_source(
    source_id: str,
    chunks: list[Chunk],
    epoch: int,
    model: str = HAIKU_MODEL,
) -> list[ConceptRecord]:
    """Extract concepts from all chunks of a single corpus source.

    Threads concept names forward across chunks so haiku can avoid repeating
    already-seen concepts while still updating them when new information appears.

    Args:
        source_id: Paper.id for the source being processed.
        chunks:    Ordered list of Chunk objects belonging to the source.
        epoch:     Current epoch number (stored on each returned record).
        model:     litellm model string.

    Returns:
        All ConceptRecord objects extracted across every chunk.
    """
    prior_concepts: list[str] = []
    all_records: list[ConceptRecord] = []

    for chunk in chunks:
        chunk_records = _extract_from_chunk(chunk, prior_context=prior_concepts, model=model)
        all_records.extend(chunk_records)
        # Thread names forward so the next chunk prompt knows what was already seen
        prior_concepts = [r.name for r in chunk_records]

    # Stamp epoch fields (discovery will be refined at merge time)
    for record in all_records:
        record.epoch_discovered = epoch
        record.epoch_last_updated = epoch

    logger.info(
        "extract_concepts_from_source(%s): %d chunks -> %d concepts",
        source_id[:16],
        len(chunks),
        len(all_records),
    )
    return all_records


# ── Staging (ChromaDB) ─────────────────────────────────────────────────────────


def stage_extractions(epoch: int, source_id: str, extractions: list[ConceptRecord]) -> None:
    """Write raw concept extractions for one source to the ChromaDB staging collection.

    Each concept is stored as a document whose content is its definition (or name
    as fallback).  Epoch, source_id, and concept metadata are stored in the
    ChromaDB document metadata so commit_staged_extractions() can reconstruct
    ConceptRecord objects without re-querying the LLM.

    Args:
        epoch:       Current epoch number.
        source_id:   Paper.id whose extractions are being staged.
        extractions: ConceptRecord list from extract_concepts_from_source().
    """
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


def commit_staged_extractions(epoch: int) -> int:
    """Read all staged extractions for the epoch and merge into ConceptRecord table.

    Args:
        epoch: Epoch whose staging data should be committed.

    Returns:
        Number of new ConceptRecord rows inserted.
    """
    collection = _get_staging_collection()

    # Retrieve all staged documents for this epoch
    try:
        result = collection.get(where={"epoch": epoch}, include=["metadatas"])
    except Exception:
        logger.exception("commit_staged_extractions: failed to query staging collection")
        return 0

    if not result or not result.get("ids"):
        logger.info("commit_staged_extractions: no staged entries for epoch %d", epoch)
        return 0

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
    count = merge_concept_records(new_records, epoch)

    logger.info(
        "commit_staged_extractions: epoch %d -> %d new concepts committed",
        epoch,
        count,
    )
    return count


def clear_staged_extractions(epoch: int) -> None:
    """Delete all staging entries for the given epoch.

    Call this at the start of each new epoch before running Pass 1 so stale
    data from a previous (possibly crashed) run does not pollute the new epoch.

    Args:
        epoch: Epoch whose staging data should be cleared.
    """
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


# ── DB merge ──────────────────────────────────────────────────────────────────


def merge_concept_records(new_records: list[ConceptRecord], epoch: int) -> int:
    """Merge a batch of newly extracted ConceptRecords into the database.

    Deduplication logic:
    - If a record with the same slug (id) already exists, update epoch_last_updated
      and extend aliases with any new aliases found.
    - If no slug match, check whether any alias in the new record overlaps with
      aliases stored for any existing record.  If so, treat as the same concept.
    - If no match at all, insert as a new concept with epoch_discovered = epoch.

    Args:
        new_records: ConceptRecord objects to merge (not yet in DB).
        epoch:       Current epoch number.

    Returns:
        Number of truly new concepts inserted.
    """
    if not new_records:
        return 0

    new_count = 0

    with get_session() as session:
        # Load all existing records once for efficient lookup
        existing_all: list[ConceptRecord] = list(session.exec(select(ConceptRecord)).all())

        # Build lookup indices
        by_slug: dict[str, ConceptRecord] = {r.id: r for r in existing_all}

        # alias -> existing record (many-to-one)
        alias_index: dict[str, ConceptRecord] = {}
        for rec in existing_all:
            for alias in rec.parsed_aliases:
                alias_index[alias.lower()] = rec
            alias_index[rec.name.lower()] = rec

        for new_rec in new_records:
            if not new_rec.id:
                continue

            existing: ConceptRecord | None = by_slug.get(new_rec.id)

            # Fall back to alias overlap if no slug match
            if existing is None:
                for alias in new_rec.parsed_aliases:
                    existing = alias_index.get(alias.lower())
                    if existing is not None:
                        break
                if existing is None:
                    existing = alias_index.get(new_rec.name.lower())

            if existing is not None:
                # Update last-seen epoch and merge any new aliases
                existing.epoch_last_updated = epoch

                existing_aliases = set(existing.parsed_aliases)
                new_aliases = set(new_rec.parsed_aliases)
                merged = sorted(existing_aliases | new_aliases)
                existing.aliases = json.dumps(merged)

                # Backfill definition if the existing one is empty
                if not existing.definition and new_rec.definition:
                    existing.definition = new_rec.definition

                # Backfill concept_type if missing
                if not existing.concept_type and new_rec.concept_type:
                    existing.concept_type = new_rec.concept_type

                session.add(existing)

                # Keep alias_index consistent so later records in the same batch
                # can find this record via its new aliases
                for alias in merged:
                    alias_index[alias.lower()] = existing
            else:
                # Truly new concept
                new_rec.epoch_discovered = epoch
                new_rec.epoch_last_updated = epoch
                session.add(new_rec)
                new_count += 1

                # Update in-memory indices so later records in the batch deduplicate
                # against this newly inserted record
                by_slug[new_rec.id] = new_rec
                alias_index[new_rec.name.lower()] = new_rec
                for alias in new_rec.parsed_aliases:
                    alias_index[alias.lower()] = new_rec

        session.commit()

    logger.info(
        "merge_concept_records: %d input records -> %d new concepts (epoch %d)",
        len(new_records),
        new_count,
        epoch,
    )
    return new_count


# ── Orchestration ─────────────────────────────────────────────────────────────


def discover_concepts(
    paper_ids: list[str],
    epoch: int,
    model: str | None = None,
) -> list[ConceptRecord]:
    """Pass 1 orchestrator: run concept discovery across a set of corpus papers.

    For each paper:
    1. Load Paper + Chunks from SQLite.
    2. Call extract_concepts_from_source() (cross-chunk context threading).
    3. Call stage_extractions() to write results to ChromaDB staging.

    After all papers are processed:
    4. Call commit_staged_extractions() to merge into ConceptRecord table.
    5. Return the final list of committed ConceptRecords for the epoch.

    Args:
        paper_ids: List of Paper.id values to process.
        epoch:     Current epoch number.
        model:     litellm model string.  Defaults to HAIKU_MODEL.

    Returns:
        All ConceptRecord rows whose epoch_last_updated == epoch.
    """
    resolved_model = model or HAIKU_MODEL

    for paper_id in paper_ids:
        with get_session() as session:
            paper = session.get(Paper, paper_id)
            if paper is None:
                logger.warning("discover_concepts: paper %s not found, skipping", paper_id)
                continue

            chunks: list[Chunk] = list(
                session.exec(
                    select(Chunk).where(Chunk.paper_id == paper_id).order_by(Chunk.chunk_index)  # type: ignore[arg-type]
                ).all()
            )

        if not chunks:
            logger.debug("discover_concepts: paper %s has no chunks, skipping", paper_id[:16])
            continue

        logger.info(
            "discover_concepts: processing paper %s (%d chunks)",
            paper_id[:16],
            len(chunks),
        )

        extractions = extract_concepts_from_source(
            source_id=paper_id,
            chunks=chunks,
            epoch=epoch,
            model=resolved_model,
        )

        stage_extractions(epoch=epoch, source_id=paper_id, extractions=extractions)

    commit_staged_extractions(epoch)

    # Return all records touched in this epoch
    with get_session() as session:
        results: list[ConceptRecord] = list(
            session.exec(
                select(ConceptRecord).where(ConceptRecord.epoch_last_updated == epoch)
            ).all()
        )

    logger.info(
        "discover_concepts: epoch %d complete, %d concepts in DB with epoch_last_updated=%d",
        epoch,
        len(results),
        epoch,
    )
    return results


# ── Query helpers ─────────────────────────────────────────────────────────────


def get_concept_by_name(name: str) -> ConceptRecord | None:
    """Look up a ConceptRecord by display name or alias.

    Checks the slug (slugified name) first, then scans all alias lists.

    Args:
        name: Canonical display name or known alias to search for.

    Returns:
        The matching ConceptRecord, or None if not found.
    """
    slug = slugify(name)
    name_lower = name.lower()

    with get_session() as session:
        # Fast path: slug match
        record = session.get(ConceptRecord, slug)
        if record is not None:
            return record

        # Slow path: check all alias lists
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
    """Return ConceptRecords filtered by domain and minimum importance score.

    Args:
        domain:         If non-empty, only return records whose domain matches
                        (case-insensitive substring match).
        min_importance: Include only records with importance >= this threshold.

    Returns:
        Matching ConceptRecord rows, ordered by importance descending.
    """
    with get_session() as session:
        stmt = select(ConceptRecord).where(ConceptRecord.importance >= min_importance)
        records: list[ConceptRecord] = list(session.exec(stmt).all())

    if domain:
        domain_lower = domain.lower()
        records = [r for r in records if domain_lower in r.domain.lower()]

    records.sort(key=lambda r: r.importance, reverse=True)
    return records
