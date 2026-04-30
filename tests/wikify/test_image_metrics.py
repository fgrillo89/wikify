"""Tests for image coverage and figure reference metrics."""

import math
from pathlib import Path

import numpy as np
import pytest

from wikify.bundle.wiki.page import Bundle, Page
from wikify.eval.metrics import figure_reference_counts, image_coverage_residual


def _make_bundle(body_texts: list[str], run_meta: dict | None = None) -> Bundle:
    pages = [
        Page(
            id=f"p{i}",
            kind="article",
            title=f"Page {i}",
            aliases=[],
            links=[],
            body_clean=body,
            evidence=[],
            path=Path(f"p{i}.md"),
        )
        for i, body in enumerate(body_texts)
    ]
    return Bundle(name="test", root=Path("."), pages=pages, run_meta=run_meta or {})


def _embed(texts: list[str]) -> np.ndarray:
    """Deterministic fake embedder: embed by first char index."""
    dim = 4
    out = np.zeros((len(texts), dim), dtype=np.float32)
    for i, t in enumerate(texts):
        v = np.ones(dim, dtype=np.float32) * (ord(t[0]) if t else 1.0)
        n = np.linalg.norm(v)
        out[i] = v / n if n > 0 else v
    return out


# ---- image_coverage_residual --------------------------------------------


def test_image_coverage_residual_returns_float_in_range(monkeypatch):
    bundle = _make_bundle(["alpha text", "beta text"])
    cap_embeds = _embed(["alpha caption", "beta figure"])

    def fake_load_or_compute(_bundle, pages, _embed_fn):
        return [p.id for p in pages], _embed([p.body_clean for p in pages])

    monkeypatch.setattr(
        "wikify.bundle.wiki.embeddings.load_or_compute",
        fake_load_or_compute,
    )
    val = image_coverage_residual(bundle, cap_embeds, _embed)
    assert isinstance(val, float)
    assert 0.0 <= val <= 1.0


def test_image_coverage_residual_no_pages():
    bundle = Bundle(name="test", root=Path("."), pages=[])
    cap_embeds = np.zeros((3, 4), dtype=np.float32)
    val = image_coverage_residual(bundle, cap_embeds, _embed)
    assert val == 1.0


def test_image_coverage_residual_no_captions(monkeypatch):
    bundle = _make_bundle(["alpha text"])

    def fake_load_or_compute(_bundle, pages, _embed_fn):
        return [p.id for p in pages], _embed([p.body_clean for p in pages])

    monkeypatch.setattr(
        "wikify.bundle.wiki.embeddings.load_or_compute",
        fake_load_or_compute,
    )
    val = image_coverage_residual(bundle, np.empty((0, 4), dtype=np.float32), _embed)
    assert val == 1.0


def test_image_coverage_residual_non_negative(monkeypatch):
    """Regression: result must not be negative due to float underflow."""
    bundle = _make_bundle(["alpha"])
    dim = 4
    vec = np.ones((1, dim), dtype=np.float32)
    vec /= np.linalg.norm(vec, axis=1, keepdims=True)

    def fake_load_or_compute(_bundle, pages, _embed_fn):
        return [p.id for p in pages], vec

    monkeypatch.setattr(
        "wikify.bundle.wiki.embeddings.load_or_compute",
        fake_load_or_compute,
    )
    val = image_coverage_residual(bundle, vec, lambda xs: vec)
    assert val >= 0.0


# ---- figure_reference_counts --------------------------------------------


def test_figure_reference_counts_detects_embeds():
    body = "Some text.\n![Figure 1](path/to/fig.png)\nMore text.\n![Figure 2](other.png)"
    bundle = _make_bundle([body], run_meta={"n_caption_chunks": 5})
    result = figure_reference_counts(bundle)
    assert result["n_figures_referenced_in_bodies"] == 2
    assert result["n_total_captions"] == 5
    assert not math.isnan(result["figure_reference_rate"])
    assert result["figure_reference_rate"] == pytest.approx(2 / 5)


def test_figure_reference_counts_no_figures():
    bundle = _make_bundle(["plain text with no figure embeds"])
    result = figure_reference_counts(bundle)
    assert result["n_figures_referenced_in_bodies"] == 0
    assert "n_total_captions" in result
    assert "figure_reference_rate" in result


def test_figure_reference_counts_nan_when_no_captions():
    bundle = _make_bundle(["text"], run_meta={"n_caption_chunks": 0})
    result = figure_reference_counts(bundle)
    assert math.isnan(result["figure_reference_rate"])


def test_figure_reference_counts_keys():
    bundle = _make_bundle([])
    result = figure_reference_counts(bundle)
    expected_keys = {"n_figures_referenced_in_bodies", "n_total_captions", "figure_reference_rate"}
    assert set(result.keys()) == expected_keys
