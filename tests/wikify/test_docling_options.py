"""Tests for ``wikify.ingest.parsers.docling`` option/retry surface.

GPU-free: every test mocks ``_gpu_batch_size_default`` and the
converter factory so the suite runs on CI machines with no CUDA.
"""

from __future__ import annotations

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
    assert docling._is_cuda_oom(RuntimeError("Some CUDA error"))
    assert not docling._is_cuda_oom(RuntimeError("file not found"))


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

    with pytest.raises(RuntimeError, match=r"layout=4.*ocr=4"):
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
