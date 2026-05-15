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


def test_build_person_draft_uses_author_alias_for_context(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "run").mkdir()
    bundle = Bundle(root=bundle_dir)
    slug, _ = create_concept(
        bundle,
        page_id="A. Author",
        kind="person",
        aliases=["Alice Adams"],
    )
    corpus = _make_corpus(tmp_path / "corpus")
    from wikify.corpus.store.routing import open_store

    store = open_store(corpus.root)
    try:
        store.con.execute(
            "UPDATE documents SET authors_json = ? WHERE doc_id = ?",
            ('["Alice Adams"]', "paper_0"),
        )
        store.con.commit()
    finally:
        store.close()

    request = build_draft(
        bundle,
        slug=slug,
        corpus=corpus,
        model_id="claude-sonnet-4-6",
        tier="M",
    )

    assert request.author_context is not None
    assert request.author_context["primary_publications"][0]["doc_id"] == "paper_0"


def test_build_person_draft_uses_author_handle_alias_for_context(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "run").mkdir()
    bundle = Bundle(root=bundle_dir)
    slug, _ = create_concept(
        bundle,
        page_id="A. Author",
        kind="person",
        aliases=["author:alice_adams"],
    )
    corpus = _make_corpus(tmp_path / "corpus")
    from wikify.corpus.store.routing import open_store

    store = open_store(corpus.root)
    try:
        store.con.execute(
            "UPDATE documents SET authors_json = ? WHERE doc_id = ?",
            ('["Alice Adams"]', "paper_0"),
        )
        store.con.commit()
    finally:
        store.close()

    request = build_draft(
        bundle,
        slug=slug,
        corpus=corpus,
        model_id="claude-sonnet-4-6",
        tier="M",
    )

    assert request.author_context is not None
    assert request.author_context["display_name"] == "Alice Adams"


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


def test_build_draft_writes_dossier(tmp_path: Path) -> None:
    """``draft build`` writes ``dossier.md`` next to ``draft.json``. The
    dossier groups chunks by document and shows a marker index."""
    from wikify.bundle.draft.artifact import dossier_path
    from wikify.bundle.work.evidence import EvidenceRecord, append_evidence

    bundle, corpus, slug = _bundle_with_concept(tmp_path)
    # Two chunks from the same paper, plus one from a second paper.
    append_evidence(
        bundle,
        slug,
        [
            EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0"),
            EvidenceRecord(chunk_id="paper_0__c0001", doc_id="paper_0"),
            EvidenceRecord(chunk_id="paper_1__c0000", doc_id="paper_1"),
        ],
    )
    build_draft(bundle, slug=slug, corpus=corpus, model_id="claude-sonnet-4-6", tier="M")
    p = dossier_path(bundle, slug)
    assert p.is_file()
    body = p.read_text(encoding="utf-8")
    assert "page_id: Atomic Layer Deposition" in body
    assert "evidence_records: 3" in body
    assert "## Marker index" in body
    assert "| e1 |" in body
    assert "| e2 |" in body
    assert "| e3 |" in body
    # paper_0 should appear once with its 2 chunks (e1, e2) and paper_1
    # should appear with e3.
    assert body.count("### paper_0") >= 1
    assert "_Chunks: 2 (e1, e2)_" in body or "_Chunks: 2 (e2, e1)_" in body
    assert "_Chunks: 1 (e3)_" in body


def test_build_draft_with_adjacent_populates_context_window(tmp_path: Path) -> None:
    """``--with-adjacent`` loads ord-1 and ord+1 chunks of the same doc into
    ``context_window`` so the writer sees flanking context. The primary
    ``chunk_text`` and ``chunk_id`` must be unchanged.
    """
    bundle, corpus, slug = _bundle_with_concept(tmp_path)
    # paper_0 has 2 chunks in the fixture (c0000, c0001); cite c0000 so
    # only the trailing neighbour exists.
    append_evidence(
        bundle,
        slug,
        [EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0")],
    )
    without = build_draft(
        bundle, slug=slug, corpus=corpus,
        model_id="claude-sonnet-4-6", tier="M",
        with_adjacent=False,
    )
    assert without.evidence[0].context_window == ""

    with_adj = build_draft(
        bundle, slug=slug, corpus=corpus,
        model_id="claude-sonnet-4-6", tier="M",
        with_adjacent=True,
    )
    ev = with_adj.evidence[0]
    assert ev.chunk_id == "paper_0__c0000"
    assert "Chunk 0" in ev.chunk_text
    # c0000 is ord=0; only the next chunk (c0001) exists in the doc.
    assert "[next ord=1]" in ev.context_window
    assert "Chunk 1" in ev.context_window
    assert "[prev" not in ev.context_window


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
