"""Tests for the GPU-fallback health check in embedding._load_fe.

DirectML (and CUDA to a lesser extent) can silently route ops to CPU
when VRAM is exhausted. The health check times a 1-token inference
right after model load and raises if it exceeds a generous threshold,
preventing the 4–12 h silent-hang pattern we hit on ald_references.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest


def _fake_model(inference_delay: float):
    """Build a stand-in for ``_fe_model`` with configurable embed latency.

    Yields a single NumPy-ish vector after sleeping for
    ``inference_delay`` seconds, mimicking slow CPU fallback.
    """
    import numpy as np

    def embed(texts, batch_size=1):
        time.sleep(inference_delay)
        for _ in texts:
            yield np.zeros(384, dtype=np.float32)

    inner_session = SimpleNamespace(
        get_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"],
    )
    outer = SimpleNamespace(model=SimpleNamespace(model=inner_session))
    outer.embed = embed
    return outer


def _reset_embedder_module():
    from wikify import embedding

    embedding._fe_model = None
    embedding._fe_model_id = None


def test_health_check_raises_when_inference_is_slow(monkeypatch):
    """When the probe takes > _HEALTH_CHECK_SLOW_S on a GPU-requested
    session, _load_fe must raise RuntimeError with a remediation hint."""
    from wikify import embedding

    _reset_embedder_module()
    monkeypatch.setattr(
        embedding, "_onnx_providers",
        lambda: ["DmlExecutionProvider", "CPUExecutionProvider"],
    )
    # 3 s > 2 s threshold → should raise.
    fake = _fake_model(inference_delay=3.0)
    with patch.object(embedding, "TextEmbedding", return_value=fake, create=True):
        # TextEmbedding is imported inside the function — patch the local name.
        with patch("fastembed.TextEmbedding", return_value=fake):
            with pytest.raises(RuntimeError, match="fallen back to CPU"):
                embedding._load_fe("jinaai/jina-embeddings-v2-small-en")


def test_health_check_passes_when_inference_is_fast(monkeypatch):
    """A quick probe must not raise; model stays loaded."""
    from wikify import embedding

    _reset_embedder_module()
    monkeypatch.setattr(
        embedding, "_onnx_providers",
        lambda: ["DmlExecutionProvider", "CPUExecutionProvider"],
    )
    # 50 ms — well under 2 s threshold.
    fake = _fake_model(inference_delay=0.05)
    with patch("fastembed.TextEmbedding", return_value=fake):
        embedding._load_fe("jinaai/jina-embeddings-v2-small-en")
    assert embedding._fe_model is fake


def test_health_check_skipped_on_cpu_only_providers(monkeypatch):
    """If only CPU is available, the health check is a no-op — the user
    is intentionally on CPU and we shouldn't abort."""
    from wikify import embedding

    _reset_embedder_module()
    monkeypatch.setattr(
        embedding, "_onnx_providers", lambda: ["CPUExecutionProvider"],
    )
    # Slow but OK — we're not supposed to check.
    fake = _fake_model(inference_delay=5.0)
    with patch("fastembed.TextEmbedding", return_value=fake):
        embedding._load_fe("sentence-transformers/all-MiniLM-L6-v2")
    assert embedding._fe_model is fake


def test_health_check_skipped_when_providers_is_none(monkeypatch):
    """``_onnx_providers()`` returns None when no GPU is present at all —
    the health check has nothing to validate and must stay out of the way."""
    from wikify import embedding

    _reset_embedder_module()
    monkeypatch.setattr(embedding, "_onnx_providers", lambda: None)
    fake = _fake_model(inference_delay=5.0)
    with patch("fastembed.TextEmbedding", return_value=fake):
        embedding._load_fe("sentence-transformers/all-MiniLM-L6-v2")
    assert embedding._fe_model is fake


def test_health_check_opt_out_env_var(monkeypatch):
    """WIKIFY_EMBED_SKIP_HEALTH_CHECK=1 bypasses the check even on
    GPU-requested sessions — escape hatch for weird setups."""
    from wikify import embedding

    _reset_embedder_module()
    monkeypatch.setenv("WIKIFY_EMBED_SKIP_HEALTH_CHECK", "1")
    monkeypatch.setattr(
        embedding, "_onnx_providers",
        lambda: ["DmlExecutionProvider", "CPUExecutionProvider"],
    )
    fake = _fake_model(inference_delay=5.0)
    with patch("fastembed.TextEmbedding", return_value=fake):
        embedding._load_fe("jinaai/jina-embeddings-v2-small-en")
    assert embedding._fe_model is fake


def test_probe_embed_stack_reports_broken_onnxruntime(monkeypatch):
    """A partially-installed onnxruntime imports as an empty namespace
    package with no ``get_available_providers``; the probe must report
    ``ok=False`` with the error instead of raising, and ``_onnx_providers``
    must degrade to ``None`` rather than crashing the query path."""
    import sys
    import types

    from wikify import embedding

    broken = types.ModuleType("onnxruntime")  # no get_available_providers
    monkeypatch.setitem(sys.modules, "onnxruntime", broken)
    monkeypatch.delenv("WIKIFY_EMBEDDER", raising=False)

    result = embedding.probe_embed_stack()
    assert result["ok"] is False
    assert "get_available_providers" in (result["error"] or "")
    assert embedding._onnx_providers() is None  # no AttributeError


def test_probe_embed_stack_hash_backend_ok(monkeypatch):
    """The hash backend needs no onnxruntime, so the probe reports ok."""
    from wikify import embedding

    monkeypatch.setenv("WIKIFY_EMBEDDER", "hash")
    result = embedding.probe_embed_stack()
    assert result["ok"] is True
    assert result["providers"] == ["hash"]


def teardown_module(module):
    """Reset the module-level cache so other tests don't see the mocks."""
    _reset_embedder_module()
