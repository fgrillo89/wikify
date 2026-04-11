"""Database garbage collection and integrity checks.

As the wiki grows iteratively (epochs, campaigns, merges), orphaned rows
accumulate. This module provides:

- gc_run(): Full garbage collection pass
- integrity_check(): Read-only health report
- redirect_merged(): Fix references to merged concepts
- clean_chromadb_staging(): Remove stale staging entries

Run after each epoch or via /wiki-maintain.
"""

from __future__ import annotations

import logging

from sqlmodel import select

from wikify.core.store.db import get_session
from wikify.core.store.models import (
    ConceptEvidence,
    ConceptRecord,
    ConceptRelation,
    Paper,
    ParameterExtraction,
)

logger = logging.getLogger(__name__)


def integrity_check() -> dict:
    """Read-only health check of DB referential integrity.

    Returns a dict with counts of orphaned/problematic rows.
    """
    with get_session() as s:
        concepts = list(s.exec(select(ConceptRecord)).all())
        evidence = list(s.exec(select(ConceptEvidence)).all())
        params = list(s.exec(select(ParameterExtraction)).all())
        relations = list(s.exec(select(ConceptRelation)).all())
        papers = list(s.exec(select(Paper)).all())

    concept_ids = {c.id for c in concepts}
    paper_ids = {p.id for p in papers}
    merged = {
        c.id: c.article_status.split(":")[1]
        for c in concepts
        if c.article_status and c.article_status.startswith("merged:")
    }

    orphan_evidence = sum(1 for e in evidence if e.concept_id not in concept_ids)
    orphan_params = sum(1 for p in params if p.concept_id and p.concept_id not in concept_ids)
    dangling_rels = sum(
        1
        for r in relations
        if r.source_concept not in concept_ids or r.target_concept not in concept_ids
    )
    evidence_to_merged = sum(1 for e in evidence if e.concept_id in merged)
    evidence_bad_paper = sum(1 for e in evidence if e.paper_id and e.paper_id not in paper_ids)

    report = {
        "total_concepts": len(concepts),
        "merged_concepts": len(merged),
        "total_evidence": len(evidence),
        "total_params": len(params),
        "total_relations": len(relations),
        "orphan_evidence": orphan_evidence,
        "orphan_params": orphan_params,
        "dangling_relations": dangling_rels,
        "evidence_to_merged": evidence_to_merged,
        "evidence_bad_paper": evidence_bad_paper,
    }

    logger.info("integrity_check: %s", report)
    return report


def merge_concepts_atomic(
    primary_id: str,
    secondary_id: str,
) -> int:
    """Atomically merge secondary concept into primary, redirecting all refs.

    In a single transaction:
    1. Merge aliases from secondary into primary
    2. Redirect all evidence, params, relations to primary
    3. Mark secondary as merged
    4. Delete secondary's article file if it exists

    This prevents garbage production by doing everything atomically.

    Args:
        primary_id: ConceptRecord.id to keep.
        secondary_id: ConceptRecord.id to merge away.

    Returns:
        Number of rows redirected.
    """
    import json
    from pathlib import Path

    count = 0

    with get_session() as s:
        primary = s.get(ConceptRecord, primary_id)
        secondary = s.get(ConceptRecord, secondary_id)

        if not primary or not secondary:
            logger.warning(
                "merge_concepts_atomic: concept not found (primary=%s, secondary=%s)",
                primary_id,
                secondary_id,
            )
            return 0

        # 1. Merge aliases
        p_aliases = set(json.loads(primary.aliases or "[]"))
        s_aliases = set(json.loads(secondary.aliases or "[]"))
        p_aliases.add(secondary.name)
        p_aliases |= s_aliases
        primary.aliases = json.dumps(sorted(p_aliases))
        s.add(primary)

        # 2. Redirect evidence
        for row in s.exec(
            select(ConceptEvidence).where(ConceptEvidence.concept_id == secondary_id)
        ).all():
            row.concept_id = primary_id
            s.add(row)
            count += 1

        # Redirect parameters
        for row in s.exec(
            select(ParameterExtraction).where(ParameterExtraction.concept_id == secondary_id)
        ).all():
            row.concept_id = primary_id
            s.add(row)
            count += 1

        # Redirect relations
        for row in s.exec(
            select(ConceptRelation).where(ConceptRelation.source_concept == secondary_id)
        ).all():
            row.source_concept = primary_id
            s.add(row)
            count += 1

        for row in s.exec(
            select(ConceptRelation).where(ConceptRelation.target_concept == secondary_id)
        ).all():
            row.target_concept = primary_id
            s.add(row)
            count += 1

        # 3. Mark secondary as merged
        secondary.article_status = f"merged:{primary_id}"
        s.add(secondary)

        s.commit()

    # 4. Delete secondary article file
    wiki_dir = Path("data/wiki")
    secondary_file = wiki_dir / "concepts" / f"{secondary_id}.md"
    if secondary_file.exists():
        secondary_file.unlink()
        logger.info("merge_concepts_atomic: deleted %s", secondary_file.name)

    logger.info(
        "merge_concepts_atomic: %s -> %s (%d rows redirected)",
        secondary_id,
        primary_id,
        count,
    )
    return count


def redirect_merged() -> int:
    """Redirect evidence/params/relations from merged concepts to their primaries.

    When concept A is merged into concept B (A.article_status = "merged:B"),
    all evidence, parameters, and relations pointing to A should point to B.

    Returns:
        Number of rows redirected.
    """
    with get_session() as s:
        concepts = list(s.exec(select(ConceptRecord)).all())

    # Build redirect map: merged_id -> primary_id
    redirect: dict[str, str] = {}
    for c in concepts:
        if c.article_status and c.article_status.startswith("merged:"):
            primary = c.article_status.split(":", 1)[1]
            redirect[c.id] = primary

    if not redirect:
        return 0

    count = 0
    with get_session() as s:
        # Redirect evidence
        for old_id, new_id in redirect.items():
            rows = list(
                s.exec(select(ConceptEvidence).where(ConceptEvidence.concept_id == old_id)).all()
            )
            for row in rows:
                row.concept_id = new_id
                s.add(row)
                count += 1

        # Redirect parameters
        for old_id, new_id in redirect.items():
            rows = list(
                s.exec(
                    select(ParameterExtraction).where(ParameterExtraction.concept_id == old_id)
                ).all()
            )
            for row in rows:
                row.concept_id = new_id
                s.add(row)
                count += 1

        # Redirect relations
        for old_id, new_id in redirect.items():
            rows = list(
                s.exec(
                    select(ConceptRelation).where(ConceptRelation.source_concept == old_id)
                ).all()
            )
            for row in rows:
                row.source_concept = new_id
                s.add(row)
                count += 1

            rows = list(
                s.exec(
                    select(ConceptRelation).where(ConceptRelation.target_concept == old_id)
                ).all()
            )
            for row in rows:
                row.target_concept = new_id
                s.add(row)
                count += 1

        s.commit()

    logger.info("redirect_merged: redirected %d rows", count)
    return count


def remove_orphans() -> dict[str, int]:
    """Delete rows referencing nonexistent concepts or papers.

    Returns:
        Dict with counts of deleted rows per table.
    """
    with get_session() as s:
        concept_ids = {c.id for c in s.exec(select(ConceptRecord)).all()}
        paper_ids = {p.id for p in s.exec(select(Paper)).all()}

    deleted: dict[str, int] = {}

    with get_session() as s:
        # Orphan evidence (concept doesn't exist)
        orphans = list(s.exec(select(ConceptEvidence)).all())
        bad = [e for e in orphans if e.concept_id not in concept_ids]
        for row in bad:
            s.delete(row)
        deleted["evidence"] = len(bad)

        # Orphan evidence (paper doesn't exist)
        bad_paper = [e for e in orphans if e.paper_id and e.paper_id not in paper_ids]
        for row in bad_paper:
            if row not in bad:  # avoid double-delete
                s.delete(row)
                deleted["evidence"] = deleted.get("evidence", 0) + 1

        s.commit()

    with get_session() as s:
        # Orphan parameters
        all_params = list(s.exec(select(ParameterExtraction)).all())
        bad = [p for p in all_params if p.concept_id and p.concept_id not in concept_ids]
        for row in bad:
            s.delete(row)
        deleted["parameters"] = len(bad)
        s.commit()

    with get_session() as s:
        # Dangling relations
        all_rels = list(s.exec(select(ConceptRelation)).all())
        bad = [
            r
            for r in all_rels
            if r.source_concept not in concept_ids or r.target_concept not in concept_ids
        ]
        for row in bad:
            s.delete(row)
        deleted["relations"] = len(bad)
        s.commit()

    logger.info("remove_orphans: %s", deleted)
    return deleted


def clean_chromadb_staging() -> int:
    """Remove all entries from the concept_staging ChromaDB collection.

    Returns:
        Number of entries removed.
    """
    from wikify.core.store.embeddings import _store

    try:
        _ = _store.collection  # ensure client initialized
        client = _store._client
        staging = client.get_collection("concept_staging")
        count = staging.count()
        if count > 0:
            # Get all IDs and delete
            result = staging.get(include=[])
            ids = result.get("ids", [])
            if ids:
                staging.delete(ids=ids)
        logger.info("clean_chromadb_staging: removed %d entries", count)
        return count
    except Exception:
        logger.debug("clean_chromadb_staging: no staging collection")
        return 0


def gc_run() -> dict:
    """Full garbage collection pass.

    Runs in order:
    1. Redirect merged concept references to primaries
    2. Remove orphaned rows
    3. Clean ChromaDB staging

    Returns:
        Summary dict with all actions taken.
    """
    logger.info("gc_run: starting garbage collection")

    redirected = redirect_merged()
    orphans = remove_orphans()
    staging = clean_chromadb_staging()

    summary = {
        "redirected": redirected,
        "orphans_removed": orphans,
        "staging_cleaned": staging,
    }

    logger.info("gc_run: complete -> %s", summary)
    return summary
