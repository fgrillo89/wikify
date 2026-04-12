"""Tests for incremental ingest correctness.

Covers: add, modify, delete (sync), markdown body preservation,
vector id invariants, parse-failure preservation, nested same-name
files, vector reuse, and distill preload visibility.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from wikify.ingest.manifest import CorpusManifest
from wikify.ingest.pipeline import ingest_corpus
from wikify.store.corpus import (
    all_chunks,
    list_documents,
    read_graph,
    read_vector_store,
)

# Body filler long enough to survive MIN_CHUNK_ALNUM=30.
_FILLER = " ".join(["word"] * 20)


def _write_md(path: Path, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{body} {_FILLER}\n", encoding="utf-8")


@pytest.fixture
def sources_dir(tmp_path: Path) -> Path:
    d = tmp_path / "sources"
    d.mkdir()
    return d


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    return tmp_path / "corpus"


# --- Fresh ingest ---

def test_fresh_ingest(sources_dir, corpus_dir):
    _write_md(sources_dir / "alpha.md", "Alpha", "Alpha body text.")
    _write_md(sources_dir / "beta.md", "Beta", "Beta body text.")

    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    docs = list_documents(paths)
    assert len(docs) == 2
    chunks = all_chunks(paths)
    assert len(chunks) >= 2

    vs = read_vector_store(paths)
    assert set(vs.ids) == {c.id for c in chunks}

    graph = read_graph(paths)
    for d in docs:
        assert d.id in graph.nodes
    for c in chunks:
        assert c.id in graph.nodes

    manifest = CorpusManifest.load(paths.manifest_path)
    assert len(manifest.sources) == 2
    assert all(s.status == "active" for s in manifest.sources.values())
    for artifact in (
        paths.library_bib_path,
        paths.references_bib_path,
        paths.bibliography_bib_path,
        paths.citation_index_path,
    ):
        assert artifact.exists()


# --- Add preserves existing markdown ---

def test_add_preserves_existing_markdown(sources_dir, corpus_dir):
    _write_md(sources_dir / "alpha.md", "Alpha", "Alpha body text.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    alpha_doc = list_documents(paths)[0]
    alpha_md = (paths.markdown_dir / f"{alpha_doc.id}.md").read_text(
        encoding="utf-8"
    )
    assert "Alpha body text" in alpha_md

    _write_md(sources_dir / "beta.md", "Beta", "Beta body text.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    docs = list_documents(paths)
    assert len(docs) == 2
    index = json.loads(paths.citation_index_path.read_text(encoding="utf-8"))
    assert set(index["doc_bibkeys"]) == {doc.id for doc in docs}

    alpha_md_after = (paths.markdown_dir / f"{alpha_doc.id}.md").read_text(
        encoding="utf-8"
    )
    assert "Alpha body text" in alpha_md_after


# --- Modify replaces old doc ---

def test_modify_replaces_old_doc(sources_dir, corpus_dir):
    _write_md(sources_dir / "alpha.md", "Alpha v1", "First version body.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    old_doc_id = list_documents(paths)[0].id

    _write_md(sources_dir / "alpha.md", "Alpha v2", "Second version body.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    docs = list_documents(paths)
    assert len(docs) == 1
    new_doc_id = docs[0].id
    assert new_doc_id != old_doc_id

    assert not (paths.docs_dir / f"{old_doc_id}.json").exists()
    assert not (paths.chunks_dir / f"{old_doc_id}.jsonl").exists()

    md = (paths.markdown_dir / f"{new_doc_id}.md").read_text(encoding="utf-8")
    assert "Second version" in md

    vs = read_vector_store(paths)
    chunks = all_chunks(paths)
    assert set(vs.ids) == {c.id for c in chunks}

    manifest = CorpusManifest.load(paths.manifest_path)
    active = [s for s in manifest.sources.values() if s.status == "active"]
    assert len(active) == 1
    assert active[0].doc_id == new_doc_id


# --- Sync removes absent ---

def test_sync_removes_absent(sources_dir, corpus_dir):
    _write_md(sources_dir / "alpha.md", "Alpha", "Alpha body.")
    _write_md(sources_dir / "beta.md", "Beta", "Beta body.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    docs = list_documents(paths)
    assert len(docs) == 2
    beta_id = [d.id for d in docs if "beta" in d.id.lower()][0]

    (sources_dir / "beta.md").unlink()
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1, mode="sync")

    docs_after = list_documents(paths)
    assert len(docs_after) == 1
    assert docs_after[0].id != beta_id

    assert not (paths.docs_dir / f"{beta_id}.json").exists()

    graph = read_graph(paths)
    assert beta_id not in graph.nodes

    vs = read_vector_store(paths)
    chunks = all_chunks(paths)
    assert set(vs.ids) == {c.id for c in chunks}
    for cid in vs.ids:
        assert beta_id not in cid


# --- Distill preload excludes deleted ---

def test_distill_preload_excludes_deleted(sources_dir, corpus_dir):
    from wikify.distill.preload import preload_corpus

    _write_md(sources_dir / "alpha.md", "Alpha", "Alpha body.")
    _write_md(sources_dir / "beta.md", "Beta", "Beta body.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    (sources_dir / "beta.md").unlink()
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1, mode="sync")

    loaded = preload_corpus(paths)
    doc_ids = {d.id for d in loaded.docs}
    chunk_doc_ids = {c.doc_id for c in loaded.chunks}

    for did in doc_ids:
        assert "beta" not in did.lower(), f"Deleted doc visible: {did}"
    for cdid in chunk_doc_ids:
        assert "beta" not in cdid.lower(), f"Deleted chunk visible: {cdid}"


# --- Parse failure preserves old artifacts (Finding #1) ---

def test_parse_failure_preserves_old_artifacts(sources_dir, corpus_dir):
    """If a modified source fails to parse, the old doc stays active."""
    _write_md(sources_dir / "alpha.md", "Alpha v1", "First version body.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    old_doc_id = list_documents(paths)[0].id
    old_md = (paths.markdown_dir / f"{old_doc_id}.md").read_text(
        encoding="utf-8"
    )
    assert "First version body" in old_md

    # Modify file to trigger replacement
    _write_md(sources_dir / "alpha.md", "Alpha v2", "Second version body.")

    # Patch parse_file to raise for the modified file
    real_parse = __import__(
        "wikify.ingest.parsers.registry", fromlist=["parse_file"]
    ).parse_file

    def failing_parse(path):
        if "alpha" in path.name:
            raise RuntimeError("simulated parse failure")
        return real_parse(path)

    with patch("wikify.ingest.pipeline.parse_file", side_effect=failing_parse):
        paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    # Old doc must still be on disk and active
    docs = list_documents(paths)
    assert len(docs) == 1
    assert docs[0].id == old_doc_id

    preserved_md = (paths.markdown_dir / f"{old_doc_id}.md").read_text(
        encoding="utf-8"
    )
    assert "First version body" in preserved_md

    manifest = CorpusManifest.load(paths.manifest_path)
    active = [s for s in manifest.sources.values() if s.status == "active"]
    assert len(active) == 1
    assert active[0].doc_id == old_doc_id


# --- Nested same-name files (Finding #2) ---

def test_nested_same_name_files(sources_dir, corpus_dir):
    """set1/alpha.md and set2/alpha.md must coexist without collision."""
    (sources_dir / "set1").mkdir()
    (sources_dir / "set2").mkdir()
    _write_md(sources_dir / "set1" / "alpha.md", "Alpha Set1", "Set1 body.")
    _write_md(sources_dir / "set2" / "alpha.md", "Alpha Set2", "Set2 body.")

    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    docs = list_documents(paths)
    assert len(docs) == 2

    manifest = CorpusManifest.load(paths.manifest_path)
    active = [s for s in manifest.sources.values() if s.status == "active"]
    assert len(active) == 2
    sids = {s.source_id for s in active}
    # source_ids must be different
    assert len(sids) == 2
    # Both should contain "alpha" but be distinguishable
    assert all("alpha" in sid for sid in sids)

    # Modify only one
    _write_md(
        sources_dir / "set1" / "alpha.md", "Alpha Set1 v2", "Set1 v2 body."
    )
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    docs = list_documents(paths)
    assert len(docs) == 2

    # Check that set2/alpha is unchanged
    manifest = CorpusManifest.load(paths.manifest_path)
    active = {s.source_id: s for s in manifest.sources.values()
              if s.status == "active"}
    assert len(active) == 2


# --- Vector reuse (Finding #3) ---

def test_vector_reuse_on_incremental(sources_dir, corpus_dir):
    """Adding a source should reuse vectors for unchanged chunks."""
    _write_md(sources_dir / "alpha.md", "Alpha", "Alpha body text.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    alpha_chunks = all_chunks(paths)
    old_vs = read_vector_store(paths)
    alpha_vecs = {cid: old_vs.matrix[i].copy()
                  for i, cid in enumerate(old_vs.ids)}

    # Add beta
    _write_md(sources_dir / "beta.md", "Beta", "Beta body text.")

    embed_call_texts: list[list[str]] = []
    real_embed = __import__(
        "wikify.ingest.pipeline", fromlist=["embed_texts"]
    ).embed_texts

    def tracking_embed(texts):
        embed_call_texts.append(list(texts))
        return real_embed(texts)

    with patch("wikify.ingest.pipeline.embed_texts", side_effect=tracking_embed):
        paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    # Alpha's chunk ids should NOT have been re-embedded
    all_embedded_texts = []
    for batch in embed_call_texts:
        all_embedded_texts.extend(batch)

    alpha_texts = {c.text for c in alpha_chunks}
    newly_embedded = set(all_embedded_texts)
    # None of alpha's texts should appear in the embed calls
    assert not (alpha_texts & newly_embedded), (
        "Alpha chunks were re-embedded instead of reused"
    )

    # Vectors for alpha chunks should be identical
    new_vs = read_vector_store(paths)
    for cid, old_vec in alpha_vecs.items():
        if cid in {c_id for c_id in new_vs.ids}:
            idx = new_vs.ids.index(cid)
            assert (new_vs.matrix[idx] == old_vec).all(), (
                f"Vector changed for unchanged chunk {cid}"
            )


# --- Embedder change re-embeds everything (Finding #2) ---

def test_embedder_change_reembeds_all(sources_dir, corpus_dir):
    """Changing WIKIFY_EMBEDDER must re-embed all chunks, not reuse old."""

    _write_md(sources_dir / "alpha.md", "Alpha", "Alpha body text.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    # Tamper with the vectors.meta.json to simulate a different backend
    from wikify.store.vectors_meta import VectorsMeta, write_meta

    write_meta(paths.vectors_path, VectorsMeta(
        backend="fake_old_backend", dim=999, model="old-model",
    ))

    embed_call_count = [0]
    real_embed = __import__(
        "wikify.ingest.pipeline", fromlist=["embed_texts"]
    ).embed_texts

    def counting_embed(texts):
        embed_call_count[0] += len(texts)
        return real_embed(texts)

    # Add beta to trigger an incremental run
    _write_md(sources_dir / "beta.md", "Beta", "Beta body text.")

    with patch(
        "wikify.ingest.pipeline.embed_texts",
        side_effect=counting_embed,
    ):
        paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    chunks = all_chunks(paths)
    # ALL chunks should have been embedded (no reuse from wrong backend)
    assert embed_call_count[0] == len(chunks), (
        f"Expected {len(chunks)} embeddings, got {embed_call_count[0]}"
    )


# --- Same-stem sources get distinct image dirs (Finding #1) ---

def test_same_stem_image_dirs_distinct(sources_dir, corpus_dir):
    """set1/alpha.md and set2/alpha.md must not share an image folder."""
    from wikify.ingest.pipeline import doc_id_for, image_slug

    (sources_dir / "set1").mkdir()
    (sources_dir / "set2").mkdir()

    a1 = sources_dir / "set1" / "alpha.md"
    a2 = sources_dir / "set2" / "alpha.md"
    _write_md(a1, "Alpha Set1", "Set1 body.")
    _write_md(a2, "Alpha Set2", "Set2 body.")

    did1 = doc_id_for(a1)
    did2 = doc_id_for(a2)
    # Different content -> different doc_ids
    assert did1 != did2

    slug1 = image_slug(did1)
    slug2 = image_slug(did2)
    # Image slugs must be different
    assert slug1 != slug2, (
        f"Image slug collision: {slug1} for both doc_ids"
    )

    # Full ingest should produce two docs with distinct image dirs
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)
    docs = list_documents(paths)
    assert len(docs) == 2
    image_dirs = {d.image_dir for d in docs}
    assert len(image_dirs) == 2


# --- Duplicate-content aliases: sync delete one keeps shared doc ---

def test_duplicate_content_alias_sync(sources_dir, corpus_dir):
    """Two sources with identical bytes: deleting one in sync mode
    must NOT remove the shared physical doc."""
    (sources_dir / "copy1").mkdir()
    (sources_dir / "copy2").mkdir()
    body = "Identical body."
    _write_md(sources_dir / "copy1" / "paper.md", "Paper", body)
    _write_md(sources_dir / "copy2" / "paper.md", "Paper", body)

    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    manifest = CorpusManifest.load(paths.manifest_path)
    active = [s for s in manifest.sources.values()
              if s.status == "active"]
    assert len(active) == 2
    # Both should reference the same doc_id (same content hash)
    doc_ids = {s.doc_id for s in active}
    assert len(doc_ids) == 1, (
        f"Expected 1 shared doc_id, got {doc_ids}"
    )
    shared_did = doc_ids.pop()

    # Remove one copy and sync
    import shutil

    shutil.rmtree(sources_dir / "copy2")
    paths = ingest_corpus(
        sources_dir, corpus_dir, max_workers=1, mode="sync",
    )

    # The shared doc should still exist (copy1 still references it)
    docs = list_documents(paths)
    assert len(docs) == 1
    assert docs[0].id == shared_did

    manifest = CorpusManifest.load(paths.manifest_path)
    active = [s for s in manifest.sources.values()
              if s.status == "active"]
    assert len(active) == 1


# --- Duplicate-content with different filenames (alias doc_id fix) ---

def test_duplicate_content_different_names_alias(sources_dir, corpus_dir):
    """copy1/foo.md and copy2/bar.md with identical bytes: alias must
    reference foo's persisted doc_id, not a non-existent bar_<hash>."""
    (sources_dir / "copy1").mkdir()
    (sources_dir / "copy2").mkdir()
    body = "Identical body."
    _write_md(sources_dir / "copy1" / "foo.md", "Paper", body)
    _write_md(sources_dir / "copy2" / "bar.md", "Paper", body)

    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    manifest = CorpusManifest.load(paths.manifest_path)
    active = [s for s in manifest.sources.values()
              if s.status == "active"]
    assert len(active) == 2

    # Both must reference the same doc_id (the one actually on disk)
    doc_ids = {s.doc_id for s in active}
    assert len(doc_ids) == 1, f"Expected shared doc_id, got {doc_ids}"

    # That doc_id must actually exist on disk
    shared = doc_ids.pop()
    assert (paths.docs_dir / f"{shared}.json").exists()


# --- Unknown parser backend fails fast ---

def test_unknown_parser_backend_raises(sources_dir, corpus_dir):
    _write_md(sources_dir / "alpha.md", "Alpha", "Alpha body.")
    with pytest.raises(ValueError, match="unknown parser backend"):
        ingest_corpus(
            sources_dir, corpus_dir,
            max_workers=1, parser_backend="nonexistent",
        )


# --- Same-stem sources with RawImage: image sidecars don't collide ---

def test_same_stem_images_survive(sources_dir, corpus_dir):
    """Two same-stem PDFs with images must get separate image dirs."""
    from wikify.ingest.parsers.registry import RawImage

    (sources_dir / "set1").mkdir()
    (sources_dir / "set2").mkdir()
    _write_md(sources_dir / "set1" / "alpha.md", "A1", "Set1.")
    _write_md(sources_dir / "set2" / "alpha.md", "A2", "Set2.")

    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        b"\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDATx\x9cc\xf8\xff"
        b"\xff?\x00\x05\xfe\x02\xfeA5\xc8\x91"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


    call_count = [0]
    real_parse = __import__(
        "wikify.ingest.parsers.registry", fromlist=["parse_file"]
    ).parse_file

    def parse_with_images(path, **kw):
        kind, result = real_parse(path, **kw)
        if "alpha" in path.name:
            call_count[0] += 1
            result.raw_images = [
                RawImage(
                    data=png, ext="png",
                    caption=f"Fig from {path.parent.name}",
                    label=f"Fig. {call_count[0]}",
                )
            ]
        return kind, result

    with patch(
        "wikify.ingest.pipeline.parse_file",
        side_effect=parse_with_images,
    ):
        paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    docs = list_documents(paths)
    assert len(docs) == 2

    # Both docs should have images and distinct image_dirs
    image_dirs = {d.image_dir for d in docs}
    assert len(image_dirs) == 2, (
        f"Image dir collision: {image_dirs}"
    )

    # Both image dirs should have sidecars on disk
    for d in docs:
        img_dir = Path(d.image_dir)
        sidecars = list(img_dir.glob("*.json")) if img_dir.exists() else []
        assert sidecars, f"No sidecars for doc {d.id} in {d.image_dir}"


# --- Cross-run duplicate alias ---

def test_cross_run_duplicate_becomes_alias(sources_dir, corpus_dir):
    """Ingest foo.md, then add bar.md with identical bytes in a later run.
    bar should become an alias, not a separate doc."""
    body = "Shared content for cross-run dedup."
    _write_md(sources_dir / "foo.md", "Paper", body)
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    docs_r1 = list_documents(paths)
    assert len(docs_r1) == 1
    foo_did = docs_r1[0].id

    # Add bar with identical content in a second run
    _write_md(sources_dir / "bar.md", "Paper", body)
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    # Should still be 1 doc on disk (bar is an alias, not a new doc)
    docs_r2 = list_documents(paths)
    assert len(docs_r2) == 1, (
        f"Expected 1 doc, got {len(docs_r2)}: {[d.id for d in docs_r2]}"
    )
    assert docs_r2[0].id == foo_did

    # Manifest has 2 active sources pointing at the same doc_id
    manifest = CorpusManifest.load(paths.manifest_path)
    active = [s for s in manifest.sources.values()
              if s.status == "active"]
    assert len(active) == 2
    doc_ids = {s.doc_id for s in active}
    assert len(doc_ids) == 1
    assert foo_did in doc_ids


# --- Alias to failed parse does not register ---

def test_alias_to_failed_parse_not_registered(sources_dir, corpus_dir):
    """If canonical source fails to parse, its alias must not be
    registered either."""
    body = "Identical content for alias failure test."
    (sources_dir / "set1").mkdir()
    (sources_dir / "set2").mkdir()
    _write_md(sources_dir / "set1" / "alpha.md", "A", body)
    _write_md(sources_dir / "set2" / "alpha.md", "A", body)

    real_parse = __import__(
        "wikify.ingest.parsers.registry", fromlist=["parse_file"]
    ).parse_file

    call_count = [0]

    def fail_first_alpha(path, **kw):
        if "alpha" in path.name:
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated first-alpha failure")
        return real_parse(path, **kw)

    with patch(
        "wikify.ingest.pipeline.parse_file",
        side_effect=fail_first_alpha,
    ):
        paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    manifest = CorpusManifest.load(paths.manifest_path)
    active = [s for s in manifest.sources.values()
              if s.status == "active"]
    # The canonical parse failed, so neither source should have an
    # active manifest record pointing at a non-existent doc
    for rec in active:
        assert (paths.docs_dir / f"{rec.doc_id}.json").exists(), (
            f"Active manifest record {rec.source_id} points at "
            f"non-existent doc {rec.doc_id}"
        )


# --- Replacement becomes alias when new content matches existing doc ---

def test_replacement_becomes_alias_to_existing(sources_dir, corpus_dir):
    """Edit bar.md so its bytes match foo.md. bar should become an alias
    to foo's doc_id, and bar's old doc should be removed."""
    _write_md(sources_dir / "foo.md", "Foo", "Shared content.")
    _write_md(sources_dir / "bar.md", "Bar", "Original bar content.")
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    docs_r1 = list_documents(paths)
    assert len(docs_r1) == 2
    foo_did = next(d.id for d in docs_r1 if "foo" in d.id)
    bar_old_did = next(d.id for d in docs_r1 if "bar" in d.id)

    # Edit bar to match foo's content exactly
    foo_bytes = (sources_dir / "foo.md").read_bytes()
    (sources_dir / "bar.md").write_bytes(foo_bytes)

    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)

    # Should be 1 doc on disk (bar is now an alias to foo)
    docs_r2 = list_documents(paths)
    assert len(docs_r2) == 1, (
        f"Expected 1 doc, got {[d.id for d in docs_r2]}"
    )
    assert docs_r2[0].id == foo_did

    # Old bar doc is gone
    assert not (paths.docs_dir / f"{bar_old_did}.json").exists()

    # Manifest has 2 active sources pointing at foo's doc_id
    manifest = CorpusManifest.load(paths.manifest_path)
    active = [s for s in manifest.sources.values()
              if s.status == "active"]
    assert len(active) == 2
    assert all(s.doc_id == foo_did for s in active)


# --- Unregistered parser backend fails fast before any parsing ---

def test_unregistered_backend_raises_before_ingest(sources_dir, corpus_dir):
    """Selecting a backend not in the enum or custom registry must raise
    ValueError before any corpus artifacts are written."""
    _write_md(sources_dir / "alpha.md", "Alpha", "Alpha body.")
    with pytest.raises(ValueError, match="unknown parser backend"):
        ingest_corpus(
            sources_dir, corpus_dir,
            max_workers=1, parser_backend="docling",
        )
    # No corpus artifacts should have been created.
    assert not corpus_dir.exists() or not list(corpus_dir.iterdir())


# --- Atomic write: crash mid-persist leaves corpus recoverable ---

def test_crash_mid_persist_recoverable(sources_dir, corpus_dir):
    """If ingest crashes after persisting some docs but before finishing,
    a subsequent ingest should recover cleanly."""
    _write_md(sources_dir / "alpha.md", "Alpha", "Alpha body text.")
    _write_md(sources_dir / "beta.md", "Beta", "Beta body text.")
    _write_md(sources_dir / "gamma.md", "Gamma", "Gamma body text.")

    call_count = [0]
    real_write = __import__(
        "wikify.store.corpus", fromlist=["write_document"]
    ).write_document

    def crash_on_second(paths_arg, doc, markdown, chunks):
        call_count[0] += 1
        if call_count[0] == 2:
            raise OSError("simulated disk failure")
        return real_write(paths_arg, doc, markdown, chunks)

    # First ingest crashes mid-persist
    with patch(
        "wikify.ingest.pipeline.write_document",
        side_effect=crash_on_second,
    ):
        try:
            ingest_corpus(sources_dir, corpus_dir, max_workers=1)
        except OSError:
            pass  # expected crash

    # Second ingest should succeed cleanly (additive mode re-parses all)
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)
    docs = list_documents(paths)
    assert len(docs) == 3


# --- Atomic write: crash during final resave leaves corpus recoverable ---

def test_crash_during_resave_recoverable(sources_dir, corpus_dir):
    """If ingest crashes during the final _resave_docs (after graph
    population), a subsequent ingest should recover cleanly."""
    _write_md(sources_dir / "alpha.md", "Alpha", "Alpha body text.")
    _write_md(sources_dir / "beta.md", "Beta", "Beta body text.")

    from wikify.store.corpus import atomic_write_text

    call_count = [0]
    real_write = atomic_write_text

    def crash_during_resave(path, content):
        # Let initial write_document calls through; crash on the
        # doc resave pass (which rewrites docs/*.json a second time).
        if "docs" in str(path) and path.suffix == ".json":
            call_count[0] += 1
            # First 2 calls are initial persist; 3rd+ are resave
            if call_count[0] > 2:
                raise OSError("simulated resave failure")
        return real_write(path, content)

    with patch(
        "wikify.store.corpus.atomic_write_text",
        side_effect=crash_during_resave,
    ):
        try:
            ingest_corpus(sources_dir, corpus_dir, max_workers=1)
        except OSError:
            pass  # expected crash

    # Second ingest should succeed cleanly
    paths = ingest_corpus(sources_dir, corpus_dir, max_workers=1)
    docs = list_documents(paths)
    assert len(docs) == 2
