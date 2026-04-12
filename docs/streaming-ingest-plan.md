# Streaming Ingest Pipeline Refactor

## Context

The current ingest pipeline holds ALL parsed bundles in memory before
persisting anything, then runs corpus-wide operations (embeddings, graph,
topics) in sequence. Problems:
- No visibility into progress until everything is parsed
- Memory scales linearly with corpus size (all _ParsedBundles in RAM)
- A crash mid-parse loses all work
- Slow parsers (docling ~15-30s/paper with GPU) block everything
- Corpus-wide ops run sequentially even when independent

Docling is becoming the first-class parser (layout analysis, formula
extraction, hybrid chunking, image classification). It's heavier than
pymupdf4llm but produces richer output. The pipeline must handle both
fast (pymupdf, ~2s/paper) and slow (docling, ~15s/paper GPU) backends
efficiently.

## Design: Two-Command Architecture

### Command 1: `ingest` (streaming, per-file)

Parses sources and persists per-document artifacts immediately as each
file completes. No corpus-wide operations.

```
ingest data/papers/mvp50 --out data/corpora/mvp50 --parser docling

  [prepare_change_set]
          |
  [parse+persist file_1] --> markdown/1.md, chunks/1.jsonl, docs/1.json, images/1/
  [parse+persist file_2] --> markdown/2.md, chunks/2.jsonl, docs/2.json, images/2/
  ...                        (parallel via ProcessPoolExecutor)
  [parse+persist file_N] --> ...
          |
  [update_manifest + remove_stale]
          |
  DONE (corpus has per-doc artifacts, no derived)
```

For **docling with GPU**: a single process holds the GPU model and uses
`convert_all()` to batch pages across documents through the layout model.
Parallelism comes from GPU batch inference, not multiple worker processes.
The worker persists each document's artifacts as soon as its conversion
completes from the `convert_all()` iterator.

For **default (pymupdf)**: ProcessPoolExecutor with N workers, each
parsing independently. Same as today but persisting per-file instead
of accumulating.

**What changes:**
- Merge `_parse_worker` + per-bundle persist into `_parse_and_persist_worker`
- Worker writes to disk atomically, returns lightweight `FileReceipt`
- `_ParsedBundle` never leaves the worker scope
- `raw_markdown_by_id` dict eliminated (read from disk when needed)
- `extract_citations`, `link_chunks_to_images`, `sections_from_chunks`
  move into the worker
- For docling: single-process mode using `convert_all()` for GPU batching,
  with streaming persist as each result arrives

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

Reads persisted per-doc artifacts from disk, runs all derived operations.
Can be called independently after any number of ingests.

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
- Extract `_rebuild_derived` into public `refresh_corpus(paths)` function
- New CLI: `uv run python -m wikify.cli refresh --corpus data/corpora/mvp50`
- Waves A/B/C via `ThreadPoolExecutor(max_workers=4)`
- `ingest` calls `refresh` by default; `--no-refresh` to skip

### DAG Dependency Graph (Formal)

```
Layer 0 (setup):
  prepare_change_set -> {change_set, manifest}

Layer 1 (streaming, parallel per-file):
  For pymupdf: ProcessPoolExecutor, one file per worker
  For docling: single process, convert_all() with GPU batching
  Each file: parse -> chunk -> persist -> FileReceipt
  BARRIER: all files done

Layer 2 (bookkeeping, sequential):
  identify_stale -> update_manifest -> remove_stale -> save_manifest

Layer 3 (refresh, separate command or auto after ingest):
  load_active_corpus -> embed_chunks
  -> WAVE A: {doc_edges, topics, images_index, bibtex}  [parallel]
  -> WAVE B: corpus_graph                                [after doc_edges]
  -> WAVE C: {explorer_index, pagerank, doc_resave}      [parallel, after graph]
```

## Files to Modify

| File | Change |
|------|--------|
| `src/wikify/ingest/pipeline.py` | Refactor `ingest_corpus`, add `refresh_corpus`, `_parse_and_persist_worker`, `_stream_docling_batch`, `_run_batch_waves`, `FileReceipt`. Remove `_persist_bundles`. |
| `src/wikify/cli.py` | Add `refresh` command. Add `--no-refresh` flag to `ingest`. |
| `src/wikify/store/corpus.py` | Verify `_read_body_from_doc_markdown` handles raw markdown (no frontmatter). |

## Key Design Decisions

1. **Worker does all per-file I/O**: Parse, chunk, save images, write
   doc/chunks/markdown. Returns only a receipt.
2. **Docling gets single-process GPU path**: `convert_all()` batches
   pages across documents for GPU throughput. Multi-process would
   duplicate the GPU model in each worker (wasting VRAM).
3. **No `raw_markdown_by_id` cache**: `_resave_docs` reads raw body
   from disk. Already-persisted markdown files work with
   `_read_body_from_doc_markdown`.
4. **Manifest saved between ingest and refresh**: If refresh crashes,
   re-run `refresh` without re-parsing.
5. **`ingest` calls `refresh` by default**: `--no-refresh` skips
   derived rebuild (useful when ingesting batches incrementally).
6. **Thread pool for batch waves**: Python threads are fine -- most
   batch ops release the GIL (numpy, I/O).

## Crash Recovery

- **Layer 1 crash**: Already-persisted docs safe. Manifest not updated,
  so re-run re-parses (idempotent via atomic writes).
- **Layer 2 crash**: Manifest not saved, re-run re-does bookkeeping.
- **Layer 3 crash (refresh)**: Manifest IS saved. Re-run detects no
  new files, skips to refresh.

## Verification

1. `uv run pytest tests/wikify -q` -- all tests pass
2. Streaming: `ingest ... --no-refresh` -- files appear incrementally
3. Refresh: `refresh --corpus ...` -- derived artifacts built
4. Combined: `ingest ...` -- full pipeline, same output as before
5. Compare corpus stats between old and new pipeline outputs
