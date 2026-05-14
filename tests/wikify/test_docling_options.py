"""Tests for ``wikify.ingest.parsers.docling`` option/retry surface.

GPU-free: every test mocks ``_gpu_batch_size_default`` and the
converter factory so the suite runs on CI machines with no CUDA.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from wikify.ingest.parsers import docling
from wikify.ingest.parsers.docling import DoclingOptions


def test_from_env_uses_adaptive_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCLING_LAYOUT_BATCH_SIZE", raising=False)
    monkeypatch.delenv("DOCLING_OCR_BATCH_SIZE", raising=False)
    monkeypatch.setattr(docling, "_gpu_batch_size_default", lambda: 16)
    o = DoclingOptions.from_env()
    assert o.layout_batch_size == 16
    assert o.ocr_batch_size == 16


def test_from_env_explicit_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCLING_LAYOUT_BATCH_SIZE", "32")
    monkeypatch.setenv("DOCLING_OCR_BATCH_SIZE", "64")
    monkeypatch.setattr(docling, "_gpu_batch_size_default", lambda: 8)
    o = DoclingOptions.from_env()
    assert o.layout_batch_size == 32
    assert o.ocr_batch_size == 64


def test_from_env_empty_string_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCLING_LAYOUT_BATCH_SIZE", "")
    monkeypatch.delenv("DOCLING_OCR_BATCH_SIZE", raising=False)
    monkeypatch.setattr(docling, "_gpu_batch_size_default", lambda: 8)
    o = DoclingOptions.from_env()
    assert o.layout_batch_size == 8
    assert o.ocr_batch_size == 8


def test_next_lower_batch_walks_ladder() -> None:
    assert docling._next_lower_batch(64) == 32
    assert docling._next_lower_batch(32) == 16
    assert docling._next_lower_batch(16) == 8
    assert docling._next_lower_batch(8) == 4
    assert docling._next_lower_batch(4) is None


def test_step_down_batches_lowers_each_knob_independently() -> None:
    """Asymmetric inputs (layout=64, ocr=4) must step layout down to 32
    and leave ocr at 4 — never raise either knob during fallback."""
    opts = DoclingOptions(
        layout_batch_size=64, ocr_batch_size=4, table_batch_size=4,
    )
    stepped = docling._step_down_batches(opts)
    assert stepped is not None
    assert stepped.layout_batch_size == 32
    assert stepped.ocr_batch_size == 4
    # Original is unchanged (defensive copy).
    assert opts.layout_batch_size == 64
    assert opts.ocr_batch_size == 4


def test_step_down_batches_returns_none_at_floor() -> None:
    """Both knobs at floor (4) means no further step is possible."""
    opts = DoclingOptions(
        layout_batch_size=4, ocr_batch_size=4, table_batch_size=4,
    )
    assert docling._step_down_batches(opts) is None


def test_step_down_batches_one_at_floor() -> None:
    """Layout at floor + ocr higher: step ocr down only."""
    opts = DoclingOptions(
        layout_batch_size=4, ocr_batch_size=32, table_batch_size=4,
    )
    stepped = docling._step_down_batches(opts)
    assert stepped is not None
    assert stepped.layout_batch_size == 4
    assert stepped.ocr_batch_size == 16


def test_step_down_batches_preserves_quality_knobs() -> None:
    """formulas/ocr/pic_describe/vlm/images_scale must never be touched
    during a batch-only retry — quality is non-negotiable."""
    opts = DoclingOptions(
        formulas=True, ocr=True, pic_classify=True, pic_describe=True,
        vlm=False, images_scale=3.0,
        layout_batch_size=64, ocr_batch_size=64, table_batch_size=4,
    )
    stepped = docling._step_down_batches(opts)
    assert stepped is not None
    assert stepped.formulas is True
    assert stepped.ocr is True
    assert stepped.pic_classify is True
    assert stepped.pic_describe is True
    assert stepped.vlm is False
    assert stepped.images_scale == 3.0
    assert stepped.table_batch_size == 4


def test_clear_converter_cache_resets_globals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_clear_converter_cache`` drops the cached converter + key so
    the next ``_get_converter`` call rebuilds. Without the clear, an
    OOM retry would briefly co-reside two converter copies."""
    monkeypatch.setattr(docling, "_CACHED_CONVERTER", "sentinel")
    monkeypatch.setattr(docling, "_CACHED_OPTS_KEY", ("k",))
    docling._clear_converter_cache()
    assert docling._CACHED_CONVERTER is None
    assert docling._CACHED_OPTS_KEY is None


def test_is_cuda_oom_recognises_messages() -> None:
    assert docling._is_cuda_oom(RuntimeError("CUDA out of memory"))
    assert docling._is_cuda_oom(RuntimeError("CUBLAS out of memory"))
    assert not docling._is_cuda_oom(RuntimeError("Some CUDA error"))
    assert not docling._is_cuda_oom(RuntimeError("host out of memory"))
    assert not docling._is_cuda_oom(RuntimeError("file not found"))


def test_is_cuda_oom_recognises_torch_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCudaOOMError(RuntimeError):
        pass

    fake_torch = SimpleNamespace(
        cuda=SimpleNamespace(OutOfMemoryError=FakeCudaOOMError),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    assert docling._is_cuda_oom(FakeCudaOOMError("driver-specific wording"))


class _FakeConverter:
    """Test stub: ``convert`` raises CUDA OOM until ``batch_at_build``
    is at or below ``succeed_at``.
    """

    def __init__(self, batch_at_build: int, succeed_at: int) -> None:
        self.batch_at_build = batch_at_build
        self.succeed_at = succeed_at

    def convert(self, _path: str) -> SimpleNamespace:
        if self.batch_at_build > self.succeed_at:
            raise RuntimeError("CUDA out of memory")
        return SimpleNamespace(document=SimpleNamespace())


def test_oom_retry_lowers_batch_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry must drop batch size; never touch quality knobs."""

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    opts = DoclingOptions(
        formulas=True,
        ocr=True,
        ocr_auto=False,
        pic_classify=True,
        pic_describe=True,
        vlm=False,
        images_scale=3.0,
        layout_batch_size=64,
        ocr_batch_size=64,
        table_batch_size=4,
    )

    captured_opts: list[DoclingOptions] = []

    def fake_get_converter(o: DoclingOptions):
        captured_opts.append(o)
        return _FakeConverter(
            batch_at_build=o.layout_batch_size, succeed_at=16,
        )

    monkeypatch.setattr(docling, "_get_converter", fake_get_converter)

    result, effective = docling._convert_with_oom_retry(opts, pdf)

    assert result is not None
    assert effective.layout_batch_size == 16
    assert effective.ocr_batch_size == 16
    for o in captured_opts:
        assert o.formulas is True
        assert o.ocr is True
        assert o.pic_classify is True
        assert o.pic_describe is True
        assert o.vlm is False
        assert o.images_scale == 3.0
    assert [o.layout_batch_size for o in captured_opts] == [64, 32, 16]


def test_oom_retry_raises_at_minimum_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If even batch=4 OOMs, raise loudly — do NOT degrade quality."""

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    opts = DoclingOptions(
        formulas=True, ocr=True, pic_describe=True,
        layout_batch_size=64, ocr_batch_size=64, table_batch_size=4,
    )

    captured_opts: list[DoclingOptions] = []

    def always_oom(o: DoclingOptions):
        captured_opts.append(o)
        # succeed_at=0 ensures convert() always OOMs
        return _FakeConverter(batch_at_build=o.layout_batch_size, succeed_at=0)

    monkeypatch.setattr(docling, "_get_converter", always_oom)

    with pytest.raises(
        RuntimeError,
        match=r"layout=4.*ocr=4.*more VRAM",
    ):
        docling._convert_with_oom_retry(opts, pdf)

    assert [o.layout_batch_size for o in captured_opts] == [64, 32, 16, 8, 4]
    for o in captured_opts:
        assert o.formulas is True
        assert o.ocr is True
        assert o.pic_describe is True


def test_non_oom_runtime_error_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-OOM RuntimeErrors must NOT trigger the retry ladder."""

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    calls: list[int] = []

    class _PermDenied:
        def convert(self, _path: str) -> SimpleNamespace:
            raise RuntimeError("permission denied")

    def fake_get_converter(o: DoclingOptions):
        calls.append(o.layout_batch_size)
        return _PermDenied()

    monkeypatch.setattr(docling, "_get_converter", fake_get_converter)

    with pytest.raises(RuntimeError, match="permission denied"):
        docling._convert_with_oom_retry(DoclingOptions(), pdf)
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# PDF backend selection (root-cause fix A1)
# ---------------------------------------------------------------------------


def test_pdf_backend_is_pypdfium() -> None:
    """``_make_document_converter`` must wire the pypdfium2 text backend
    for the PDF input format. The default ``DoclingParseDocumentBackend``
    inserts spaces inside ligature glyphs (``ﬁ``, ``ﬂ``, ``ff``) when the
    PDF lacks a ToUnicode CMap, producing ``arti fi cial`` /
    ``di ff usion`` artefacts in body text. pypdfium2 reads the CMap and
    reconstructs ligatures correctly.
    """
    from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
    from docling.datamodel.base_models import InputFormat

    conv = docling._make_document_converter(DoclingOptions())
    pdf_opt = conv.format_to_options[InputFormat.PDF]
    assert pdf_opt.backend is PyPdfiumDocumentBackend


# ---------------------------------------------------------------------------
# _light_clean: html-entity unescaping + page-number drop (A2, A3)
# ---------------------------------------------------------------------------


def test_light_clean_unescapes_html_entities() -> None:
    """Docling's ``export_to_markdown`` escapes ``<``, ``>``, ``&`` so
    its output stays valid as inline HTML. Downstream renderers expect
    literal characters (``<10 nm``, ``R & D``); we unescape once at
    parser boundary."""
    src = "We see <10 nm thick layers with >95% yield. R &amp; D Center.\n"
    out = docling._light_clean(src)
    assert "<10 nm" in out
    assert ">95%" in out
    assert "R & D Center" in out
    assert "&lt;" not in out
    assert "&gt;" not in out
    assert "&amp;" not in out


def test_light_clean_preserves_isolated_digit_lines() -> None:
    """Isolated single-digit lines are NOT dropped — they might be
    table cells, footnote anchors, or numbered-list markers without
    punctuation. Only CLUSTERS (>=2 consecutive digit-only lines, the
    page-margin column pattern in 2-col PDFs) get erased. The user
    prefers information preservation over surface cleanliness."""
    src = "First paragraph of body text.\n\n42\n\nSecond paragraph follows.\n"
    out = docling._light_clean(src)
    assert "First paragraph" in out
    assert "Second paragraph" in out
    # The isolated ``42`` survives — it might be a table cell.
    assert "42" in [ln.strip() for ln in out.splitlines() if ln.strip()]


def test_light_clean_keeps_inline_numbers() -> None:
    """The digit-cluster drop must NOT remove inline numbers."""
    src = "The device retained 42 states after 1000 cycles.\n"
    out = docling._light_clean(src)
    assert "42 states" in out


def test_light_clean_keeps_numbered_list_items() -> None:
    """Numbered list markers carry punctuation (``1.`` / ``1)``) so they
    survive — the regex only matches pure integer lines."""
    src = "Steps:\n\n1.\nFirst step.\n\n2.\nSecond step.\n"
    out = docling._light_clean(src)
    assert "1." in out
    assert "2." in out


def test_light_clean_drops_page_column_run() -> None:
    """The 2-column-PDF failure mode: vertical page-number column
    routed as a contiguous RUN of standalone digits. All in the run
    must be dropped."""
    src = (
        "First paragraph.\n\n"
        "6\n9\n9\n8\n"
        "Second paragraph.\n"
    )
    out = docling._light_clean(src)
    body_lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    for d in ("6", "9", "8"):
        assert d not in body_lines, (d, body_lines)
    assert "First paragraph." in out
    assert "Second paragraph." in out


def test_light_clean_preserves_footnote_anchor() -> None:
    """A single footnote-anchor digit line on its own (NOT part of a
    margin-column cluster) is preserved. The old behaviour erased
    this; the cluster-only rule fixes it."""
    src = "Paragraph end.\n\n3\n\nNext paragraph.\n"
    out = docling._light_clean(src)
    assert "3" in [ln.strip() for ln in out.splitlines() if ln.strip()]


def test_light_clean_preserves_small_data_cluster() -> None:
    """Threshold raised from >=2 to >=4: a 3-row run of digit-only
    lines is preserved because it's much more likely a small table
    column or a pair of equation-number-only blocks than a page-margin
    column. Real page margins are virtually always >=4 (most papers
    have >=4 pages); the false-positive risk of >=2 outweighed the
    cleanup gain."""
    src = (
        "Steady-state currents:\n\n"
        "100\n200\n50\n"
        "Next paragraph.\n"
    )
    out = docling._light_clean(src)
    body_lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    for d in ("100", "200", "50"):
        assert d in body_lines, (d, body_lines)


def test_light_clean_preserves_two_digit_run() -> None:
    """A pair of digit-only lines (e.g. equation-number-only blocks)
    is preserved under the >=4 threshold."""
    src = "End of derivation.\n\n3\n4\nNext paragraph.\n"
    out = docling._light_clean(src)
    body_lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    assert "3" in body_lines and "4" in body_lines


# ---------------------------------------------------------------------------
# Picture-extraction filter: drop publisher decorations + thin banners
# ---------------------------------------------------------------------------


def _png_bytes(width: int, height: int) -> bytes:
    """Encode an opaque-white PNG of the requested size for testing."""
    import io as _io

    from PIL import Image as PilImage

    img = PilImage.new("RGB", (width, height), color="white")
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class _FakeProv:
    def __init__(self, page: int) -> None:
        self.page_no = page


class _FakePictureItem:
    """Just enough surface for ``_picture_to_raw_image``."""

    def __init__(
        self,
        *,
        width: int,
        height: int,
        page: int = 1,
        caption: str = "",
        image_bytes_override: bytes | None = None,
    ) -> None:
        self._caption = caption
        self.prov = [_FakeProv(page)]
        self.image = SimpleNamespace(
            pil_image=None,
            uri=f"data:image/png;base64,{_b64(image_bytes_override or _png_bytes(width, height))}",
        )

    def caption_text(self, _doc) -> str:
        return self._caption


def _b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode("ascii")


def test_picture_filter_drops_thin_uncaptioned_banner() -> None:
    """Crossmark badge / journal-logo banner: wide, short, no caption.
    Old filter (BOTH dims <150) let it through because width passed.
    New filter (min dim <150 AND no caption) drops it.
    """
    item = _FakePictureItem(width=600, height=80, page=1, caption="")
    out = docling._picture_to_raw_image(item, doc=None)
    assert out is None


def test_picture_filter_keeps_banner_with_caption() -> None:
    """A captioned wide figure (e.g. a panel-strip with sub-figures) must
    survive — Docling's caption-text presence is a strong real-figure
    signal."""
    item = _FakePictureItem(
        width=800, height=120, page=3,
        caption="Fig. 4. I-V characteristics under DC sweep.",
    )
    out = docling._picture_to_raw_image(item, doc=None)
    assert out is not None
    assert out.caption.startswith("Fig. 4")


def test_picture_filter_drops_extreme_aspect_on_page_1() -> None:
    """A 10:1 thin strip on page 1 with no caption is almost always a
    decorative banner / header rule. Drop it."""
    item = _FakePictureItem(width=900, height=70, page=1, caption="")
    out = docling._picture_to_raw_image(item, doc=None)
    assert out is None


def test_picture_filter_keeps_real_figure() -> None:
    """A normal scientific figure (square-ish, captioned, mid-document)
    must pass cleanly."""
    item = _FakePictureItem(
        width=800, height=600, page=4,
        caption="Fig. 2. Device structure.",
    )
    out = docling._picture_to_raw_image(item, doc=None)
    assert out is not None


def test_picture_filter_drops_tiny_uncaptioned() -> None:
    """The original both-dims-under-150 rule must still fire for tiny
    icons / decorations."""
    item = _FakePictureItem(width=80, height=80, page=1, caption="")
    out = docling._picture_to_raw_image(item, doc=None)
    assert out is None
