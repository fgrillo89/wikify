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


def test_dossier_renders_curated_quote_when_vetter_supplied_one() -> None:
    """A vetter-supplied ``quote`` (different from the chunk's leading
    text) renders as a "Selected quote" block above the chunk text so
    the writer reads the on-topic sentence first."""
    from wikify.bundle.draft.dossier import render_dossier
    from wikify.schema import WriteEvidenceRef, WriteRequest

    chunk_text = (
        "Ryan Goul, Angelo Marshall, Sierra Seacat. "
        "Herein, we report atomically tunable Pd/M1/M2/Al ultrathin "
        "memristors using in vacuo atomic layer deposition by controlled "
        "insertion of MgO atomic layers."
    )
    curated_quote = (
        "Herein, we report atomically tunable Pd/M1/M2/Al ultrathin "
        "memristors using in vacuo atomic layer deposition by controlled "
        "insertion of MgO atomic layers."
    )
    req = WriteRequest(
        page_id="Atomic Layer Deposition",
        page_kind="article",
        title="Atomic Layer Deposition",
        aliases=["ALD"],
        skeleton="",
        prompt_template="",
        model_id="claude-sonnet-4-6",
        tier="M",
        evidence=[
            WriteEvidenceRef(
                chunk_id="paper_0__c0000",
                doc_id="paper_0",
                quote=curated_quote,
                chunk_text=chunk_text,
                section_type="body",
                score=1.0,
                chunk_ord=0,
            )
        ],
    )
    body = render_dossier(req)
    assert "**Selected quote:**" in body
    assert curated_quote in body
    # Selected quote block appears BEFORE the chunk_text head ("Ryan Goul,
    # Angelo Marshall, Sierra Seacat.") so the writer reads it first.
    i_quote = body.index("**Selected quote:**")
    i_byline = body.index("Ryan Goul, Angelo Marshall")
    assert i_quote < i_byline


def test_dossier_suppresses_selected_quote_when_default_text_head_fallback() -> None:
    """When ``quote`` equals the chunk's leading text (i.e., the default
    ``text[:400]`` fallback because no vetter curated it), the "Selected
    quote" block is suppressed — repeating the chunk head adds noise."""
    from wikify.bundle.draft.dossier import render_dossier
    from wikify.schema import WriteEvidenceRef, WriteRequest

    chunk_text = "The first sentence is the lead. The second is detail."
    # Default fallback shape: quote IS the chunk head.
    default_quote = chunk_text[:30]
    assert chunk_text.startswith(default_quote)  # sanity
    req = WriteRequest(
        page_id="Atomic Layer Deposition",
        page_kind="article",
        title="Atomic Layer Deposition",
        aliases=[],
        skeleton="",
        prompt_template="",
        model_id="claude-sonnet-4-6",
        tier="M",
        evidence=[
            WriteEvidenceRef(
                chunk_id="paper_0__c0000",
                doc_id="paper_0",
                quote=default_quote,
                chunk_text=chunk_text,
                section_type="body",
                score=1.0,
                chunk_ord=0,
            )
        ],
    )
    body = render_dossier(req)
    assert "**Selected quote:**" not in body
    # Chunk text still renders.
    assert chunk_text in body


def test_load_draft_roundtrip(tmp_path: Path) -> None:
    bundle, corpus, slug = _bundle_with_concept(tmp_path)
    append_evidence(
        bundle, slug, [EvidenceRecord(chunk_id="paper_0__c0000", doc_id="paper_0")]
    )
    built = build_draft(bundle, slug=slug, corpus=corpus, model_id="claude-sonnet-4-6", tier="M")
    loaded = load_draft(bundle, slug)
    assert loaded.page_id == built.page_id
    assert loaded.evidence[0].chunk_id == built.evidence[0].chunk_id


# ---------------------------------------------------------------------------
# Figure-candidate resolution via short doc:hex handles
# ---------------------------------------------------------------------------

PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfeA5\xc8\x91\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _corpus_with_hex_doc_and_image(root: Path):
    """Return a Corpus whose doc_id ends in a 12-hex suffix.

    The evidence workflow stores these as ``doc:<hex>`` short handles,
    so ``_figure_candidates_for_evidence`` must resolve the short form
    to the full doc_id to locate figures.
    """
    from wikify.corpus.store import Store, transaction
    from wikify.corpus.store.sync import project_documents
    from wikify.ingest.images import save_doc_images
    from wikify.ingest.parsers.registry import RawImage
    from wikify.models import Chunk, Document

    full_doc_id = "[2020 Test] Sample Paper_aabbccddeeff"
    hex_suffix = "aabbccddeeff"

    corpus = Corpus(root=root)
    corpus.ensure()

    folder = corpus.images_dir / "2020_Test_Sample"
    raw_images = [
        RawImage(
            data=PNG_1x1,
            ext="png",
            page=1,
            caption="Schematic of the ALD cycle.",
            label="Fig. 1",
            media_type="figure",
            width=400,
            height=300,
        ),
    ]
    saved = save_doc_images(full_doc_id, folder, raw_images)

    doc = Document(
        id=full_doc_id,
        source_path=f"src/{full_doc_id}.pdf",
        kind="pdf",
        title="Sample Paper",
        metadata={"year": 2020},
        markdown_path=f"markdown/{full_doc_id}.md",
        image_dir=str(folder),
        images=saved,
    )
    chunk = Chunk(
        id=f"{full_doc_id}__c0000",
        doc_id=full_doc_id,
        ord=0,
        text="The ALD cycle schematic (Fig. 1) shows the precursor sequence.",
        char_span=(0, 60),
        section_path=["body"],
        section_type="body",
    )

    store = Store(corpus.sqlite_path)
    try:
        with transaction(store.con):
            project_documents(store, [doc], {full_doc_id: [chunk]})
    finally:
        store.close()

    asset_stem = "Figure_01"
    asset_id_str = f"{full_doc_id}/{asset_stem}"

    import json as _json
    import sqlite3
    con = sqlite3.connect(str(corpus.sqlite_path))
    try:
        # Patch metadata_json to include dimensions so the decoration filter
        # sees real (non-tiny) dimensions without opening the 1x1 test PNG.
        con.execute(
            "UPDATE assets SET metadata_json = ? WHERE asset_id = ?",
            (_json.dumps({"width": 500, "height": 400, "label": "Fig. 1"}), asset_id_str),
        )
        # Link the chunk to the figure via chunk_assets so the near-chunk filter passes.
        con.execute(
            "INSERT OR IGNORE INTO chunk_assets"
            " (chunk_id, asset_id, relation) VALUES (?, ?, 'near')",
            (chunk.id, asset_id_str),
        )
        con.commit()
    finally:
        con.close()

    return corpus, full_doc_id, hex_suffix


def test_figure_candidates_resolves_short_doc_hex_handle(tmp_path: Path) -> None:
    """``_figure_candidates_for_evidence`` must find figures even when the
    evidence record stores ``doc:<hex>`` rather than the full doc_id.

    This is the primary fix: ``ImageIndex.by_doc`` is keyed by full doc_ids,
    but evidence records carry short handles like ``doc:aabbccddeeff``.
    """
    from wikify.bundle.draft.builder import _figure_candidates_for_evidence
    from wikify.bundle.work.evidence import EvidenceRecord

    corpus, full_doc_id, hex_suffix = _corpus_with_hex_doc_and_image(tmp_path / "corpus")

    # Simulate what the workflow writes: short handle form.
    short_handle = f"doc:{hex_suffix}"
    chunk_id = f"{full_doc_id}__c0000"
    records = [
        EvidenceRecord(chunk_id=chunk_id, doc_id=short_handle, status="active"),
    ]

    figures = _figure_candidates_for_evidence(corpus, records, limit=6)
    assert figures, (
        "No figures returned — short doc:hex handle was not resolved to the full doc_id"
    )
    assert figures[0].caption == "Schematic of the ALD cycle."


def test_figure_candidates_returns_empty_for_unknown_handle(tmp_path: Path) -> None:
    """An unresolvable short handle must return an empty list, not raise."""
    from wikify.bundle.draft.builder import _figure_candidates_for_evidence
    from wikify.bundle.work.evidence import EvidenceRecord

    corpus, _, _ = _corpus_with_hex_doc_and_image(tmp_path / "corpus")

    records = [
        EvidenceRecord(chunk_id="x__c0000", doc_id="doc:000000000000", status="active"),
    ]
    figures = _figure_candidates_for_evidence(corpus, records, limit=6)
    assert figures == []


def test_build_draft_figures_populated_with_hex_doc_id(tmp_path: Path) -> None:
    """``build_draft`` must populate ``request.figures`` when the evidence
    record's ``doc_id`` is in short ``doc:<hex>`` form and the corpus has
    a captioned, chunk-linked figure for that doc."""
    corpus, full_doc_id, hex_suffix = _corpus_with_hex_doc_and_image(tmp_path / "corpus")

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "run").mkdir()
    bundle = Bundle(root=bundle_dir)
    slug, _ = create_concept(bundle, page_id="ALD Cycle")

    short_handle = f"doc:{hex_suffix}"
    chunk_id = f"{full_doc_id}__c0000"
    append_evidence(
        bundle,
        slug,
        [EvidenceRecord(chunk_id=chunk_id, doc_id=short_handle, status="active")],
    )

    request = build_draft(
        bundle,
        slug=slug,
        corpus=corpus,
        model_id="claude-sonnet-4-6",
        tier="M",
    )
    assert request.figures, (
        "build_draft returned no figures — short doc:hex handle not resolved"
    )
    assert request.figures[0].caption == "Schematic of the ALD cycle."
