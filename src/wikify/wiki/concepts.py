"""Haiku-based concept discovery pipeline for the Wikipedia/epoch model.

Pass 1 of the epoch pipeline: scan corpus chunks with claude-haiku to extract
named concepts (technique, material, phenomenon, method, theory, dataset).

Pipeline:
  discover_concepts()
      -> get_mining_frontier()              [select chunks for this epoch]
      -> concept_aware_prefilter()          [skip chunks far from known concepts]
      -> extract_concepts_from_source()     [per paper, threads context across chunks]
          -> _extract_from_chunk()          [single haiku call, returns ConceptRecord list]
      -> stage_extractions()               [write raw results to ChromaDB staging]
      -> commit_staged_extractions()        [merge staging into SQLite ConceptRecord table]
      -> record_mining()                    [write ChunkMiningLog rows]

Staging uses a ChromaDB collection named "concept_staging" so that crash recovery
is possible between the extraction and commit phases. Call clear_staged_extractions()
at the start of each new epoch before running Pass 1.
"""

from __future__ import annotations

import json
import logging
import os
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from sqlmodel import select

from wikify.llm.client import complete_json
from wikify.store.db import get_session
from wikify.store.embeddings import _store
from wikify.store.models import Chunk, ChunkMiningLog, ConceptRecord
from wikify.wiki.builder import slugify
from wikify.wiki.template import build_extraction_prompt, load_template

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_WIKI_DIR = Path("data/wiki")
HAIKU_MODEL = "claude-haiku-4-5-20251001"
_STAGING_COLLECTION = "concept_staging"
_VALID_CONCEPT_TYPES = frozenset(
    {"technique", "material", "phenomenon", "method", "theory", "dataset"}
)

# Sections that rarely contain extractable domain concepts — skip to save LLM calls
_SKIP_SECTIONS = frozenset({"references", "acknowledgments", "appendix"})

# Section type to mining tier (0=highest priority, 2=lowest)
_SECTION_TIERS: dict[str, int] = {
    "abstract": 0,
    "introduction": 0,
    "conclusion": 0,
    "methods": 1,
    "results": 1,
    "body": 2,
    "discussion": 2,
}
_DEFAULT_TIER = 2  # anything not in the map

# 5% of unmined lower-tier chunks explored randomly each epoch
_EXPLORATION_RATE = 0.05

# Module-level storage for rich extraction results from the last extraction run.
# Maps paper_id -> list of per-chunk rich dicts. Populated by
# extract_concepts_from_source(), consumed by evidence/gap/parameter processors.
_last_rich_extractions: dict[str, list[dict[str, Any]]] = {}

# Minimum cosine similarity to any known concept definition to keep a chunk
_CONCEPT_SIM_THRESHOLD = 0.15


def get_rich_extractions() -> dict[str, list[dict[str, Any]]]:
    """Return the rich extraction results from the last extraction run.

    Returns:
        Dict mapping paper_id -> list of per-chunk rich extraction dicts.
        Each dict has keys: concepts, parameters, mechanisms, relationships,
        gaps, _chunk_id, _paper_id, _chunk_content.
    """
    return _last_rich_extractions


def clear_rich_extractions() -> None:
    """Clear the stored rich extraction results."""
    _last_rich_extractions.clear()


# ── Internal helpers ───────────────────────────────────────────────────────────


def _get_staging_collection() -> Any:
    """Return (creating if absent) the ChromaDB staging collection."""
    # Access the underlying chromadb client via the collection property
    # which triggers lazy init of _client
    _ = _store.collection  # ensure client is initialized
    return _store._client.get_or_create_collection(_STAGING_COLLECTION)


def _build_staging_id(epoch: int, concept_id: str, source_id: str, chunk_index: int) -> str:
    """Construct a deterministic ChromaDB document ID for a staged extraction."""
    return f"e{epoch}|{source_id[:16]}|{chunk_index}|{concept_id}"


def _chunk_tier(chunk: Chunk) -> int:
    """Return the mining tier (0/1/2) for a chunk based on its section_type."""
    if chunk.section_type in _SKIP_SECTIONS:
        # Skipped sections are assigned tier 99 so they never get picked
        return 99
    return _SECTION_TIERS.get(chunk.section_type, _DEFAULT_TIER)


# ── Core extraction ────────────────────────────────────────────────────────────


def _extract_rich_from_chunk(
    chunk: Chunk,
    prior_context: list[str],
    model: str,
    template: str | None = None,
) -> dict[str, Any]:
    """Call haiku to extract structured knowledge from a single chunk.

    Uses the extraction template to produce a rich result containing concepts,
    parameters, mechanisms, relationships, and gaps.

    Args:
        chunk: The Chunk object whose content will be analysed.
        prior_context: Concept names already seen in earlier chunks of the same
            source.  Passed to the prompt so haiku avoids redundant re-extractions.
        model: litellm model string (normally HAIKU_MODEL).
        template: Extraction template content. If None, loads from disk.

    Returns:
        Dict with keys: concepts, parameters, mechanisms, relationships, gaps.
        Each value is a list of dicts. Returns empty lists on failure.
    """
    empty_result: dict[str, Any] = {
        "concepts": [],
        "parameters": [],
        "mechanisms": [],
        "relationships": [],
        "gaps": [],
    }

    if template is None:
        template = load_template(_WIKI_DIR)

    messages = build_extraction_prompt(template, chunk.content, prior_context)

    try:
        raw = complete_json(
            messages=messages,
            model=model,
            temperature=0.1,
            max_tokens=2048,
        )
    except Exception:
        logger.exception("_extract_rich_from_chunk: LLM call failed for chunk %s", chunk.id)
        return empty_result

    # Handle both dict and list responses
    if isinstance(raw, list):
        # Legacy format: list of concept dicts
        return {**empty_result, "concepts": raw}

    if not isinstance(raw, dict):
        logger.warning(
            "_extract_rich_from_chunk: expected dict, got %s for chunk %s",
            type(raw).__name__,
            chunk.id,
        )
        return empty_result

    # Normalize: ensure all expected keys exist as lists
    for key in empty_result:
        val = raw.get(key, [])
        if not isinstance(val, list):
            val = []
        empty_result[key] = val

    return empty_result


def _parse_concepts_from_rich(rich_result: dict[str, Any]) -> list[ConceptRecord]:
    """Extract ConceptRecord objects from a rich extraction result.

    Provides backward compatibility: the rest of the pipeline expects
    a list of ConceptRecord objects from extraction.

    Args:
        rich_result: Dict from _extract_rich_from_chunk().

    Returns:
        List of ConceptRecord objects (not yet persisted to DB).
    """
    records: list[ConceptRecord] = []
    for item in rich_result.get("concepts", []):
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


def _extract_from_chunk(
    chunk: Chunk,
    prior_context: list[str],
    model: str,
    template: str | None = None,
) -> list[ConceptRecord]:
    """Call haiku to extract named concepts from a single chunk.

    Backward-compatible wrapper around _extract_rich_from_chunk().

    Args:
        chunk: The Chunk object whose content will be analysed.
        prior_context: Concept names already seen in earlier chunks of the same
            source.  Passed to the prompt so haiku avoids redundant re-extractions.
        model: litellm model string (normally HAIKU_MODEL).
        template: Extraction template content. If None, loads from disk.

    Returns:
        List of ConceptRecord objects (not yet persisted to DB).
    """
    rich = _extract_rich_from_chunk(chunk, prior_context, model, template=template)
    return _parse_concepts_from_rich(rich)


def extract_concepts_from_source(
    source_id: str,
    chunks: list[Chunk],
    epoch: int,
    model: str = HAIKU_MODEL,
    template: str | None = None,
) -> list[ConceptRecord]:
    """Extract concepts from a (possibly pre-filtered) list of chunks for one source.

    Threads concept names forward across chunks so haiku can avoid repeating
    already-seen concepts while still updating them when new information appears.

    The caller is responsible for pre-filtering chunks via get_mining_frontier()
    and concept_aware_prefilter().  When called without pre-filtering (e.g. from
    the profiler or tests), this function applies only the baseline _SKIP_SECTIONS
    and minimum-length guards.

    Also stores rich extraction results (parameters, mechanisms, relationships,
    gaps) in the module-level _last_rich_extractions dict for downstream
    consumers (evidence linkage, gap reporting, parameter extraction).

    Args:
        source_id: Paper.id for the source being processed.
        chunks:    Ordered list of Chunk objects to process (may be a subset of
                   all chunks for the paper when called via the epoch pipeline).
        epoch:     Current epoch number (stored on each returned record).
        model:     litellm model string.
        template:  Extraction template content. If None, loads from disk.

    Returns:
        All ConceptRecord objects extracted across every supplied chunk.
    """
    # Baseline pre-filter: skip sections that never yield extractable concepts
    relevant_chunks = [c for c in chunks if c.section_type not in _SKIP_SECTIONS]
    # Also skip very short chunks (usually metadata/headers, not substantive content)
    relevant_chunks = [c for c in relevant_chunks if len(c.content) > 50]

    if not relevant_chunks:
        logger.debug("extract_concepts_from_source(%s): all chunks filtered out", source_id[:16])
        return []

    logger.info(
        "extract_concepts_from_source(%s): %d/%d chunks after pre-filter",
        source_id[:16],
        len(relevant_chunks),
        len(chunks),
    )

    # Load template once for all chunks in this source
    if template is None:
        template = load_template(_WIKI_DIR)

    prior_concepts: list[str] = []
    all_records: list[ConceptRecord] = []
    all_rich: list[dict[str, Any]] = []

    for chunk in relevant_chunks:
        rich = _extract_rich_from_chunk(
            chunk, prior_context=prior_concepts, model=model, template=template
        )
        # Tag each rich result with chunk metadata for downstream consumers
        rich["_chunk_id"] = chunk.id
        rich["_paper_id"] = chunk.paper_id
        rich["_chunk_content"] = chunk.content
        all_rich.append(rich)

        chunk_records = _parse_concepts_from_rich(rich)
        all_records.extend(chunk_records)
        # Thread names forward so the next chunk prompt knows what was already seen
        prior_concepts = [r.name for r in chunk_records]

    # Store rich results for downstream consumers
    _last_rich_extractions[source_id] = all_rich

    # Stamp epoch fields (discovery will be refined at merge time)
    for record in all_records:
        record.epoch_discovered = epoch
        record.epoch_last_updated = epoch

    logger.info(
        "extract_concepts_from_source(%s): %d chunks -> %d concepts",
        source_id[:16],
        len(relevant_chunks),
        len(all_records),
    )
    return all_records


# ── Progressive mining frontier ────────────────────────────────────────────────


def get_mining_frontier(
    paper_ids: list[str],
    epoch: int,
) -> list[tuple[Chunk, str]]:
    """Select chunks to mine this epoch using progressive frontier + exploration.

    Algorithm:
    1. Load all chunks for the given papers from SQLite.
    2. Query ChunkMiningLog to identify already-mined chunk IDs.
    3. Filter out _SKIP_SECTIONS chunks (they are never mined).
    4. Group unmined chunks by tier (0, 1, 2).
    5. Current frontier = lowest tier that still has unmined chunks.
    6. Schedule ALL unmined chunks in the frontier tier.
    7. Add an exploration sample: 5% of unmined chunks from LOWER tiers
       (i.e. tiers > frontier_tier), randomly selected.
    8. After all tiers are exhausted, cycle back but only process chunks from
       papers whose ingest timestamp is newer than the last full-scan epoch —
       behaving as if those papers are newly added (their chunks are "unmined").

    Args:
        paper_ids: Paper IDs to consider.
        epoch:     Current epoch number (used only for logging context).

    Returns:
        List of (chunk, source_reason) tuples where source_reason is one of
        "tier_0", "tier_1", "tier_2", or "exploration".
    """
    with get_session() as session:
        # Load all chunks for the requested papers
        all_chunks: list[Chunk] = []
        for pid in paper_ids:
            paper_chunks = list(
                session.exec(
                    select(Chunk).where(Chunk.paper_id == pid).order_by(Chunk.chunk_index)  # type: ignore[arg-type]
                ).all()
            )
            all_chunks.extend(paper_chunks)

        # Load all mined chunk IDs from the log
        mined_ids: set[str] = set(session.exec(select(ChunkMiningLog.chunk_id)).all())

    logger.info(
        "get_mining_frontier: epoch %d — %d total chunks, %d already mined",
        epoch,
        len(all_chunks),
        len(mined_ids),
    )

    # Separate mineable chunks from skipped ones
    mineable = [
        c for c in all_chunks if c.section_type not in _SKIP_SECTIONS and len(c.content) > 50
    ]
    skipped_count = len(all_chunks) - len(mineable)
    if skipped_count:
        logger.debug(
            "get_mining_frontier: %d chunks skipped (skip-sections or too short)", skipped_count
        )

    # Split into mined / unmined
    unmined = [c for c in mineable if c.id not in mined_ids]
    logger.info(
        "get_mining_frontier: %d mineable chunks, %d unmined",
        len(mineable),
        len(unmined),
    )

    if not unmined:
        logger.info(
            "get_mining_frontier: all chunks mined — nothing to schedule for epoch %d", epoch
        )
        return []

    # Group unmined by tier
    by_tier: dict[int, list[Chunk]] = defaultdict(list)
    for chunk in unmined:
        tier = _chunk_tier(chunk)
        if tier <= 2:  # only tiers 0-2 are valid mining tiers
            by_tier[tier].append(chunk)

    # Current frontier = lowest tier with unmined chunks
    available_tiers = sorted(by_tier.keys())
    if not available_tiers:
        logger.info("get_mining_frontier: no unmined chunks in tiers 0-2 for epoch %d", epoch)
        return []

    frontier_tier = available_tiers[0]
    scheduled = by_tier[frontier_tier]

    logger.info(
        "get_mining_frontier: frontier=tier_%d, scheduling %d chunks",
        frontier_tier,
        len(scheduled),
    )

    result: list[tuple[Chunk, str]] = [(c, f"tier_{frontier_tier}") for c in scheduled]

    # Exploration: 5% random sample from chunks in lower-priority (higher-numbered) tiers
    lower_tier_chunks: list[Chunk] = []
    for tier in available_tiers:
        if tier > frontier_tier:
            lower_tier_chunks.extend(by_tier[tier])

    if lower_tier_chunks:
        exploration_n = max(1, int(len(lower_tier_chunks) * _EXPLORATION_RATE))
        exploration_sample = random.sample(
            lower_tier_chunks, min(exploration_n, len(lower_tier_chunks))
        )
        logger.info(
            "get_mining_frontier: exploration sample %d/%d lower-tier chunks",
            len(exploration_sample),
            len(lower_tier_chunks),
        )
        # Avoid duplicates (a chunk already scheduled cannot also be in exploration)
        scheduled_ids = {c.id for c in scheduled}
        for chunk in exploration_sample:
            if chunk.id not in scheduled_ids:
                result.append((chunk, "exploration"))

    logger.info(
        "get_mining_frontier: epoch %d total chunks to process: %d (%d scheduled + %d exploration)",
        epoch,
        len(result),
        len(scheduled),
        len(result) - len(scheduled),
    )
    return result


def record_mining(
    chunk_id: str,
    paper_id: str,
    epoch: int,
    tier: int,
    source: str,
) -> None:
    """Write a ChunkMiningLog row after successfully mining a chunk.

    Args:
        chunk_id: Chunk.id that was mined.
        paper_id: Paper.id that owns the chunk.
        epoch:    Epoch in which the chunk was mined.
        tier:     Mining tier (0, 1, or 2).
        source:   "scheduled" | "exploration" | "deepening".
    """
    with get_session() as session:
        log_entry = ChunkMiningLog(
            chunk_id=chunk_id,
            paper_id=paper_id,
            epoch_mined=epoch,
            tier=tier,
            source=source,
        )
        session.add(log_entry)
        session.commit()

    logger.debug(
        "record_mining: chunk %s (paper %s) mined at epoch %d tier %d source=%s",
        chunk_id[:16],
        paper_id[:16],
        epoch,
        tier,
        source,
    )


def concept_aware_prefilter(
    chunks_with_reason: list[tuple[Chunk, str]],
    epoch: int,
) -> list[tuple[Chunk, str]]:
    """Filter chunks by embedding similarity to known concept definitions.

    On epoch 1 (or when no concept definitions exist), all chunks pass through
    unfiltered.  From epoch 2 onward, chunks whose maximum cosine similarity to
    any known concept definition falls below _CONCEPT_SIM_THRESHOLD are dropped —
    unless they are in the exploration budget (source_reason == "exploration"),
    which always bypass the filter.

    Args:
        chunks_with_reason: List of (Chunk, source_reason) from get_mining_frontier().
        epoch:              Current epoch number.

    Returns:
        Filtered list of (Chunk, source_reason) tuples.
    """
    if epoch <= 1:
        logger.debug("concept_aware_prefilter: epoch %d — skipping filter (first epoch)", epoch)
        return chunks_with_reason

    if not chunks_with_reason:
        return []

    # Load all concept definitions
    with get_session() as session:
        all_concepts: list[ConceptRecord] = list(session.exec(select(ConceptRecord)).all())

    definitions = [c.definition or c.name for c in all_concepts if (c.definition or c.name)]

    if not definitions:
        logger.info(
            "concept_aware_prefilter: epoch %d — no concept definitions, passing all %d chunks",
            epoch,
            len(chunks_with_reason),
        )
        return chunks_with_reason

    # Encode all concept definitions
    concept_embeddings = np.array(_store.model.encode(definitions))  # shape (n_concepts, dim)

    # Separate exploration chunks (always kept) from candidates that need filtering
    exploration: list[tuple[Chunk, str]] = [
        (c, r) for c, r in chunks_with_reason if r == "exploration"
    ]
    candidates: list[tuple[Chunk, str]] = [
        (c, r) for c, r in chunks_with_reason if r != "exploration"
    ]

    if not candidates:
        logger.debug(
            "concept_aware_prefilter: all %d chunks are exploration — no filtering needed",
            len(exploration),
        )
        return chunks_with_reason

    # Encode candidate chunks
    candidate_texts = [c.content for c, _ in candidates]
    chunk_embeddings = np.array(_store.model.encode(candidate_texts))  # shape (n_chunks, dim)

    # Cosine similarity: normalise then dot-product
    concept_norms = np.linalg.norm(concept_embeddings, axis=1, keepdims=True)
    concept_norms = np.where(concept_norms == 0, 1.0, concept_norms)
    concept_unit = concept_embeddings / concept_norms

    chunk_norms = np.linalg.norm(chunk_embeddings, axis=1, keepdims=True)
    chunk_norms = np.where(chunk_norms == 0, 1.0, chunk_norms)
    chunk_unit = chunk_embeddings / chunk_norms

    # sim[i, j] = cosine similarity between chunk i and concept j
    sim_matrix = chunk_unit @ concept_unit.T  # shape (n_chunks, n_concepts)
    max_sim = sim_matrix.max(axis=1)  # shape (n_chunks,)

    kept: list[tuple[Chunk, str]] = []
    dropped = 0
    for (chunk, reason), sim in zip(candidates, max_sim):
        if sim >= _CONCEPT_SIM_THRESHOLD:
            kept.append((chunk, reason))
        else:
            dropped += 1
            logger.debug(
                "concept_aware_prefilter: dropping chunk %s (max_sim=%.3f < %.2f)",
                chunk.id[:16],
                float(sim),
                _CONCEPT_SIM_THRESHOLD,
            )

    logger.info(
        "concept_aware_prefilter: epoch %d — %d candidates: %d kept, %d dropped; "
        "%d exploration chunks bypass filter",
        epoch,
        len(candidates),
        len(kept),
        dropped,
        len(exploration),
    )

    return kept + exploration


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

    Progressive mining pipeline:
    1. Call get_mining_frontier() to select which chunks to process this epoch.
    2. Apply concept_aware_prefilter() if epoch > 1.
    3. Group selected chunks by paper_id for cross-chunk context threading.
    4. Process papers in parallel via ThreadPoolExecutor.
       Per paper: extract_concepts_from_source() -> stage_extractions() -> record_mining().
    5. commit_staged_extractions() to merge into ConceptRecord table.
    6. Return all ConceptRecord rows touched this epoch.

    Fallback for tests / profiler: if paper_ids is empty this function returns
    immediately.  The function is also fully compatible with being called in a
    context where ChunkMiningLog is empty (epoch 1 behaviour).

    Args:
        paper_ids: List of Paper.id values to process.
        epoch:     Current epoch number.
        model:     litellm model string.  Defaults to HAIKU_MODEL.

    Returns:
        All ConceptRecord rows whose epoch_last_updated == epoch.
    """
    resolved_model = model or HAIKU_MODEL

    if not paper_ids:
        logger.info("discover_concepts: no paper_ids provided, nothing to do")
        return []

    # Determine worker count: 60% of CPU cores, min 2, max 8
    max_workers = max(2, min(8, int((os.cpu_count() or 1) * 0.6)))
    logger.info(
        "discover_concepts: epoch %d, %d papers, %d workers",
        epoch,
        len(paper_ids),
        max_workers,
    )

    # ── Step 1: determine the mining frontier ──────────────────────────────────
    frontier: list[tuple[Chunk, str]] = get_mining_frontier(paper_ids, epoch)

    if not frontier:
        logger.info(
            "discover_concepts: epoch %d — mining frontier is empty, nothing to process",
            epoch,
        )
        # Still return any records already in DB for this epoch
        with get_session() as session:
            return list(
                session.exec(
                    select(ConceptRecord).where(ConceptRecord.epoch_last_updated == epoch)
                ).all()
            )

    # ── Step 2: concept-aware pre-filter (epoch > 1 only) ─────────────────────
    frontier = concept_aware_prefilter(frontier, epoch)

    logger.info(
        "discover_concepts: epoch %d — %d chunks after concept-aware pre-filter",
        epoch,
        len(frontier),
    )

    # ── Step 3: group chunks by paper_id for per-paper processing ─────────────
    # Build a mapping: paper_id -> list of (chunk, source_reason) in chunk_index order
    paper_frontier: dict[str, list[tuple[Chunk, str]]] = defaultdict(list)
    for chunk, reason in frontier:
        paper_frontier[chunk.paper_id].append((chunk, reason))

    # Sort each paper's chunks by chunk_index to preserve document order
    for pid in paper_frontier:
        paper_frontier[pid].sort(key=lambda cr: cr[0].chunk_index)

    logger.info(
        "discover_concepts: epoch %d — processing %d papers from frontier",
        epoch,
        len(paper_frontier),
    )

    # ── Step 4: parallel per-paper processing ─────────────────────────────────
    def _process_paper(paper_id: str, chunk_reasons: list[tuple[Chunk, str]]) -> None:
        """Process one paper's frontier chunks: extract, stage, record."""
        chunks_only = [c for c, _ in chunk_reasons]

        logger.info(
            "discover_concepts: processing paper %s (%d frontier chunks)",
            paper_id[:16],
            len(chunks_only),
        )

        extractions = extract_concepts_from_source(
            source_id=paper_id,
            chunks=chunks_only,
            epoch=epoch,
            model=resolved_model,
        )
        stage_extractions(epoch=epoch, source_id=paper_id, extractions=extractions)

        # Record each chunk as mined
        for chunk, reason in chunk_reasons:
            # Map source_reason -> source label for ChunkMiningLog
            source_label = "exploration" if reason == "exploration" else "scheduled"
            record_mining(
                chunk_id=chunk.id,
                paper_id=paper_id,
                epoch=epoch,
                tier=_chunk_tier(chunk),
                source=source_label,
            )

    from concurrent.futures import ThreadPoolExecutor, as_completed

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_process_paper, pid, chunk_reasons): pid
            for pid, chunk_reasons in paper_frontier.items()
        }
        for future in as_completed(futures):
            pid = futures[future]
            try:
                future.result()
            except Exception:
                logger.exception("discover_concepts: failed on paper %s", pid[:16])

    # ── Step 5: commit all staged extractions ─────────────────────────────────
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
