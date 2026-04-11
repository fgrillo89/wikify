"""Verify ``build_write_request`` ranks page_figures by ``near_chunk_ids`` overlap.

The writer should see figures most relevant to the page's evidence
chunks first. Decorative figures (no near_chunk_ids) sink to the bottom,
and the list is capped to keep the prompt focused.
"""

from pathlib import Path

from wikify_simple.distill.extract.dossier import DossierStore
from wikify_simple.distill.write.requests import (
    WriteRequestConfig,
    build_write_request,
)
from wikify_simple.models import Evidence, WikiPage
from wikify_simple.store.images_index import ImageIndex, ImageRecord


def _cfg() -> WriteRequestConfig:
    return WriteRequestConfig(
        model_id="haiku",
        writer_tier="S",
        prompt_name="wikify_simple/write",
        style_text="",
        field_text="",
        artifact_text="",
        person_artifact_text="",
        persona_text="",
    )


def _make_page(doc_id: str, chunk_ids: list[str]) -> WikiPage:
    return WikiPage(
        id="TestPage",
        kind="article",
        title="Test Page",
        aliases=[],
        body_markdown="",
        evidence=[
            Evidence(marker=f"e{i}", chunk_id=cid, doc_id=doc_id, quote=f"q{i}")
            for i, cid in enumerate(chunk_ids)
        ],
    )


def _make_image(
    doc_id: str,
    stem: str,
    near_chunk_ids: tuple[str, ...] = (),
    label: str | None = None,
) -> ImageRecord:
    return ImageRecord(
        id=f"{doc_id}/{stem}",
        label=label,
        caption=f"caption for {stem}",
        alt_text="",
        page=None,
        path=f"images/{doc_id}/{stem}.png",
        sidecar=f"images/{doc_id}/{stem}.png.json",
        media_type="figure",
        width=800,
        height=600,
        near_chunk_ids=near_chunk_ids,
    )


def test_figures_ranked_by_near_chunk_overlap(tmp_path: Path):
    doc_id = "doc1"
    cited_chunks = ["doc1__c0", "doc1__c1", "doc1__c2"]
    page = _make_page(doc_id, cited_chunks)

    # Three figures: A overlaps 2 cited chunks, B overlaps 1, C overlaps 0.
    img_a = _make_image(doc_id, "Figure_03", near_chunk_ids=("doc1__c0", "doc1__c1"))
    img_b = _make_image(doc_id, "Figure_05", near_chunk_ids=("doc1__c2",))
    img_c = _make_image(doc_id, "Figure_07", near_chunk_ids=())

    # Pre-build a tiny ImageIndex with just these records.
    index = ImageIndex(corpus_root=tmp_path)
    index.by_doc[doc_id] = [img_c, img_a, img_b]  # input order != desired

    req = build_write_request(
        page=page,
        all_pages=[page],
        briefs={},
        dossier_store=DossierStore(tmp_path),
        chunks_by_id={},
        images_index=index,
        cfg=_cfg(),
    )

    # Top-ranked figure is the highest-overlap one (img_a).
    assert req.figures[0].id == img_a.id
    # Then img_b (1 overlap), then img_c (0 overlap, decorative).
    assert req.figures[1].id == img_b.id
    assert req.figures[2].id == img_c.id


def test_figures_capped_at_top_k(tmp_path: Path):
    doc_id = "doc1"
    cited_chunks = ["doc1__c0"]
    page = _make_page(doc_id, cited_chunks)

    # 12 figures, all decorative (no near_chunk_ids), should cap at 8.
    images = [_make_image(doc_id, f"Figure_{i:02d}") for i in range(12)]
    index = ImageIndex(corpus_root=tmp_path)
    index.by_doc[doc_id] = images

    req = build_write_request(
        page=page,
        all_pages=[page],
        briefs={},
        dossier_store=DossierStore(tmp_path),
        chunks_by_id={},
        images_index=index,
        cfg=_cfg(),
    )

    assert len(req.figures) == 8


def test_figures_with_near_chunks_beat_decorative(tmp_path: Path):
    doc_id = "doc1"
    page = _make_page(doc_id, ["doc1__cunknown"])

    # img_with_near has near_chunk_ids but the cited chunk isn't in them
    # (overlap = 0). img_decorative has no near_chunk_ids at all.
    # Both have overlap=0; the one with ANY near_chunk_ids should still
    # rank higher because it's a non-decorative figure.
    img_with_near = _make_image(doc_id, "Figure_01", near_chunk_ids=("doc1__cother",))
    img_decorative = _make_image(doc_id, "Figure_02", near_chunk_ids=())

    index = ImageIndex(corpus_root=tmp_path)
    index.by_doc[doc_id] = [img_decorative, img_with_near]

    req = build_write_request(
        page=page,
        all_pages=[page],
        briefs={},
        dossier_store=DossierStore(tmp_path),
        chunks_by_id={},
        images_index=index,
        cfg=_cfg(),
    )

    # img_with_near must come first (has near_chunk_ids; decorative does not).
    assert req.figures[0].id == img_with_near.id
    assert req.figures[1].id == img_decorative.id


def test_propagates_near_chunk_ids_to_imageref(tmp_path: Path):
    doc_id = "doc1"
    page = _make_page(doc_id, ["doc1__c0"])
    img = _make_image(doc_id, "Figure_01", near_chunk_ids=("doc1__c0", "doc1__c2"))
    index = ImageIndex(corpus_root=tmp_path)
    index.by_doc[doc_id] = [img]

    req = build_write_request(
        page=page,
        all_pages=[page],
        briefs={},
        dossier_store=DossierStore(tmp_path),
        chunks_by_id={},
        images_index=index,
        cfg=_cfg(),
    )

    assert req.figures
    assert list(req.figures[0].near_chunk_ids) == ["doc1__c0", "doc1__c2"]


def test_figures_only_from_cited_docs(tmp_path: Path):
    """Page only cites doc1; doc2 figures must NOT appear in page_figures."""
    page = _make_page("doc1", ["doc1__c0"])
    img1 = _make_image("doc1", "Figure_01")
    img2 = _make_image("doc2", "Figure_01")  # different doc, irrelevant

    index = ImageIndex(corpus_root=tmp_path)
    index.by_doc["doc1"] = [img1]
    index.by_doc["doc2"] = [img2]

    req = build_write_request(
        page=page,
        all_pages=[page],
        briefs={},
        dossier_store=DossierStore(tmp_path),
        chunks_by_id={},
        images_index=index,
        cfg=_cfg(),
    )

    assert len(req.figures) == 1
    assert req.figures[0].id == img1.id
