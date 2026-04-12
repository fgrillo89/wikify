"""Tests for the per-corpus image index."""

from pathlib import Path

import pytest

from wikify.ingest.images import save_doc_images
from wikify.ingest.parsers.registry import RawImage
from wikify.paths import CorpusPaths
from wikify.store.images_index import (
    ImageIndex,
    build_images_index,
    rebuild_images_index,
)

PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff"
    b"\xff?\x00\x05\xfe\x02\xfeA5\xc8\x91\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def corpus_with_images(tmp_path: Path) -> CorpusPaths:
    corpus = CorpusPaths(tmp_path / "corpus")
    corpus.ensure()
    doc_id = "[2020 Liu] Optimization_ff17142a965a"
    folder = corpus.images_dir / "2020_Liu_Optimization"
    raw = [
        RawImage(data=PNG_1x1, ext="png", page=2, caption="schematic",
                 label="Fig. 1", media_type="figure", width=1, height=1),
        RawImage(data=PNG_1x1, ext="png", page=3, caption="i-v curve",
                 label="Fig. 2", media_type="figure", width=1, height=1),
        RawImage(data=PNG_1x1, ext="png", page=4, caption="loose img",
                 media_type="figure", width=1, height=1),
    ]
    save_doc_images(doc_id, folder, raw)
    # Second doc to verify multi-doc dispatch
    doc_id2 = "[2018 Yang] Crossbar_abcd"
    folder2 = corpus.images_dir / "2018_Yang_Crossbar"
    save_doc_images(
        doc_id2,
        folder2,
        [RawImage(data=PNG_1x1, ext="png", page=1, caption="table",
                  label="Table 1", media_type="table", width=1, height=1)],
    )
    return corpus


def test_build_index_round_trip(corpus_with_images: CorpusPaths) -> None:
    idx = build_images_index(corpus_with_images, doc_ids=[])
    assert len(idx.by_doc) == 2
    # Persisted file exists and reloads to the same shape.
    assert corpus_with_images.images_index_path.exists()
    reloaded = ImageIndex.load(corpus_with_images)
    assert set(reloaded.by_doc.keys()) == set(idx.by_doc.keys())
    assert sum(len(v) for v in reloaded.by_doc.values()) == 4


def test_resolve_caption_aliases(corpus_with_images: CorpusPaths) -> None:
    idx = build_images_index(corpus_with_images, doc_ids=[])
    liu = next(k for k in idx.by_doc if "Liu" in k)
    for ref in ("Figure 1", "fig 1", "Figure_01", "figure_01", "Fig. 1", "fig.1"):
        hit = idx.resolve(liu, ref)
        assert hit is not None, f"{ref!r} did not resolve"
        assert hit.id.endswith("/Figure_01"), f"{ref!r} resolved to {hit.id}"


def test_resolve_table_label(corpus_with_images: CorpusPaths) -> None:
    idx = build_images_index(corpus_with_images, doc_ids=[])
    yang = next(k for k in idx.by_doc if "Yang" in k)
    hit = idx.resolve(yang, "Table 1")
    assert hit is not None
    assert hit.id.endswith("/Table_01")


def test_resolve_unmatched_returns_none(corpus_with_images: CorpusPaths) -> None:
    idx = build_images_index(corpus_with_images, doc_ids=[])
    liu = next(k for k in idx.by_doc if "Liu" in k)
    assert idx.resolve(liu, "Figure 99") is None
    assert idx.resolve("nonexistent_doc", "Figure 1") is None


def test_paths_are_relative_to_corpus_root(corpus_with_images: CorpusPaths) -> None:
    idx = build_images_index(corpus_with_images, doc_ids=[])
    for recs in idx.by_doc.values():
        for r in recs:
            assert not Path(r.path).is_absolute(), r.path
            assert (corpus_with_images.root / r.path).exists()
            assert (corpus_with_images.root / r.sidecar).exists()


def test_rebuild_from_sidecars_only(corpus_with_images: CorpusPaths) -> None:
    build_images_index(corpus_with_images, doc_ids=[])
    corpus_with_images.images_index_path.unlink()
    rebuilt = rebuild_images_index(corpus_with_images)
    assert len(rebuilt.by_doc) == 2
    assert corpus_with_images.images_index_path.exists()
