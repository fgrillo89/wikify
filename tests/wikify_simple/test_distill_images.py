"""Distill pipeline consumes ImageIndex when present."""

from __future__ import annotations

from pathlib import Path

from wikify_simple.agents.protocols import Extractor, Writer
from wikify_simple.agents.schema import (
    ExtractRequest,
    ExtractResponse,
    WriteRequest,
    WriteResponse,
)
from wikify_simple.bindings.fake import FakeExtractor, FakeWriter
from wikify_simple.distill.pipeline import run as pipeline_run
from wikify_simple.distill.strategies import STRATEGIES
from wikify_simple.infra.cache import ExtractCache
from wikify_simple.infra.cost_meter import CostMeter
from wikify_simple.ingest.refresh import ingest_corpus
from wikify_simple.paths import BundlePaths
from wikify_simple.store.images_index import ImageIndex, ImageRecord, save_images_index

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "tiny"


class _RecordingExtractor(Extractor):
    """Wrap FakeExtractor; remember the requests it saw."""

    def __init__(self, inner: FakeExtractor) -> None:
        self._inner = inner
        self.seen: list[ExtractRequest] = []

    def extract(self, request: ExtractRequest) -> ExtractResponse:
        self.seen.append(request)
        return self._inner.extract(request)


class _RecordingWriter(Writer):
    def __init__(self, inner: FakeWriter) -> None:
        self._inner = inner
        self.seen: list[WriteRequest] = []

    def write(self, request: WriteRequest) -> WriteResponse:
        self.seen.append(request)
        return self._inner.write(request)


def _run_once(tmp_path, *, inject_image: bool):
    corpus = ingest_corpus(FIXTURE, tmp_path / "corpus")

    if inject_image:
        # Hand-build a synthetic ImageIndex covering the first doc, and
        # save it over the auto-built one.
        from wikify_simple.store.corpus import list_documents

        docs = list_documents(corpus)
        assert docs, "fixture should have at least one doc"
        by_doc: dict[str, list[ImageRecord]] = {}
        by_alias: dict[str, str] = {}
        for d in docs:
            rec = ImageRecord(
                id=f"{d.id}/Figure_01",
                label="Figure 1",
                caption="synthetic figure for tests",
                alt_text="",
                page=1,
                path=f"images/{d.id}/Figure_01.png",
                sidecar=f"images/{d.id}/Figure_01.png.json",
                media_type="figure",
                width=100,
                height=100,
            )
            by_doc[d.id] = [rec]
            by_alias[f"{d.id}/figure_1"] = rec.id
            by_alias[f"{d.id}/figure_01"] = rec.id
        idx = ImageIndex(corpus_root=corpus.root, by_doc=by_doc, by_alias=by_alias)
        save_images_index(corpus, idx)

    bundle = BundlePaths(root=tmp_path / "bundle")
    cache = ExtractCache(root=tmp_path / "cache")
    meter = CostMeter(
        budget_haiku_eq=20_000.0,
        run_id="M_1x_seed0",
        events_path=bundle.calls_path,
    )
    cfg = STRATEGIES["M"](seed=0)
    extractor = _RecordingExtractor(FakeExtractor(cache, meter))
    writer = _RecordingWriter(FakeWriter(meter))
    pipeline_run(
        corpus=corpus,
        bundle=bundle,
        strategy=cfg,
        extractor=extractor,
        writer=writer,
        meter=meter,
        budget_haiku_eq=20_000.0,
    )
    return extractor, writer


def test_fake_pipeline_still_works_without_images(tmp_path):
    extractor, writer = _run_once(tmp_path, inject_image=False)
    assert extractor.seen, "extractor should have been called"
    assert writer.seen, "writer should have been called"
    # Default empty figure list, no crash
    assert all(req.figures == [] or isinstance(req.figures, list) for req in writer.seen)


def test_pipeline_passes_figures_when_index_has_them(tmp_path):
    extractor, writer = _run_once(tmp_path, inject_image=True)
    # At least one extract request should have images_for_doc populated
    assert any(req.images_for_doc for req in extractor.seen), (
        "extractor never saw images_for_doc despite injected ImageIndex"
    )
    # At least one write request should have figures populated
    assert any(req.figures for req in writer.seen), (
        "writer never saw figures despite injected ImageIndex"
    )

    # Verify the figure id round-trips through the page's evidence doc set
    found_synthetic = False
    for req in writer.seen:
        for fig in req.figures:
            if fig.label == "Figure 1" and fig.caption == "synthetic figure for tests":
                found_synthetic = True
    assert found_synthetic
