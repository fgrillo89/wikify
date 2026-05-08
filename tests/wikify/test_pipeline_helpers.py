"""Tests for new helpers in ``wikify.ingest.pipeline``."""

from __future__ import annotations

from pathlib import Path

import pytest

from wikify.api import Corpus
from wikify.ingest import pipeline


def test_worker_batch_size_default_for_gpu_backends() -> None:
    """GPU backends restart workers every 20 papers by default."""
    assert pipeline._worker_batch_size("default") == 20
    assert pipeline._worker_batch_size("docling") == 20
    assert pipeline._worker_batch_size("marker") == 20


def test_worker_batch_size_zero_for_cpu_backends() -> None:
    """CPU backends keep their long-lived pool (0 = no restart)."""
    assert pipeline._worker_batch_size("lite") == 0


def test_worker_batch_size_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """``WIKIFY_WORKER_BATCH_SIZE`` overrides the default."""
    monkeypatch.setenv("WIKIFY_WORKER_BATCH_SIZE", "5")
    assert pipeline._worker_batch_size("default") == 5
    assert pipeline._worker_batch_size("lite") == 5


def test_worker_batch_size_env_zero_disables_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WIKIFY_WORKER_BATCH_SIZE", "0")
    assert pipeline._worker_batch_size("default") == 0


def test_worker_batch_size_env_invalid_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Garbage env value falls back to backend default."""
    monkeypatch.setenv("WIKIFY_WORKER_BATCH_SIZE", "not-a-number")
    assert pipeline._worker_batch_size("default") == 20


def test_log_failure_writes_structured_line(tmp_path: Path) -> None:
    """``_log_failure`` appends one tab-separated line per call."""
    corpus = Corpus(root=tmp_path / "c")
    src = Path("/some/dir/paper.pdf")
    pipeline._log_failure(corpus, src, RuntimeError("boom"))
    pipeline._log_failure(corpus, Path("/other/q.pdf"), ValueError("bad"))

    log = (corpus.root / "failed_files.log").read_text(encoding="utf-8")
    lines = [ln for ln in log.splitlines() if ln]
    assert len(lines) == 2
    # Format: ISO\tname\tType: message
    for line in lines:
        cols = line.split("\t")
        assert len(cols) == 3
        # ISO-8601 timestamp prefix
        assert cols[0].endswith("Z")
        assert "T" in cols[0]
    assert "paper.pdf" in lines[0]
    assert "RuntimeError: boom" in lines[0]
    assert "q.pdf" in lines[1]
    assert "ValueError: bad" in lines[1]


def test_log_failure_creates_corpus_root(tmp_path: Path) -> None:
    """Log path's parent is created if missing — prevents writes failing
    when ingest_corpus crashes before paths.ensure() ran."""
    corpus = Corpus(root=tmp_path / "nested" / "fresh")
    assert not corpus.root.exists()
    pipeline._log_failure(corpus, Path("a.pdf"), RuntimeError("x"))
    assert (corpus.root / "failed_files.log").exists()


def test_release_gpu_memory_no_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_release_gpu_memory`` is a no-op when torch is unavailable."""
    from wikify.ingest.parsers import docling

    # Just call it — must not raise even on a CPU-only or torch-less host.
    docling._release_gpu_memory()
