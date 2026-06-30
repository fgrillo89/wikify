"""Tests for new helpers in ``wikify.ingest.pipeline``."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from wikify.api import Corpus
from wikify.ingest import pipeline


def test_worker_batch_size_default_for_gpu_backends() -> None:
    """GPU backends restart workers every 20 papers by default."""
    assert pipeline._worker_batch_size("default") == 20
    assert pipeline._worker_batch_size("docling") == 20


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


# --- refresh self-heal (derived-artifacts health check) --------------------


def _make_corpus_with_db(
    tmp_path: Path, *, n_chunks: int, n_embeddings: int,
) -> Corpus:
    """Corpus with one markdown file and a chunks/embeddings DB.

    First ``n_embeddings`` of the ``n_chunks`` chunks get a ``node_type=
    'chunk'`` vector row; the rest are unembedded. Mirrors the real
    embeddings schema (space_id, node_type, node_id) so the health check's
    anti-join query exercises the same shape it does in production.
    """
    import sqlite3

    corpus = Corpus(root=tmp_path / "c")
    corpus.markdown_dir.mkdir(parents=True, exist_ok=True)
    (corpus.markdown_dir / "doc.md").write_text("# doc", encoding="utf-8")
    con = sqlite3.connect(corpus.sqlite_path)
    con.execute("CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY)")
    con.execute(
        "CREATE TABLE embeddings (space_id TEXT, node_type TEXT, "
        "node_id TEXT, vector BLOB, PRIMARY KEY (space_id, node_type, node_id))"
    )
    con.executemany("INSERT INTO chunks (chunk_id) VALUES (?)",
                    [(f"c{i}",) for i in range(n_chunks)])
    con.executemany(
        "INSERT INTO embeddings (space_id, node_type, node_id, vector) "
        "VALUES ('s', 'chunk', ?, x'00')",
        [(f"c{i}",) for i in range(n_embeddings)],
    )
    con.commit()
    con.close()
    return corpus


def test_derived_missing_false_when_no_markdown(tmp_path: Path) -> None:
    corpus = Corpus(root=tmp_path / "c")
    assert pipeline._derived_artifacts_missing(corpus) is False


def test_derived_missing_true_when_markdown_but_no_db(tmp_path: Path) -> None:
    corpus = Corpus(root=tmp_path / "c")
    corpus.markdown_dir.mkdir(parents=True, exist_ok=True)
    (corpus.markdown_dir / "doc.md").write_text("# doc", encoding="utf-8")
    assert pipeline._derived_artifacts_missing(corpus) is True


def test_derived_missing_true_when_chunks_but_no_embeddings(
    tmp_path: Path,
) -> None:
    """Refresh killed mid-way: DB exists, chunks persisted, zero vectors."""
    corpus = _make_corpus_with_db(tmp_path, n_chunks=5, n_embeddings=0)
    assert pipeline._has_chunks_without_embeddings(corpus) is True
    assert pipeline._derived_artifacts_missing(corpus) is True


def test_derived_missing_true_when_some_chunks_unembedded(
    tmp_path: Path,
) -> None:
    """Incremental crash: old vectors present, new chunks unembedded."""
    corpus = _make_corpus_with_db(tmp_path, n_chunks=5, n_embeddings=1)
    assert pipeline._has_chunks_without_embeddings(corpus) is True
    assert pipeline._derived_artifacts_missing(corpus) is True


def test_derived_missing_false_when_embeddings_present(tmp_path: Path) -> None:
    corpus = _make_corpus_with_db(tmp_path, n_chunks=5, n_embeddings=5)
    assert pipeline._has_chunks_without_embeddings(corpus) is False
    assert pipeline._derived_artifacts_missing(corpus) is False


# --- embedding GPU provider selection --------------------------------------


def test_cuda_device_visible_returns_bool() -> None:
    """Probe returns a bool and never raises, regardless of torch state."""
    from wikify import embedding

    assert isinstance(embedding._cuda_device_visible(), bool)


def test_onnx_providers_force_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    from wikify import embedding

    monkeypatch.setenv("WIKIFY_EMBED_FORCE_CPU", "1")
    assert embedding._onnx_providers() == ["CPUExecutionProvider"]


def test_onnx_providers_requests_cuda_when_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CUDA in available providers + a visible device -> CUDA requested."""
    from wikify import embedding

    monkeypatch.delenv("WIKIFY_EMBED_FORCE_CPU", raising=False)
    monkeypatch.setattr(embedding, "_cuda_device_visible", lambda: True)
    monkeypatch.setattr(embedding, "_preload_cuda_dlls", lambda: True)

    import onnxruntime as ort
    monkeypatch.setattr(
        ort, "get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    assert embedding._onnx_providers() == [
        "CUDAExecutionProvider", "CPUExecutionProvider",
    ]


def test_onnx_providers_cpu_when_no_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CUDA compiled in but no visible device -> fall through to CPU/None."""
    from wikify import embedding

    monkeypatch.delenv("WIKIFY_EMBED_FORCE_CPU", raising=False)
    monkeypatch.setattr(embedding, "_cuda_device_visible", lambda: False)

    import onnxruntime as ort
    monkeypatch.setattr(
        ort, "get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    assert embedding._onnx_providers() is None


def test_preload_cuda_dlls_returns_bool() -> None:
    """Best-effort preload returns a bool and never raises (CPU or GPU)."""
    from wikify import embedding

    assert isinstance(embedding._preload_cuda_dlls(), bool)


# --- parse timeout: kill wedged worker -------------------------------------


def _hang_worker(*_args: object, **_kwargs: object) -> None:
    """Module-level (picklable) worker that outlives a short parse_timeout."""
    import time
    time.sleep(5)


def test_terminate_pool_workers_no_raise() -> None:
    """_terminate_pool_workers tolerates an empty / live pool."""
    from concurrent.futures import ProcessPoolExecutor

    pool = ProcessPoolExecutor(max_workers=1)
    try:
        pipeline._terminate_pool_workers(pool)  # no workers spawned yet
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _proc_alive(pid: int) -> bool:
    """True if pid is a live, non-zombie process (a killed child zombifies
    on posix until reaped, which pid_exists would still report as alive)."""
    import psutil

    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def test_terminate_pool_workers_kills_live_worker() -> None:
    """The worker process is actually killed, not just left to time out."""
    import time as _time
    from concurrent.futures import ProcessPoolExecutor

    pool = ProcessPoolExecutor(max_workers=1)
    try:
        pool.submit(_hang_worker)  # spawn a worker that sleeps 5s
        deadline = _time.monotonic() + 10
        pids: list[int] = []
        while _time.monotonic() < deadline:
            pids = [p.pid for p in pool._processes.values()]
            if pids and all(_proc_alive(pid) for pid in pids):
                break
            _time.sleep(0.05)
        assert pids, "worker never spawned"
        assert all(_proc_alive(pid) for pid in pids)

        pipeline._terminate_pool_workers(pool)

        deadline = _time.monotonic() + 10
        while _time.monotonic() < deadline:
            if not any(_proc_alive(pid) for pid in pids):
                break
            _time.sleep(0.05)
        assert not any(_proc_alive(pid) for pid in pids), "workers survived kill"
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="relies on fork-inherited monkeypatch (posix only)",
)
def test_parse_timeout_kills_wedged_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file exceeding parse_timeout is killed and counted as failed."""
    monkeypatch.setattr(pipeline, "_parse_and_persist_worker", _hang_worker)
    corpus = Corpus(root=tmp_path / "c")
    corpus.root.mkdir(parents=True, exist_ok=True)
    srcs = []
    for name in ("a.pdf", "b.pdf"):
        p = tmp_path / name
        p.write_text("x", encoding="utf-8")
        srcs.append(p)
    # "default" is a GPU backend -> subprocess-batched path (batch_size>0).
    receipts, failed = pipeline._stream_parse_and_persist(
        srcs, corpus, max_workers=1, parser_backend="default",
        parse_timeout=0.5,
    )
    assert failed == 2
    assert receipts == []


def test_release_gpu_memory_no_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_release_gpu_memory`` is a no-op when torch is unavailable."""
    from wikify.ingest.parsers import docling

    # Just call it — must not raise even on a CPU-only or torch-less host.
    docling._release_gpu_memory()
