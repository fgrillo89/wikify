"""Tests for wikify.bundle.work.tend — consolidation passes."""

from __future__ import annotations

from pathlib import Path

from wikify.api import Bundle
from wikify.bundle.work.card import create_concept, list_concept_slugs, load_card
from wikify.bundle.work.evidence import read_evidence
from wikify.bundle.work.inbox import append_inbox, read_inbox
from wikify.bundle.work.tend import tend_bundle


def _v2(tmp_path: Path) -> Bundle:
    (tmp_path / "run").mkdir(parents=True)
    return Bundle.open(tmp_path)


def test_tend_drains_evidence_suggestions(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
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
    bundle = _v2(tmp_path)
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
    bundle = _v2(tmp_path)
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
    bundle = _v2(tmp_path)
    create_concept(bundle, page_id="ALD")
    append_inbox(bundle, "concept_suggestions", {"title": "ALD"})
    summary = tend_bundle(bundle)
    # Existing concept skipped.
    assert summary["concepts_created"] == 0


def test_tend_query_feedback_marks_needs_refine(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
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
    bundle = _v2(tmp_path)
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
    bundle = _v2(tmp_path)
    create_concept(bundle, page_id="ALD")
    summary = tend_bundle(bundle)
    assert summary["index_path"].endswith("index.md")
    assert (bundle.work_index_path).is_file()


def test_tend_idempotent(tmp_path: Path) -> None:
    bundle = _v2(tmp_path)
    create_concept(bundle, page_id="ALD")
    summary1 = tend_bundle(bundle)
    summary2 = tend_bundle(bundle)
    assert summary1["concepts"] == summary2["concepts"]
