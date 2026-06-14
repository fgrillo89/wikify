"""Tests for wikify.bundle.work.tend — consolidation passes."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from wikify.api import Bundle
from wikify.bundle.run.lifecycle import init_run
from wikify.bundle.work.card import create_concept, list_concept_slugs, load_card
from wikify.bundle.work.evidence import EvidenceRecord, append_evidence, read_evidence
from wikify.bundle.work.inbox import append_inbox, read_inbox
from wikify.bundle.work.tend import tend_bundle


def _bundle(tmp_path: Path) -> Bundle:
    (tmp_path / "run").mkdir(parents=True)
    return Bundle(root=tmp_path)


def _bundle_with_corpus(tmp_path: Path, chunk_ids: list[tuple[str, str]]) -> tuple[Bundle, Path]:
    """Create a bundle wired to a corpus that contains *chunk_ids*.

    Returns ``(bundle, corpus_dir)``.
    """
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir(parents=True)
    db = corpus_dir / "wikify.db"
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE chunks ("
        "chunk_id TEXT PRIMARY KEY, doc_id TEXT, ord INTEGER, "
        "text TEXT, section_type TEXT, is_boilerplate INTEGER"
        ")"
    )
    for i, (cid, did) in enumerate(chunk_ids):
        con.execute(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
            (cid, did, i, f"text {cid}", "body", 0),
        )
    con.commit()
    con.close()

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir(parents=True)
    bundle = Bundle(root=bundle_dir)
    init_run(bundle, corpus_path=str(corpus_dir))
    return bundle, corpus_dir


def test_tend_drains_evidence_suggestions(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="ALD")
    append_inbox(
        bundle,
        "evidence_suggestions",
        {"concept": "ald", "chunk_id": "d1:1", "doc_id": "d1", "score": 0.9},
    )
    append_inbox(
        bundle,
        "evidence_suggestions",
        {"concept": "ald", "chunk_id": "d1:2", "doc_id": "d1", "score": 0.8},
    )
    summary = tend_bundle(bundle)
    assert summary["evidence_appended"] == 2
    assert read_inbox(bundle, "evidence_suggestions") == []
    records = read_evidence(bundle, "ald")
    assert {r.chunk_id for r in records} == {"d1:1", "d1:2"}


def test_tend_skips_evidence_for_unknown_concept(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    append_inbox(
        bundle,
        "evidence_suggestions",
        {"concept": "no-such", "chunk_id": "d1:1", "doc_id": "d1"},
    )
    summary = tend_bundle(bundle)
    # Inbox is drained even when the target concept is missing — skipped records
    # are dropped, not held forever.
    assert summary["evidence_appended"] == 0
    assert read_inbox(bundle, "evidence_suggestions") == []


def test_tend_creates_concept_from_suggestion(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    append_inbox(
        bundle,
        "concept_suggestions",
        {"title": "Atomic Layer Deposition", "kind": "article", "aliases": ["ALD"]},
    )
    summary = tend_bundle(bundle)
    assert summary["concepts_created"] == 1
    slugs = list_concept_slugs(bundle)
    assert "atomic-layer-deposition" in slugs
    card = load_card(bundle, "atomic-layer-deposition")
    assert card.aliases == ["ALD"]


def test_tend_concept_suggestion_idempotent(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="ALD")
    append_inbox(bundle, "concept_suggestions", {"title": "ALD"})
    summary = tend_bundle(bundle)
    # Existing concept skipped.
    assert summary["concepts_created"] == 0


def test_tend_query_feedback_marks_needs_refine(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="ALD")
    append_inbox(
        bundle,
        "query_feedback",
        {"query": "ALD vs CVD?", "affected_pages": ["ALD"], "gap": "no comparison"},
    )
    summary = tend_bundle(bundle)
    assert summary["query_feedback_marks"] == 1
    card = load_card(bundle, "ald")
    assert card.needs_refine is True


def test_tend_merge_suggestion_marks_both(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="ALD")
    create_concept(bundle, page_id="ALDeposition")
    append_inbox(
        bundle, "merge_suggestions", {"a": "ald", "b": "aldeposition"}
    )
    summary = tend_bundle(bundle)
    assert summary["merge_suggestion_marks"] == 2
    assert load_card(bundle, "ald").needs_refine is True
    assert load_card(bundle, "aldeposition").needs_refine is True


def test_tend_summary_includes_index_path(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="ALD")
    summary = tend_bundle(bundle)
    assert summary["index_path"].endswith("index.md")
    assert (bundle.work_index_path).is_file()


def test_tend_idempotent(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="ALD")
    summary1 = tend_bundle(bundle)
    summary2 = tend_bundle(bundle)
    assert summary1["concepts"] == summary2["concepts"]


# ---------------------------------------------------------------------------
# H1 staging sweep: handles in staging vs canonical ids in ledger


def test_sweep_staging_resolves_handles_to_canonical_ids(tmp_path: Path) -> None:
    """Staging file with chunk:<hex> handles is swept when those handles
    resolve to canonical ids already in the committed evidence ledger.

    This is the core H1 regression: the old code compared raw handles
    against canonical committed ids, which never matched, so the staging
    file was never swept.  The fixed code resolves handles first.
    """
    canonical = "[2020 Smith] ALD Review_cafebabe__c0000_aabbccdd"
    suffix = "aabbccdd"
    bundle, _corpus_dir = _bundle_with_corpus(
        tmp_path, [(canonical, "doc_0")]
    )
    create_concept(bundle, page_id="ALD", kind="article")

    # Commit evidence using the canonical id (as cmd_add_evidence does after resolving).
    append_evidence(
        bundle, "ald",
        [EvidenceRecord(chunk_id=canonical, doc_id="doc_0", status="active")],
    )

    # Staging file carries the short handle form (as written by explorer subagents).
    staging_dir = bundle.work_dir / "evidence_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_file = staging_dir / "ald.jsonl"
    staging_file.write_text(
        json.dumps({"chunk_id": f"chunk:{suffix}", "doc_id": "doc_0"}) + "\n",
        encoding="utf-8",
    )

    summary = tend_bundle(bundle)
    assert summary["staging_files_removed"] == 1, (
        "staging file should be swept when handles resolve to committed ids"
    )
    assert not staging_file.exists()


def test_sweep_staging_keeps_file_when_corpus_unreachable(tmp_path: Path) -> None:
    """When the corpus is unreachable, staging files with handles are kept
    (conservative: we cannot validate, so do not delete).
    """
    bundle = _bundle(tmp_path)
    create_concept(bundle, page_id="ALD", kind="article")

    staging_dir = bundle.work_dir / "evidence_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    staging_file = staging_dir / "ald.jsonl"
    staging_file.write_text(
        json.dumps({"chunk_id": "chunk:aabbccdd", "doc_id": "doc_0"}) + "\n",
        encoding="utf-8",
    )

    summary = tend_bundle(bundle)
    # No corpus -> cannot resolve -> file must be kept.
    assert summary["staging_files_removed"] == 0
    assert staging_file.exists()
