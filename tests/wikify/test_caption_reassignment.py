"""Caption reassignment planner + Docling parser hook."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from wikify.api import Corpus
from wikify.cli import app
from wikify.corpus.images_index import is_decoration_dims, plan_caption_reassignment
from wikify.corpus.store import Store
from wikify.ingest.parsers.docling import _reassign_misbound_captions
from wikify.ingest.parsers.registry import RawImage
from wikify.models import Chunk, Document


def test_is_decoration_dims_short_side():
    assert is_decoration_dims(236, 99)
    assert is_decoration_dims(99, 236)


def test_is_decoration_dims_small_area():
    assert is_decoration_dims(150, 150)  # area 22500 < 40000


def test_is_decoration_dims_extreme_aspect():
    # 800x150: short=150 passes; area=120000 passes; aspect=5.33 > 4
    assert is_decoration_dims(800, 150)


def test_is_decoration_dims_real_figure_passes():
    assert not is_decoration_dims(1526, 798)
    assert not is_decoration_dims(691, 304)


def test_plan_caption_reassignment_banner_then_real_same_page():
    items = [
        ("banner", 5, 237, 98, "Figure 9. Real caption text."),
        ("real", 5, 1474, 779, ""),
    ]
    plan = plan_caption_reassignment(items)
    assert plan == [(0, 1)]


def test_plan_caption_reassignment_skips_when_target_already_captioned():
    items = [
        ("banner", 5, 237, 98, "Figure 9. ..."),
        ("real", 5, 1474, 779, "already has caption"),
    ]
    assert plan_caption_reassignment(items) == []


def test_plan_caption_reassignment_no_target_available():
    items = [
        ("banner_only", 5, 237, 98, "Figure 9. ..."),
    ]
    assert plan_caption_reassignment(items) == []


def test_plan_caption_reassignment_pairs_within_page_only():
    # banner on page 5, real on page 6 — different pages, no pairing.
    items = [
        ("banner_p5", 5, 237, 98, "Figure 9. ..."),
        ("real_p6", 6, 1474, 779, ""),
    ]
    assert plan_caption_reassignment(items) == []


def test_plan_caption_reassignment_multiple_pairs_same_page():
    items = [
        ("banner1", 3, 237, 98, "Figure 1. ..."),
        ("real1", 3, 1474, 779, ""),
        ("banner2", 3, 237, 98, "Figure 2. ..."),
        ("real2", 3, 1474, 779, ""),
    ]
    plan = plan_caption_reassignment(items)
    assert plan == [(0, 1), (2, 3)]


def test_plan_caption_reassignment_skips_missing_dims():
    items = [
        ("banner", 3, None, None, "Figure 1. ..."),
        ("real", 3, 1474, 779, ""),
    ]
    assert plan_caption_reassignment(items) == []


def test_plan_caption_reassignment_skips_none_page_source():
    # banner has page=None; real has page=5. A None-page source must
    # never pair with a known-page target.
    items = [
        ("banner", None, 237, 98, "Figure 9. ..."),
        ("real", 5, 1474, 779, ""),
    ]
    assert plan_caption_reassignment(items) == []


def test_plan_caption_reassignment_skips_none_page_target():
    # banner has page=5; real has page=None. A known-page banner must
    # never pair with a None-page target.
    items = [
        ("banner", 5, 237, 98, "Figure 9. ..."),
        ("real", None, 1474, 779, ""),
    ]
    assert plan_caption_reassignment(items) == []


def test_plan_caption_reassignment_two_none_page_items_do_not_pair():
    # Both items have page=None: the previous behaviour bucketed them
    # together under the None key and paired them as if same-page,
    # which is wrong. They must not pair.
    items = [
        ("banner", None, 237, 98, "Figure 9. ..."),
        ("real", None, 1474, 779, ""),
    ]
    assert plan_caption_reassignment(items) == []


def test_reassign_misbound_captions_drops_banner_and_carries_metadata():
    images = [
        RawImage(data=b"x", page=5, width=237, height=98, caption="Figure 9. ...",
                 label="Figure 9", media_type="figure"),
        RawImage(data=b"y", page=5, width=1474, height=779, caption=""),
    ]
    out = _reassign_misbound_captions(images)
    assert len(out) == 1
    assert out[0].caption == "Figure 9. ..."
    assert out[0].label == "Figure 9"
    assert out[0].media_type == "figure"
    assert out[0].width == 1474


def test_reassign_misbound_captions_noop_when_nothing_to_move():
    images = [
        RawImage(data=b"x", page=5, width=1474, height=779, caption="Figure 1. ..."),
        RawImage(data=b"y", page=5, width=1480, height=780, caption="Figure 2. ..."),
    ]
    out = _reassign_misbound_captions(images)
    assert out == images  # same list returned unchanged


def test_reassign_misbound_captions_preserves_order():
    images = [
        RawImage(data=b"a", page=1, width=1474, height=779, caption="Figure 1. ..."),
        RawImage(data=b"b", page=5, width=237, height=98, caption="Figure 5. ..."),
        RawImage(data=b"c", page=5, width=1474, height=779, caption=""),
        RawImage(data=b"d", page=6, width=1474, height=779, caption="Figure 6. ..."),
    ]
    out = _reassign_misbound_captions(images)
    # banner at index 1 should drop; remaining order: a, c (now captioned), d
    assert [im.data for im in out] == [b"a", b"c", b"d"]
    assert out[1].caption == "Figure 5. ..."


# ---------------------------------------------------------------------------
# CLI: reclassify-figure-captions must refresh graph_edges (no stale targets)
# ---------------------------------------------------------------------------


def _write_png(path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (width, height), color=(200, 200, 200)).save(path, "PNG")


def _seed_reclassify_fixture(corpus: Corpus) -> tuple[str, str, str, str]:
    """Build a minimal corpus with a banner-with-caption + real uncaptioned
    asset on the same page, a chunk that mentions ``Fig. 1``, and
    pre-populated graph_edges that point at the banner.

    Returns ``(doc_id, banner_asset_id, real_asset_id, chunk_id)``.
    """
    doc_id = "paper_b"
    banner_id = f"{doc_id}/Figure_01_banner"
    real_id = f"{doc_id}/Figure_01"
    chunk_id = f"{doc_id}__c0000"
    banner_rel = f"images/{doc_id}/Figure_01_banner.png"
    real_rel = f"images/{doc_id}/Figure_01.png"
    _write_png(corpus.root / banner_rel, 237, 98)         # decoration dims
    _write_png(corpus.root / real_rel, 1474, 779)         # real figure dims

    store = Store(corpus.sqlite_path)
    try:
        store.upsert_document(Document(
            id=doc_id, source_path=f"/p/{doc_id}.pdf", kind="pdf",
            title="Banner test", metadata={},
            markdown_path=f"markdown/{doc_id}.md",
            image_dir=f"images/{doc_id}/",
        ))
        store.upsert_chunks([Chunk(
            id=chunk_id, doc_id=doc_id, ord=0,
            text="See Fig. 1 for the device schematic.",
            char_span=(0, 36), section_path=["Body"], section_type="body",
        )])
        store.upsert_assets(doc_id, [
            {"id": banner_id, "type": "figure", "page": 5, "ord": 0,
             "path": banner_rel, "caption": "Figure 1. Real caption text."},
            {"id": real_id, "type": "figure", "page": 5, "ord": 1,
             "path": real_rel, "caption": ""},
        ])
        # Pre-populate the chunk_assets + graph_edges to simulate the
        # pre-reclassification state where the chunk's "Fig. 1" reference
        # was wired to the banner asset.
        store.upsert_chunk_assets(doc_id, [
            {"chunk_id": chunk_id, "asset_id": banner_id, "relation": "near",
             "confidence": 0.9},
        ])
        store.con.commit()
    finally:
        store.close()
    (corpus.markdown_dir / f"{doc_id}.md").write_text(
        "# Banner test\n\nSee Fig. 1.\n", encoding="utf-8",
    )
    corpus.manifest_path.write_text("{}", encoding="utf-8")
    return doc_id, banner_id, real_id, chunk_id


def test_reclassify_figure_captions_refreshes_graph_edges(tmp_path: Path) -> None:
    """After reassignment, graph_edges must not reference the dropped
    banner asset_id; it must reference the real asset_id with the right
    relation. Regression: prior to upsert_asset_edges being called,
    chunk->near->banner and document->has_asset->banner persisted.
    """
    corpus = Corpus(root=tmp_path / "c")
    corpus.ensure()
    doc_id, banner_id, real_id, chunk_id = _seed_reclassify_fixture(corpus)

    # Pre-condition: graph_edges DO reference the banner.
    pre = Store(corpus.sqlite_path)
    try:
        rows = list(pre.con.execute(
            "SELECT src_id, dst_id, kind FROM graph_edges WHERE dst_id=?",
            (banner_id,),
        ))
        assert rows, "fixture must seed banner-pointing edges"
    finally:
        pre.close()

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["corpus", "metrics", "reclassify-figure-captions",
         "--corpus", str(corpus.root)],
    )
    assert result.exit_code == 0, result.output

    post = Store(corpus.sqlite_path)
    try:
        banner_edges = list(post.con.execute(
            "SELECT src_type, src_id, kind, dst_type, dst_id "
            "FROM graph_edges WHERE dst_id=?",
            (banner_id,),
        ))
        real_edges = list(post.con.execute(
            "SELECT src_type, src_id, kind, dst_type, dst_id "
            "FROM graph_edges WHERE dst_id=?",
            (real_id,),
        ))
    finally:
        post.close()

    # No stale targets: the banner is gone from graph_edges entirely.
    assert banner_edges == [], banner_edges
    # And the real asset picked up the chunk->near and document->has_asset.
    relations = {(r[0], r[2]) for r in real_edges}
    assert ("document", "has_asset") in relations
    assert ("chunk", "near") in relations
