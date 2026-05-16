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


def test_dossier_orders_chunks_within_doc_by_chunk_ord(tmp_path: Path) -> None:
    """Within a single paper, chunks render in ``ord`` order (intro before body),
    not in evidence-list insertion order."""
    from wikify.bundle.draft.artifact import dossier_path

    bundle, corpus, slug = _bundle_with_concept(tmp_path)
    # Append in reverse ord order so insertion order != desired render order.
    append_evidence(
        bundle,
        slug,
        [
            EvidenceRecord(chunk_id="paper_0__c0001", doc_id="paper_0"),
            EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0"),
        ],
    )
    build_draft(bundle, slug=slug, corpus=corpus, model_id="claude-sonnet-4-6", tier="M")
    body = dossier_path(bundle, slug).read_text(encoding="utf-8")
    # The lower-ord chunk (c0000, marker e2) must appear before c0001 (e1)
    # in the body section. _short_chunk renders the last 12 chars of the id.
    after = body.split("## Evidence", 1)[1]
    i0 = after.index("c0000")
    i1 = after.index("c0001")
    assert i0 < i1


def test_dossier_orders_docs_by_max_score_desc(tmp_path: Path) -> None:
    """Across docs, the paper with the higher per-doc max score leads.
    Tie-break is doc_id ascending."""
    from wikify.bundle.draft.artifact import dossier_path

    bundle, corpus, slug = _bundle_with_concept(tmp_path)
    # paper_0 has a max score of 0.2; paper_1 has 0.9 → paper_1 must lead.
    append_evidence(
        bundle,
        slug,
        [
            EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0", score=0.2),
            EvidenceRecord(chunk_id="paper_1__c0000", doc_id="paper_1", score=0.9),
        ],
    )
    build_draft(bundle, slug=slug, corpus=corpus, model_id="claude-sonnet-4-6", tier="M")
    body = dossier_path(bundle, slug).read_text(encoding="utf-8")
    # In the body section (after "## Evidence"), paper_1 must appear before paper_0.
    after = body.split("## Evidence", 1)[1]
    assert after.index("### paper_1") < after.index("### paper_0")


def test_dossier_doc_order_tiebreaks_on_doc_id(tmp_path: Path) -> None:
    """Equal max scores → docs sorted by doc_id ascending for determinism."""
    from wikify.bundle.draft.artifact import dossier_path

    bundle, corpus, slug = _bundle_with_concept(tmp_path)
    append_evidence(
        bundle,
        slug,
        [
            EvidenceRecord(chunk_id="paper_1__c0000", doc_id="paper_1", score=0.5),
            EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0", score=0.5),
        ],
    )
    build_draft(bundle, slug=slug, corpus=corpus, model_id="claude-sonnet-4-6", tier="M")
    body = dossier_path(bundle, slug).read_text(encoding="utf-8")
    after = body.split("## Evidence", 1)[1]
    assert after.index("### paper_0") < after.index("### paper_1")


def test_dossier_empty_evidence_renders_no_op(tmp_path: Path) -> None:
    """No evidence → dossier still renders with the placeholder line."""
    from wikify.bundle.draft.artifact import dossier_path

    bundle, corpus, slug = _bundle_with_concept(tmp_path)
    build_draft(bundle, slug=slug, corpus=corpus, model_id="claude-sonnet-4-6", tier="M")
    body = dossier_path(bundle, slug).read_text(encoding="utf-8")
    assert "_No evidence records._" in body


def test_dossier_omits_adjacent_block_by_default(tmp_path: Path) -> None:
    """Default ``draft build`` (no ``--with-adjacent``) must not emit the
    ``<details>Adjacent chunks ...`` block — it misled writers in earlier
    smoke runs and tripled token spend by duplicating primary chunks."""
    from wikify.bundle.draft.artifact import dossier_path

    bundle, corpus, slug = _bundle_with_concept(tmp_path)
    append_evidence(
        bundle, slug, [EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0")]
    )
    build_draft(bundle, slug=slug, corpus=corpus, model_id="claude-sonnet-4-6", tier="M")
    body = dossier_path(bundle, slug).read_text(encoding="utf-8")
    assert "Adjacent chunks" not in body
    assert "synthesis context" not in body


def test_dossier_includes_adjacent_block_when_opted_in(tmp_path: Path) -> None:
    """``--with-adjacent`` still produces the ``<details>`` block as before."""
    from wikify.bundle.draft.artifact import dossier_path

    bundle, corpus, slug = _bundle_with_concept(tmp_path)
    append_evidence(
        bundle, slug, [EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0")]
    )
    build_draft(
        bundle,
        slug=slug,
        corpus=corpus,
        model_id="claude-sonnet-4-6",
        tier="M",
        with_adjacent=True,
    )
    body = dossier_path(bundle, slug).read_text(encoding="utf-8")
    assert "Adjacent chunks" in body


def test_dossier_omits_inline_figures_caption_block(tmp_path: Path) -> None:
    """The per-chunk ``**Figures referenced in this chunk ...**`` listing
    duplicates the top-of-dossier ``## Figure candidates`` table and is
    intentionally dropped from the renderer."""
    from wikify.bundle.draft.dossier import render_dossier
    from wikify.schema import WriteEvidenceRef, WriteRequest

    req = WriteRequest(
        page_id="X",
        page_kind="article",
        title="X",
        aliases=[],
        skeleton="",
        prompt_template="",
        model_id="claude-sonnet-4-6",
        tier="M",
        evidence=[
            WriteEvidenceRef(
                chunk_id="paper_0__c0000",
                doc_id="paper_0",
                quote="",
                chunk_text="Body text.",
                chunk_figures=["Figure 1. A caption that should NOT appear inline."],
            ),
        ],
    )
    body = render_dossier(req)
    assert "**Figures referenced in this chunk" not in body
    assert "A caption that should NOT appear inline" not in body


def test_dossier_orders_unknown_chunk_ord_last() -> None:
    """Within a doc, a record with ``chunk_ord=-1`` (corpus lookup failed)
    must render AFTER records with a known ``chunk_ord``.

    The dossier sorts ascending on ``chunk_ord`` with unknown ord (-1)
    coerced to +infinity so it lands at the tail rather than at the head
    (where a naive numeric sort would put -1).
    """
    from wikify.bundle.draft.dossier import render_dossier
    from wikify.schema import WriteEvidenceRef, WriteRequest

    req = WriteRequest(
        page_id="X",
        page_kind="article",
        title="X",
        aliases=[],
        skeleton="",
        prompt_template="",
        model_id="claude-sonnet-4-6",
        tier="M",
        evidence=[
            WriteEvidenceRef(
                chunk_id="paper_0__cUNKNOWN",
                doc_id="paper_0",
                quote="",
                chunk_text="Unknown-ord body.",
                chunk_ord=-1,
            ),
            WriteEvidenceRef(
                chunk_id="paper_0__c0002",
                doc_id="paper_0",
                quote="",
                chunk_text="Known-ord body.",
                chunk_ord=2,
            ),
        ],
    )
    body = render_dossier(req)
    after = body.split("## Evidence", 1)[1]
    i_known = after.index("c0002")
    i_unknown = after.index("cUNKNOWN")
    assert i_known < i_unknown


def test_write_evidence_ref_round_trip_preserves_score_and_chunk_ord() -> None:
    """``WriteEvidenceRef`` survives a ``model_dump`` / ``model_validate``
    round trip with ``score`` and ``chunk_ord`` intact (the two PR #72
    follow-up fields the builder now populates from the corpus row)."""
    from wikify.schema import WriteEvidenceRef

    ref = WriteEvidenceRef(
        chunk_id="paper_0__c0003",
        doc_id="paper_0",
        quote="",
        chunk_text="body",
        score=0.75,
        chunk_ord=3,
    )
    dumped = ref.model_dump(mode="json")
    reloaded = WriteEvidenceRef.model_validate(dumped)
    assert reloaded.score == 0.75
    assert reloaded.chunk_ord == 3


def test_load_draft_roundtrip(tmp_path: Path) -> None:
    bundle, corpus, slug = _bundle_with_concept(tmp_path)
    append_evidence(
        bundle, slug, [EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0")]
    )
    built = build_draft(bundle, slug=slug, corpus=corpus, model_id="claude-sonnet-4-6", tier="M")
    loaded = load_draft(bundle, slug)
    assert loaded.page_id == built.page_id
    assert loaded.evidence[0].chunk_id == built.evidence[0].chunk_id
