"""Tests for wikify.bundle.draft.builder — DraftBuilder.build()."""

from __future__ import annotations

from pathlib import Path

import pytest

# Reuse the on-disk corpus builder from the corpus-queries tests.
from tests.wikify.test_corpus_queries import _make_corpus  # noqa: E402
from wikify.api import Bundle, Corpus
from wikify.bundle.draft.artifact import draft_path, read_json
from wikify.bundle.draft.builder import build_draft, load_draft
from wikify.bundle.work.card import create_concept
from wikify.bundle.work.evidence import EvidenceRecord, append_evidence


def _bundle_with_concept(tmp_path: Path) -> tuple[Bundle, Corpus, str]:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "run").mkdir()
    bundle = Bundle(root=bundle_dir)
    s, _ = create_concept(
        bundle, page_id="Atomic Layer Deposition", aliases=["ALD"]
    )
    corpus = _make_corpus(tmp_path / "corpus")
    return bundle, corpus, s


def test_build_draft_writes_json(tmp_path: Path) -> None:
    bundle, corpus, slug = _bundle_with_concept(tmp_path)
    append_evidence(
        bundle,
        slug,
        [EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0", score=0.9)],
    )
    request = build_draft(
        bundle,
        slug=slug,
        corpus=corpus,
        task="create",
        model_id="claude-sonnet-4-6",
        tier="M",
    )

    assert request.page_id == "Atomic Layer Deposition"
    assert request.page_kind == "article"
    assert request.aliases == ["ALD"]
    assert len(request.evidence) == 1
    assert request.evidence[0].chunk_id == "paper_0__c0000"
    assert "atomic layer deposition" in request.evidence[0].chunk_text.lower()


def test_build_draft_persists_to_disk(tmp_path: Path) -> None:
    bundle, corpus, slug = _bundle_with_concept(tmp_path)
    append_evidence(
        bundle,
        slug,
        [EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0")],
    )
    build_draft(bundle, slug=slug, corpus=corpus, model_id="claude-sonnet-4-6", tier="M")

    p = draft_path(bundle, slug)
    assert p.is_file()
    payload = read_json(p)
    assert payload["schema_version"] == 1
    assert payload["task"] == "create"
    assert payload["page_id"] == "Atomic Layer Deposition"


def test_build_draft_only_active_evidence(tmp_path: Path) -> None:
    """Archived evidence records do not enter the draft."""
    bundle, corpus, slug = _bundle_with_concept(tmp_path)
    append_evidence(
        bundle,
        slug,
        [
            EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0", status="active"),
            EvidenceRecord(chunk_id="paper_0__c0001", doc_id="paper_0", status="archived"),
        ],
    )
    request = build_draft(bundle, slug=slug, corpus=corpus, model_id="claude-sonnet-4-6", tier="M")
    assert len(request.evidence) == 1
    assert request.evidence[0].chunk_id == "paper_0__c0000"


def test_build_draft_unknown_concept(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "run").mkdir()
    bundle = Bundle(root=bundle_dir)
    corpus = _make_corpus(tmp_path / "corpus")
    with pytest.raises(FileNotFoundError, match="work.md"):
        build_draft(bundle, slug="no-such", corpus=corpus, model_id="claude-sonnet-4-6", tier="M")


def test_load_draft_roundtrip(tmp_path: Path) -> None:
    bundle, corpus, slug = _bundle_with_concept(tmp_path)
    append_evidence(
        bundle, slug, [EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0")]
    )
    built = build_draft(bundle, slug=slug, corpus=corpus, model_id="claude-sonnet-4-6", tier="M")
    loaded = load_draft(bundle, slug)
    assert loaded.page_id == built.page_id
    assert loaded.evidence[0].chunk_id == built.evidence[0].chunk_id
