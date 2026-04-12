"""Tests for incremental ingest correctness.

Covers: add, modify, delete (sync), markdown body preservation,
vector id invariants, and distill preload visibility.
"""

from pathlib import Path

import pytest

from wikify.ingest.manifest import CorpusManifest
from wikify.ingest.pipeline import ingest_corpus
from wikify.store.corpus import all_chunks, list_documents, read_graph, read_vector_store


def _write_md(path: Path, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")


@pytest.fixture
def sources_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sources"
    d.mkdir()
    return d


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    return tmp_path / "corpus"


# --- Test: fresh ingest produces correct artifacts ---

def test_fresh_ingest(sources_dir, corpus_dir):
    _write_md(sources_dir / "alpha.md", "Alpha", "Alpha body text with enough words for chunking.")
    _write_md(sources_dir / "beta.md", "Beta", "Beta body text with enough words for chunking too.")

    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    docs = list_documents(paths)
    assert len(docs) == 2
    chunks = all_chunks(paths)
    assert len(chunks) >= 2

    # Vector ids match active chunk ids
    vs = read_vector_store(paths)
    assert set(vs.ids) == {c.id for c in chunks}

    # Graph covers all docs and chunks
    graph = read_graph(paths)
    doc_ids = {d.id for d in docs}
    chunk_ids = {c.id for c in chunks}
    for did in doc_ids:
        assert did in graph.nodes
    for cid in chunk_ids:
        assert cid in graph.nodes

    # Manifest is populated
    manifest = CorpusManifest.load(paths.manifest_path)
    assert len(manifest.sources) == 2
    assert all(s.status == "active" for s in manifest.sources.values())


# --- Test: add source (incremental) preserves existing markdown ---

def test_add_preserves_existing_markdown(sources_dir, corpus_dir):
    _write_md(sources_dir / "alpha.md", "Alpha", "Alpha body text.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    # Read alpha's markdown body
    alpha_doc = list_documents(paths)[0]
    alpha_md = (paths.markdown_dir / f"{alpha_doc.id}.md").read_text(encoding="utf-8")
    assert "Alpha body text" in alpha_md

    # Add a second source
    _write_md(sources_dir / "beta.md", "Beta", "Beta body text.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    docs = list_documents(paths)
    assert len(docs) == 2

    # Alpha's markdown body is preserved
    alpha_md_after = (paths.markdown_dir / f"{alpha_doc.id}.md").read_text(encoding="utf-8")
    assert "Alpha body text" in alpha_md_after


# --- Test: modify source replaces old doc ---

def test_modify_replaces_old_doc(sources_dir, corpus_dir):
    _write_md(sources_dir / "alpha.md", "Alpha v1", "First version of alpha.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    docs_v1 = list_documents(paths)
    assert len(docs_v1) == 1
    old_doc_id = docs_v1[0].id

    # Modify the source
    _write_md(sources_dir / "alpha.md", "Alpha v2", "Second version of alpha.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    docs_v2 = list_documents(paths)
    assert len(docs_v2) == 1, f"Expected 1 doc, got {len(docs_v2)}: {[d.id for d in docs_v2]}"
    new_doc_id = docs_v2[0].id
    assert new_doc_id != old_doc_id

    # Old doc artifacts are gone
    assert not (paths.docs_dir / f"{old_doc_id}.json").exists()
    assert not (paths.chunks_dir / f"{old_doc_id}.jsonl").exists()

    # New markdown has v2 content
    md = (paths.markdown_dir / f"{new_doc_id}.md").read_text(encoding="utf-8")
    assert "Second version" in md

    # Vector ids match only new chunks
    vs = read_vector_store(paths)
    chunks = all_chunks(paths)
    assert set(vs.ids) == {c.id for c in chunks}

    # Manifest has only the new record
    manifest = CorpusManifest.load(paths.manifest_path)
    active = [s for s in manifest.sources.values() if s.status == "active"]
    assert len(active) == 1
    assert active[0].doc_id == new_doc_id


# --- Test: sync mode removes absent sources ---

def test_sync_removes_absent(sources_dir, corpus_dir):
    _write_md(sources_dir / "alpha.md", "Alpha", "Alpha body.")
    _write_md(sources_dir / "beta.md", "Beta", "Beta body.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    docs = list_documents(paths)
    assert len(docs) == 2
    beta_id = [d.id for d in docs if "beta" in d.id.lower()][0]

    # Remove beta from sources and run sync
    (sources_dir / "beta.md").unlink()
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1, mode="sync")

    docs_after = list_documents(paths)
    assert len(docs_after) == 1
    assert docs_after[0].id != beta_id

    # Beta artifacts are gone
    assert not (paths.docs_dir / f"{beta_id}.json").exists()
    assert not (paths.chunks_dir / f"{beta_id}.jsonl").exists()

    # Graph only has alpha
    graph = read_graph(paths)
    assert beta_id not in graph.nodes

    # Vectors only have alpha's chunks
    vs = read_vector_store(paths)
    chunks = all_chunks(paths)
    assert set(vs.ids) == {c.id for c in chunks}
    for cid in vs.ids:
        assert beta_id not in cid


# --- Test: distill preload does not see deleted docs ---

def test_distill_preload_excludes_deleted(sources_dir, corpus_dir):
    from wikify.distill.preload import preload_corpus

    _write_md(sources_dir / "alpha.md", "Alpha", "Alpha body.")
    _write_md(sources_dir / "beta.md", "Beta", "Beta body.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    # Remove beta and sync
    (sources_dir / "beta.md").unlink()
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1, mode="sync")

    loaded = preload_corpus(paths)
    doc_ids = {d.id for d in loaded.docs}
    chunk_doc_ids = {c.doc_id for c in loaded.chunks}

    # Beta should not appear anywhere in the preloaded corpus
    for did in doc_ids:
        assert "beta" not in did.lower() or False, f"Deleted doc visible: {did}"
    for cdid in chunk_doc_ids:
        assert "beta" not in cdid.lower() or False, f"Deleted chunk visible: {cdid}"
