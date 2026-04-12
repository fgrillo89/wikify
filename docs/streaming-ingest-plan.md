# Streaming Ingest Pipeline Refactor

## Context

The current ingest pipeline holds ALL parsed bundles in memory before persisting anything, then runs corpus-wide operations (embeddings, graph, topics) in sequence. This means:
- No visibility into progress until everything is parsed
- Memory scales linearly with corpus size
- A crash mid-parse loses all work
- Slow parsers (docling ~30s/paper on CPU) block everything

Per-file work should stream to disk as it completes. Corpus-wide operations
should be a separate "refresh" step that can be re-run whenever the corpus
expands.

## Design: Two-Command Architecture

### Command 1: `ingest` (streaming, per-file)

Parses sources and persists per-document artifacts (markdown, chunks, images,
doc JSON) immediately as each file completes. No corpus-wide operations.

```
ingest data/papers/mvp50 --out data/corpora/mvp50 --parser docling

  [prepare_change_set]
          |
  [parse+persist file_1] --> markdown/doc_1.md, chunks/doc_1.jsonl, docs/doc_1.json
  [parse+persist file_2] --> markdown/doc_2.md, chunks/doc_2.jsonl, docs/doc_2.json
  ...                        (parallel via ProcessPoolExecutor)
  [parse+persist file_N] --> markdown/doc_N.md, chunks/doc_N.jsonl, docs/doc_N.json
          |
  [update_manifest + remove_stale]
          |
  DONE (corpus has per-doc artifacts, no derived)
```

**What changes:**
- Merge `_parse_worker` + per-bundle persist into `_parse_and_persist_worker`
- Worker writes to disk atomically, returns lightweight `FileReceipt` (not full bundle)
- `_ParsedBundle` never leaves the worker process
- `raw_markdown_by_id` dict eliminated (read from disk when needed)
- `extract_citations`, `link_chunks_to_images`, `sections_from_chunks` move into worker

**FileReceipt** (returned from worker, ~200 bytes each):
```python
@dataclass
class FileReceipt:
    src_path: str
    doc_id: str
    n_chunks: int
    declared_keywords: list[str]
    parse_seconds: float
```

### Command 2: `refresh` (corpus-wide, DAG-parallel)

Reads persisted per-doc artifacts from disk, runs all derived operations. Can
be called independently after any number of ingests.

```
refresh data/corpora/mvp50

  [load_active_corpus]  --> all_docs, all_chunks from disk
          |
  [embed_chunks]        --> VectorStore
          |
  +-------+---------------+-----------+
  |       |               |           |
doc_edges topics    images_index   bibtex       <-- WAVE A (parallel)
  |
corpus_graph                                    <-- WAVE B (needs edges)
  |
  +-------+-----------+
  |       |           |
explorer pagerank  doc_resave                   <-- WAVE C (parallel)
```

**What changes:**
- Extract current `_rebuild_derived` logic into `refresh_corpus(paths)` public function
- New CLI command: `uv run python -m wikify.cli refresh --corpus data/corpora/mvp50`
- Batch waves A/B/C run via `ThreadPoolExecutor(max_workers=4)`
- `ingest` optionally calls `refresh` at the end (flag `--no-refresh` to skip)

### DAG Dependency Graph (Formal)

```
Layer 0 (setup):
  prepare_change_set -> {change_set, manifest}

Layer 1 (streaming, parallel per-file):
  for each file: parse_and_persist -> FileReceipt
  BARRIER: all files done

Layer 2 (bookkeeping, sequential):
  identify_stale -> update_manifest -> remove_stale -> save_manifest

Layer 3 (refresh, optional after ingest):
  load_active_corpus -> embed_chunks
  -> WAVE A: {doc_edges, topics, images_index, bibtex}  [parallel]
  -> WAVE B: corpus_graph                                [after doc_edges]
  -> WAVE C: {explorer_index, pagerank, doc_resave}      [parallel, after graph]
```

## Files to Modify

| File | Change |
|------|--------|
| `src/wikify/ingest/pipeline.py` | Refactor `ingest_corpus`, add `refresh_corpus`, new `_parse_and_persist_worker`, `_run_batch_waves`, `FileReceipt`. Remove `_persist_bundles`. |
| `src/wikify/cli.py` | Add `refresh` command. Add `--no-refresh` flag to `ingest`. |
| `src/wikify/store/corpus.py` | Verify `_read_body_from_doc_markdown` handles raw markdown files (no frontmatter). |

## Key Design Decisions

1. **Worker does all per-file I/O**: Parse, chunk, save images, write
   doc/chunks/markdown. Returns only a receipt.
2. **No `raw_markdown_by_id` cache**: `_resave_docs` reads raw body from disk.
   Already-persisted markdown files work fine with `_read_body_from_doc_markdown`
   (strips frontmatter if present, returns body as-is if not).
3. **Manifest saved between ingest and refresh**: If refresh crashes, re-run
   `refresh` without re-parsing. Manifest records which files are ingested.
4. **`ingest` calls `refresh` by default**: `--no-refresh` skips derived rebuild
   (useful when ingesting batches of files incrementally before one final refresh).
5. **Thread pool for batch waves**: Python threads are fine here -- most batch
   ops release the GIL (numpy, I/O). True CPU contention is minimal.

## Crash Recovery

- **Crash during Layer 1**: Already-persisted docs are safe on disk. Manifest
  not yet updated, so re-run re-parses everything (idempotent -- atomic writes
  overwrite cleanly).
- **Crash during Layer 2**: Manifest not saved, so re-run re-does bookkeeping.
- **Crash during Layer 3 (refresh)**: Manifest IS saved (from Layer 2). Re-run
  detects no new files, skips straight to refresh. Only derived artifacts need
  rebuilding.

## Verification

1. `uv run pytest tests/wikify -q` -- all existing tests pass
2. Streaming: `wikify.cli ingest ... --no-refresh` -- files appear on disk
   incrementally, no derived artifacts
3. Refresh: `wikify.cli refresh --corpus ...` -- derived artifacts built
4. Combined: `wikify.cli ingest ...` -- full pipeline, same output as before
5. Compare corpus stats between old and new pipeline outputs
