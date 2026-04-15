"""Scale tests for the ingest pipeline.

Uses synthetic markdown files with the hash embedder (pinned in conftest.py)
so no network or GPU is needed.  These tests verify correctness at moderate
scale and catch performance regressions.
"""

import time
from pathlib import Path

import pytest

from wikify.ingest.manifest import CorpusManifest
from wikify.ingest.pipeline import ingest_corpus
from wikify.store.corpus import all_chunks, list_documents, read_knowledge_graph, read_vector_store

_FILLER = " ".join(["word"] * 20)


def _write_papers(root: Path, n: int, prefix: str = "paper") -> None:
    """Generate *n* synthetic markdown papers under *root*."""
    for i in range(n):
        body = f"Body of {prefix}{i:03d}. Unique content {i}. {_FILLER}"
        path = root / f"{prefix}{i:03d}.md"
        path.write_text(f"# {prefix}{i:03d}\n\n{body}\n", encoding="utf-8")


@pytest.fixture
def scale_dirs(tmp_path: Path):
    src = tmp_path / "sources"
    src.mkdir()
    corpus = tmp_path / "corpus"
    return src, corpus


# --- 50-paper correctness ---


def test_50_paper_correctness(scale_dirs):
    """Ingest 50 papers and verify all derived artifacts are consistent."""
    src, corpus = scale_dirs
    _write_papers(src, 50)

    paths = ingest_corpus(src, corpus, max_workers=1)

    docs = list_documents(paths)
    assert len(docs) == 50

    chunks = all_chunks(paths)
    assert len(chunks) >= 50  # at least one chunk per doc

    vs = read_vector_store(paths)
    assert len(vs.ids) == len(chunks)
    assert set(vs.ids) == {c.id for c in chunks}

    kg = read_knowledge_graph(paths, vectors=vs)
    doc_ids = {d.id for d in docs}
    kg_sources = {s["id"] for s in kg.sources(kind="corpus").collect()}
    assert kg_sources == doc_ids

    manifest = CorpusManifest.load(paths.manifest_path)
    active = [s for s in manifest.sources.values() if s.status == "active"]
    assert len(active) == 50
    assert manifest.active_doc_ids() == {d.id for d in docs}


# --- Incremental scale: add, modify, delete ---


def test_incremental_scale(scale_dirs):
    """Ingest 50 papers, then add 10, modify 5, sync-delete 3."""
    src, corpus = scale_dirs
    _write_papers(src, 50)
    paths = ingest_corpus(src, corpus, max_workers=1)
    assert len(list_documents(paths)) == 50

    # Add 10 new papers
    _write_papers(src, 10, prefix="new")
    # Modify 5 existing (change content)
    for i in range(5):
        p = src / f"paper{i:03d}.md"
        p.write_text(f"# paper{i:03d}\n\nModified content {i}. {_FILLER}\n", encoding="utf-8")
    # Delete 3 (remove source files, use sync mode)
    for i in range(47, 50):
        (src / f"paper{i:03d}.md").unlink()

    paths = ingest_corpus(src, corpus, max_workers=1, mode="sync")

    docs = list_documents(paths)
    assert len(docs) == 57  # 50 - 3 deleted + 10 added

    chunks = all_chunks(paths)
    vs = read_vector_store(paths)
    assert set(vs.ids) == {c.id for c in chunks}

    kg = read_knowledge_graph(paths, vectors=vs)
    doc_ids = {d.id for d in docs}
    kg_sources = {s["id"] for s in kg.sources(kind="corpus").collect()}
    assert kg_sources == doc_ids

    manifest = CorpusManifest.load(paths.manifest_path)
    assert len(manifest.active_doc_ids()) == 57


# --- Timing sanity bound ---


def test_timing_100_papers(scale_dirs):
    """Ingest 100 papers and assert it finishes under 60s (hash embedder)."""
    src, corpus = scale_dirs
    _write_papers(src, 100)

    t0 = time.monotonic()
    paths = ingest_corpus(src, corpus, max_workers=1)
    elapsed = time.monotonic() - t0

    docs = list_documents(paths)
    assert len(docs) == 100
    # Generous bound: hash embedder + serial parse of 100 small md files
    assert elapsed < 60.0, f"Ingest took {elapsed:.1f}s, expected < 60s"
