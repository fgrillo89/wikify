# Streaming Ingest Pipeline Refactor

Branch: `streaming-pipeline` (off `docling-parsers`)

## Problem

`ingest_corpus()` in `src/wikify/ingest/pipeline.py` holds ALL parsed
bundles in memory before persisting, then runs corpus-wide operations
sequentially. This wastes memory, hides progress, loses work on crash,
and serializes independent batch operations.

## Goal

Split ingest into two commands with a clean boundary:
- `ingest`: streaming per-file parse+persist (Layer 1-2)
- `refresh`: corpus-wide derived rebuild with DAG parallelism (Layer 3)

## Architecture

```
ingest data/papers/mvp50 --out data/corpora/mvp50 --parser docling

  Layer 0: prepare_change_set
  Layer 1: for each file (parallel):
             parse -> chunk -> save images -> build Document
             -> write markdown/, chunks/, docs/ atomically
             -> return FileReceipt (lightweight)
  Layer 2: identify_stale -> update_manifest -> remove_stale -> save_manifest
  Layer 3 (optional, --no-refresh to skip):
             refresh_corpus(paths)

refresh data/corpora/mvp50

  load_active_corpus from disk -> all_docs, all_chunks
  embed_chunks (incremental)
  WAVE A (parallel): doc_edges | topics | images_index | bibtex
  WAVE B (after doc_edges): corpus_graph
  WAVE C (parallel, after graph): explorer_index | pagerank | doc_resave
```

## Implementation Steps

### Step 1: Add FileReceipt dataclass

Location: `src/wikify/ingest/pipeline.py`, after `_ParsedBundle`

```python
@dataclass
class FileReceipt:
    """Lightweight result from a parse+persist worker.

    Only carries identifiers and stats -- the full document, markdown,
    and chunks are already persisted to disk.
    """
    src_path: str
    doc_id: str
    n_chunks: int
    declared_keywords: list[str]
    parse_seconds: float
```

### Step 2: Create _parse_and_persist_worker

Location: `src/wikify/ingest/pipeline.py`, replace `_parse_worker`

This function does everything the old `_parse_worker` + the per-bundle
loop body of `_persist_bundles` did, then returns a `FileReceipt`.

```python
def _parse_and_persist_worker(
    src_str: str,
    corpus_root_str: str,
    parser_backend: str = "default",
) -> FileReceipt:
```

Logic (copy from existing code, merge):
1. Parse file: `kind, parsed = parse_file(src, parser_backend=backend)`
2. Compute doc_id: `did = doc_id_for(src)`
3. Save images: `save_doc_images(did, image_dir_path, parsed.raw_images)`
4. Chunk: `_chunks_from_docling()` or `chunk_document()` + `caption_chunks_for()`
5. Extract equations, figure refs, bind to chunks
6. **NEW** - extract citations: `citations = extract_citations(parsed.markdown, did)`
7. **NEW** - link images: `near_map = link_chunks_to_images(chunks, parsed.images)` + `rewrite_sidecar_near_chunks()`
8. **NEW** - build sections: `sections = sections_from_chunks(chunks)`
9. **NEW** - build Document object (same fields as current `_persist_bundles`)
10. **NEW** - write to disk: `write_document(paths, doc, parsed.markdown, chunks)`
11. Return `FileReceipt(src_path, did, len(chunks), keywords, elapsed)`

Key: `CorpusPaths` must be reconstructed inside the worker from
`corpus_root_str` since it crosses a process boundary:
`paths = CorpusPaths(root=Path(corpus_root_str))`

### Step 3: Create _stream_parse_and_persist

Location: `src/wikify/ingest/pipeline.py`, replace `_parse_sources`

```python
def _stream_parse_and_persist(
    sources: list[Path],
    paths: CorpusPaths,
    max_workers: int | None,
    parser_backend: str = "default",
) -> list[FileReceipt]:
```

For pymupdf/default backend: `ProcessPoolExecutor` with N workers,
each calling `_parse_and_persist_worker`. Files persist as they complete.

For docling backend: serial (workers=1) since GPU model should not be
duplicated across processes. Future optimization: `convert_all()` batch
mode in a single process.

Print progress as each file completes:
`[ingest] [3/50] 14.2s  paper_name.pdf`

Print slowest-paper report at end (same as current).

### Step 4: Update _identify_stale_docs for FileReceipt

Current signature takes `bundles: list[_ParsedBundle]`. Change to
`receipts: list[FileReceipt]`.

The only field used from bundles is `src_path` (to look up sid).
`FileReceipt.src_path` provides the same data. Minimal change.

### Step 5: Update _update_manifest for FileReceipt

Current signature takes `bundles: list[_ParsedBundle]`. Change to
`receipts: list[FileReceipt]`.

Current code accesses:
- `bundle.src_path` -> `receipt.src_path`
- `bundle.doc_id` -> `receipt.doc_id`
- `bundle.chunks` (for chunk_ids) -> read from disk: load chunk ids
  from `chunks/{doc_id}.jsonl` (just the "id" field, don't load text)

Add a helper to read just chunk ids from a JSONL file:

```python
def _read_chunk_ids(paths: CorpusPaths, doc_id: str) -> list[str]:
    p = paths.chunks_dir / f"{doc_id}.jsonl"
    if not p.exists():
        return []
    ids = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            ids.append(json.loads(line)["id"])
    return ids
```

### Step 6: Extract refresh_corpus with parallel waves

Location: `src/wikify/ingest/pipeline.py`, new public function

```python
def refresh_corpus(
    paths: CorpusPaths,
    *,
    stale_doc_ids: set[str] | None = None,
) -> None:
```

Implementation:
1. Load active corpus: `list_documents(paths)`, `all_chunks(paths)`
2. Build pairs, collect declared keywords
3. Embed chunks (incremental, sequential -- same as current)
4. **WAVE A** (ThreadPoolExecutor, max_workers=4):
   - `populate_doc_edges(all_docs, all_pairs, store)`
   - `extract_topics(all_pairs, declared) -> write_topics()`
   - `build_images_index(paths, doc_ids)`
   - `write_corpus_bibliography(paths, all_docs)`
   Wait for `doc_edges` future specifically.
5. **WAVE B** (after doc_edges):
   - `build_corpus_graph(all_docs, all_chunks, store) -> write_graph()`
   Wait for graph result.
6. **WAVE C** (ThreadPoolExecutor):
   - `build_explorer_index() -> save_explorer_index()`
   - `write_pagerank()`
   - `_resave_docs(paths, all_docs, {})` (empty dict -- reads body from disk)
   Wait for all.

Thread safety note: `populate_doc_edges` mutates `doc.similar_to`,
`.cites`, `.cites_same`. Other WAVE A tasks only read `.id`, `.title`,
`.metadata`, `.images`, `.citations` -- no conflict. WAVE B/C run
after the barrier so they see the mutations.

### Step 7: Update _resave_docs to not need raw_markdown_by_id

The `raw_markdown_by_id` parameter becomes unnecessary. `_resave_docs`
already has a fallback that reads from disk via `_read_body_from_doc_markdown`.
Change signature to drop the dict, always read from disk.

`_read_body_from_doc_markdown` already handles both cases:
- Raw markdown (no frontmatter) from fresh ingest -> returns as-is
- Enriched markdown (with frontmatter + edges) from prior runs -> strips both

### Step 8: Update ingest_corpus

```python
def ingest_corpus(
    input_dir: Path,
    output_dir: Path,
    *,
    max_workers: int | None = None,
    mode: str = "additive",
    parser_backend: str = "default",
    refresh: bool = True,
) -> CorpusPaths:
```

New flow:
1. `_prepare_change_set()` (unchanged)
2. `_stream_parse_and_persist()` (replaces `_parse_sources` + `_persist_bundles`)
3. `_identify_stale_docs(receipts, ...)` (updated for FileReceipt)
4. `_update_manifest(receipts, ...)` (updated for FileReceipt)
5. Remove stale artifacts (unchanged)
6. Save manifest (moved here -- before refresh)
7. If `refresh`: `refresh_corpus(paths, stale_doc_ids=stale_doc_ids)`

### Step 9: Add refresh CLI command

Location: `src/wikify/cli.py`

```python
@app.command()
def refresh(
    corpus_dir: Path = typer.Argument(...),
) -> None:
    """Rebuild derived artifacts (embeddings, graph, topics, etc.)."""
    from wikify.ingest.pipeline import refresh_corpus
    from wikify.paths import CorpusPaths
    paths = CorpusPaths(root=corpus_dir)
    refresh_corpus(paths)
```

Add `--no-refresh` flag to `ingest`:
```python
def ingest(
    ...
    no_refresh: bool = typer.Option(False, "--no-refresh"),
) -> None:
    ...
    ingest_corpus(..., refresh=not no_refresh)
```

### Step 10: Delete dead code

Remove after all steps are working:
- `_ParsedBundle` dataclass (replaced by FileReceipt)
- `_parse_worker` function (replaced by _parse_and_persist_worker)
- `_parse_sources` function (replaced by _stream_parse_and_persist)
- `_persist_bundles` function (merged into worker)
- `_rebuild_derived` function (replaced by refresh_corpus)

Only delete after tests pass. Keep `_ParsedBundle` temporarily if tests
reference it directly.

## Files Changed

| File | Changes |
|------|---------|
| `src/wikify/ingest/pipeline.py` | Steps 1-8, 10. Major refactor. |
| `src/wikify/cli.py` | Step 9. Add `refresh` command, `--no-refresh` flag. |

## Test Strategy

1. `uv run pytest tests/wikify -q` -- all existing tests must pass
   (the test suite exercises `ingest_corpus` end-to-end)
2. Manual: `ingest ... --no-refresh` -> verify per-doc files appear
   on disk, no derived artifacts (no vectors.npz, graph.json, etc.)
3. Manual: `refresh --corpus ...` -> verify derived artifacts appear
4. Manual: `ingest ...` (with refresh) -> same output as old pipeline
5. Compare chunk counts and doc counts between old and new runs

## Blast Radius

- `ingest_corpus()` signature gains `refresh: bool = True` (backward compatible)
- `refresh_corpus()` is new public API
- `_resave_docs` loses `raw_markdown_by_id` parameter
- `_identify_stale_docs` and `_update_manifest` change from `_ParsedBundle`
  to `FileReceipt` (internal, not public API)
- No changes to models, store layer, parsers, chunker, or any other module
- CLI gains one new command (`refresh`) and one new flag (`--no-refresh`)

## Lessons from ingest-refactor-plan.md

The prior refactor (phases A-C, done) established key principles that
this streaming refactor must preserve:

1. **Source records are incremental; corpus indexes are reproducible.**
   The ingest/refresh split makes this explicit: `ingest` produces
   source records (per-doc artifacts), `refresh` rebuilds indexes.

2. **Atomic writes for all ingest outputs.** Already done (tempfile +
   os.replace). The streaming worker must use the same `atomic_write_text`
   and `write_document` functions -- no new write paths.

3. **Manifest is the correctness primitive.** The manifest tracks what's
   been ingested. Saving it between ingest and refresh is the crash
   recovery boundary. Don't mutate manifest during Layer 1 (parsing).

4. **Code that changes together lives together.** The worker merges
   parse + chunk + enrich + persist into one function because they
   always run together per-file. Don't split them back apart.

5. **Name by what it does.** `refresh_corpus` rebuilds derived artifacts.
   `_parse_and_persist_worker` parses and persists. `FileReceipt` is
   the receipt from a completed file, not a "result" or "output."

6. **Docling is a first-class parser (Phase D item 17 is now triggered).**
   The GPU batching path (`convert_all`) is docling-specific and belongs
   in the parser module or a dedicated streaming function, not in the
   generic pipeline orchestrator. The pipeline should dispatch based on
   backend capabilities, not hardcode backend-specific logic.

7. **Don't introduce new abstractions ahead of need.** The prior plan's
   Phase D deferred `CorpusReader` protocol, `DocType` classification,
   and alternative store backends. This refactor similarly should NOT
   add: enrichment stage protocols, store abstraction layers, or
   pipeline plugin systems. Just split ingest/refresh and parallelize.
