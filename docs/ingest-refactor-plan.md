# Ingest Pipeline Refactoring -- Design Brief

## Status (2026-04-12)

Completed:
- **P2 (parser abstraction)**: `RawImage` is typed (not `metadata["_raw_images"]`).
  `ParserBackend` enum + unified `_PARSER_TABLE` dispatch. Alternative parsers
  (e.g. docling) register via one table row + one module. CLI: `--parser default|docling`.
- **Incremental ingest**: manifest-based dedup, replacement-before-delete safety,
  alias dedup, cross-run dedup. Old `_dedupe_sources` / `_existing_corpus_hashes`
  removed.
- **Pipeline renamed**: `refresh.py` -> `pipeline.py`.

Remaining (not in scope for this pass):
- P1 (god function): pipeline.py is ~650L, staged but still one function.
- P3 (enrichment protocol): explicit typed calls, no shared protocol yet.
- P4 (storage adapter): still file-based, not protocol-backed.
- P5 (distill coupling): implicit shapes, no corpus reader protocol.
- P6 (scattered config): partially addressed; SKIP_SECTION_TYPES still duped.

## Lessons from the distill consolidation

The distill refactor eliminated 6 directories, ~1300 lines of dead code, and renamed concepts to match what they actually do. The principles that worked:

### 1. Names are organizing principles, not labels

Renaming `Sampler` to `Explorer` wasn't cosmetic -- it moved the action dispatch into the explorer module because "the explorer owns the action vocabulary." Renaming `Schedule` to `BudgetAllocator` made clear it manages money, not time. Renaming `Policy` to `RunMode` revealed it's just a choice between scripted and guided execution.

**For ingest**: name each component by what it does in the pipeline, not by the implementation technique. `refresh.py` doesn't "refresh" -- it ingests. `_clean.py` doesn't "clean" -- it strips structural noise. `figures.py` doesn't handle "figures" -- it extracts media binaries from PDFs.

### 2. Code that changes together should live together

The distill refactor merged `schedule.py` (52L) + `registry.py` (82L) + `policy.py` (406L) into `strategy.py` (~470L) because they all answer "how does a run behave?" Separately, they required jumping between files to understand the strategy abstraction.

**For ingest**: the 10 stages in `refresh.py` are sequential but their helpers are scattered across 15 files. `equations.py`, `figure_refs.py`, `figures.py`, and `images.py` all deal with "extracting non-text content from documents" but live as separate modules. `citations.py` and `coupling.py` both deal with "inter-document relationships."

### 3. One data table + one factory over scattered modules

The strategy configs are 3 rows in one dict, not 3 files. The dispatch classes collapsed from 6 into 1 because the pattern was identical.

**For ingest**: the parser registry is already a single dispatch function -- good. But the post-parse enrichment (equations, figures, figure_refs, citations, metadata) is 5 separate modules with no explicit stage boundary. Each returns a different shape of dict. Typed stage functions and an applicability table would let new enrichments (e.g. docling's table extraction) slot in without turning `refresh.py` into the control-flow bottleneck.

### 4. Separate definition from usage

Enums, protocols, and constants moved to `types.py` and `config.py` at the package root. Business logic imports from definitions, never the reverse.

**For ingest**: `ingest/config.py` (33L) is already clean. But `SKIP_SECTION_TYPES` is defined in `distill/dossier.py` and duplicated in `ingest/explorer_index.py` -- a definition that should live in one place.

### 5. Delete what's superseded in the same change

The heuristic binding (529L), fake binding (moved to tests), and 3 `__init__.py` files were all dead weight. The `ALLOW_NETWORK` env var was a guard for a world that no longer existed.

**For ingest**: identify anything that exists "just in case" or "for the old architecture."

---

## Current ingest architecture

### What it does (10 stages)

```
Input files (pdf/docx/pptx/html/md)
  |
  v
1. Enumerate + dedupe (by SHA1 content hash)
2. Parse + chunk (parallel, ProcessPoolExecutor)
   - parse_file(path) -> ParseResult (markdown, sections, metadata, raw_images)
   - chunk_document(markdown, sections) -> list[Chunk]
   - extract equations, figure_refs (bound to chunks by char_span)
3. Per-doc persist (markdown, chunks, images, sidecars)
4. Embed all chunks (fastembed or hash backend)
5. Doc-level edges (citations, similarity, coupling)
6. Corpus graph (7 edge types)
7. Explorer index (pre-computed sampler state)
8. PageRank (on doc graph)
9. Topics, image index, bibtex
10. Re-save documents (with populated edges)
```

### What it produces

```
corpus/
  docs/{id}.json          Document records
  chunks/{doc_id}.jsonl   Chunks (one per line)
  markdown/{id}.md        Source markdown
  images/{slug}/          Binaries + JSON sidecars
  vectors.npz             Chunk embeddings
  vectors.meta.json       Embedder metadata
  graph.json              Typed multi-edge corpus graph
  explorer_index.json     Pre-computed distill state
  pagerank.json           Doc-level PageRank scores
  topics.json             Topic vocabulary
  images.json             Image index
  library.bib             BibTeX export
```

### File inventory (5,469 LOC)

| File | Lines | Role |
|------|-------|------|
| refresh.py | 699 | Pipeline orchestration |
| metadata.py | 705 | Title/author/year/DOI/summary extraction |
| images.py | 430 | Image persistence + caption chunking |
| topics.py | 422 | Topic vocabulary extraction |
| figures.py | 337 | PDF media binary extraction |
| equations.py | 301 | Equation extraction from markdown |
| chunker.py | 224 | Markdown -> chunks |
| citations.py | 225 | Reference section parsing |
| figure_refs.py | 134 | Inline caption extraction |
| explorer_index.py | 123 | Pre-computed distill state |
| corpus_graph.py | 112 | Graph construction |
| section_classifier.py | 98 | Heading -> section type |
| coupling.py | 92 | Bibliographic coupling |
| bibtex.py | 84 | BibTeX export |
| config.py | 33 | Constants |
| parsers/ (7 files) | 1,206 | Format-specific parsing |

---

## Problems identified

### P1: refresh.py is a 700-line god function

The 10-stage pipeline is one function with local variables threading state between stages. Adding a new stage means editing the god function. Testing one stage means running the whole pipeline.

### P2: No parser abstraction beyond ParseResult

Parsers return `ParseResult(markdown, sections, images, metadata, title)` which is good, but:
- Raw images are smuggled through `metadata["_raw_images"]` (no type safety)
- Each parser hardcodes its library imports (pymupdf, python-docx, etc.)
- No way to register an alternative parser (e.g. docling) for the same format without editing `registry.py`
- No parser-level configuration (e.g. OCR threshold, image extraction policy)

### P3: Post-parse enrichment has no shared interface

Five separate modules (equations, figure_refs, figures, citations, metadata) each:
- Take different inputs (markdown text, parsed sections, raw image bytes, PDF metadata)
- Return different shapes (list of dicts, structured objects, raw strings)
- Are called at different points in refresh.py
- Have no common protocol

Adding a new enrichment stage (e.g. docling's table extraction, or a model-based metadata extractor) requires modifying refresh.py's control flow.

### P4: Storage is hardcoded everywhere

- `Document.markdown_path` stores absolute paths (corpus not relocatable)
- `store/corpus.py` assumes glob patterns on JSON/JSONL files
- VectorStore assumes numpy `.npz` format
- Image sidecars assume `{binary_path}.json` naming
- 12 different file formats across 14 artifacts

Swapping to SQLite/ChromaDB/LanceDB would require touching ~10 files.

### P5: Tight coupling between ingest output and distill input

Distill's `preload_corpus()` assumes:
- Documents as JSON files in `docs/`
- Chunks as JSONL in `chunks/`
- Vectors as `.npz`
- Graph as `graph.json`
- Explorer index as `explorer_index.json`
- Images as sidecars in `images/`

The shapes are implicit -- there's no protocol for "a corpus reader."

### P6: Scattered configuration

- `ingest/config.py`: chunking, media, graph constants
- `config.py` (root): `CHARS_PER_TOKEN` used by the chunker via token counting
- `SKIP_SECTION_TYPES` in `distill/dossier.py` AND `ingest/explorer_index.py`
- Embedder selection via env var `WIKIFY_EMBEDDER`
- Parser dispatch via file extension (hardcoded match statement)

---

## Proposed refactoring

### Principle: source records are incremental; corpus indexes are reproducible

The pipeline should distinguish durable source records from derived corpus
indexes:

- **Source artifacts are incremental**: parse, media extraction, chunking, and
  embedding should run only for new or changed source files.
- **Global corpus artifacts are reproducible**: graph, citation resolution,
  bibliographic coupling, PageRank, topics, explorer index, image index, and
  BibTeX should be rebuilt from the active source records unless a later phase
  proves a partial update is both correct and faster.

This mirrors the practical pattern used by modern RAG ingestion systems:
track stable source ids + content hashes, skip unchanged inputs, upsert changed
leaf records, and rebuild or republish global indexes from a consistent record
set. Keep that pattern simple here: a file-based manifest first, optional
database/vector backends later.

The pipeline should still be a sequence of typed stages, each with explicit
inputs and outputs. Storage should be a thin adapter around corpus-level
operations, not a protocol that leaks JSON/JSONL/NPZ sidecar details.

### New module structure

```
src/wikify/ingest/
  pipeline.py           Orchestrates stages (was refresh.py, slimmed to ~200L)
  config.py             All ingest constants (expanded, absorbs scattered defs)

  # --- Parsing ---
  parse/
    types.py            ParseResult + RawImage dataclass (typed, not dict)
    registry.py         Parser protocol + dispatch table
    pdf.py              pymupdf4llm parser
    docx.py             python-docx parser
    pptx.py             python-pptx parser
    html.py             trafilatura parser
    markdown.py         plain markdown parser
    clean.py            structural noise stripping (was _clean.py)
    sections.py         section span detection (was _sections.py)

  # --- Enrichment / typed stages ---
  enrich/
    stages.py           Stage applicability table + shared typed stage helpers
    equations.py        Equation extraction
    media.py            Figure binary extraction + image persistence (was figures.py + images.py)
    captions.py         Inline caption extraction (was figure_refs.py)
    citations.py        Reference parsing + resolution
    metadata.py         Title/author/year/DOI/summary
    topics.py           Topic vocabulary

  # --- Corpus assembly ---
  assemble.py           Chunking + section classification (was chunker.py + section_classifier.py)
  graph.py              Corpus graph + coupling + doc edges (was corpus_graph.py + coupling.py)
  index.py              Explorer index + PageRank (was explorer_index.py + pagerank logic)

  # --- Storage / manifest ---
  store/
    manifest.py         SourceRecord + CorpusManifest
    filesystem.py       FileSystemStore with staging, validation, atomic publish
    reader.py           CorpusReader protocol for distill preload
```

### Key abstractions

#### 0. Corpus manifest / record manager

Before introducing alternative storage backends, ingest needs a small local
record manager. This is the correctness and speed primitive for incremental
ingest.

```python
@dataclass
class SourceRecord:
    source_id: str                 # stable identity for this source
    source_path: str
    content_hash: str              # sha1/sha256 of source bytes
    doc_id: str
    status: Literal["active", "deleted"]
    parser_fingerprint: str
    chunker_fingerprint: str
    embedder_fingerprint: str
    chunk_ids: list[str]
    parsed_at: str

@dataclass
class CorpusManifest:
    schema_version: int
    corpus_id: str
    sources: dict[str, SourceRecord]
    last_successful_ingest: str
```

The manifest enables three ingest modes:

| Mode | Meaning | Deletes missing sources? |
|------|---------|--------------------------|
| `additive` | Add new/changed files, leave old sources alone | no |
| `sync` | Make the corpus match the input source set | yes, via tombstones |
| `rebuild` | Reprocess every active source from scratch | optional |

The manifest also guards migration boundaries:
- if the embedder fingerprint changes, fail unless `--rebuild-vectors` or
  `--rebuild` is requested;
- if parser/chunker fingerprints change, assume chunk ids and derived caches
  are invalid and require a rebuild;
- if a previous ingest left a staging directory behind, recover or discard it
  before writing new artifacts.

#### 1. Parser protocol (extensible for docling)

```python
class DocumentParser(Protocol):
    def parse(self, path: Path) -> ParseResult: ...
    def supported_formats(self) -> set[str]: ...

@dataclass
class ParseResult:
    markdown: str
    sections: list[SectionSpan]
    raw_images: list[RawImage]      # typed, not metadata["_raw_images"]
    metadata: dict
    title: str

@dataclass
class RawImage:
    data: bytes | None = None
    url: str | None = None
    ext: str = "png"
    caption: str = ""
    label: str | None = None
    page: int | None = None
```

A docling parser would implement the same protocol:
```python
class DoclingParser:
    def parse(self, path: Path) -> ParseResult:
        result = docling.convert(path)
        return ParseResult(
            markdown=result.to_markdown(),
            sections=extract_sections(result),
            raw_images=extract_images(result),
            metadata=result.metadata,
            title=result.title,
        )
    def supported_formats(self) -> set[str]:
        return {"pdf", "docx", "pptx", "html"}
```

Parser registration becomes a data table:
```python
PARSERS: dict[str, DocumentParser] = {
    "pdf": PymupdfParser(),
    "docx": DocxParser(),
    ...
}
# Override for docling:
# PARSERS["pdf"] = DoclingParser()
```

#### 2. Typed ingest stages before a generic enricher protocol

The enrichment modules do not all have the same natural input shape:
equations and figure refs are markdown-only, PDF media extraction needs parser
or PDF-level state, image persistence needs binary blobs and paths, citations
need reference text, and metadata needs source + parsed text. Forcing these
into one `enrich(doc, chunks, markdown)` protocol would make the interface too
generic too early.

Use explicit stage functions first:

```python
raw = parse_source(source, parser)
chunks = chunk_source(raw.markdown, raw.sections)
equations = extract_equations(raw.markdown)
figure_refs = extract_figure_refs(raw.markdown)
images = persist_images(raw.raw_images, source, paths)
citations = extract_citations(raw.markdown, doc_id)
metadata = extract_metadata(source, raw)
```

If two or more stages later converge on the same input/output shape, introduce
a protocol for that narrower family. Until then, explicit typed calls are
clearer, easier to profile, and less likely to hide scale costs.

#### 3. Corpus store adapter (storage-agnostic later)

The first storage abstraction should be higher-level than the current files:
read/write documents, chunks, vectors, graph, images, manifest, and derived
indexes. It should not expose JSON/JSONL/NPZ/sidecar mechanics as protocol
methods.

Start with a concrete `FileSystemStore(CorpusPaths)` that owns staging,
validation, and atomic publish. Add `CorpusReader` for distill first:

```python
class CorpusReader(Protocol):
    def list_documents(self) -> list[Document]: ...
    def read_chunks(self, doc_id: str) -> list[Chunk]: ...
    def read_all_chunks(self) -> list[Chunk]: ...
    def read_vectors(self) -> VectorStore: ...
    def read_graph(self) -> CorpusGraph: ...
    def read_manifest(self) -> CorpusManifest: ...
```

A writer protocol can wait until the filesystem implementation stabilizes.
SQLite/LanceDB/Qdrant/Chroma can be added later if exact file-based ingest
misses scale targets.

#### 4. Pipeline as stage composition

```python
def ingest(
    input_dir: Path,
    store: FileSystemStore,
    parsers: dict[str, DocumentParser] | None = None,
    embedder: Callable | None = None,
) -> CorpusPaths:
    parsers = parsers or DEFAULT_PARSERS
    embedder = embedder or default_embedder()

    manifest = store.read_manifest()
    change_set = diff_sources(input_dir, manifest)
    with store.staging_ingest() as staged:
        changed = process_changed_sources(change_set, parsers)
        staged.write_source_artifacts(changed)

        docs, chunks = staged.read_active_corpus(manifest, change_set)
        vectors = staged.merge_vectors(manifest, change_set, changed, embedder)
        graph = rebuild_derived_artifacts(staged, docs, chunks, vectors)
        staged.validate_invariants()
        staged.publish_atomically()
    ...
```

The pipeline is ~200 lines of orchestration, not 700. Each stage is a function call. Adding a stage means adding one line.

---

## Gap 1: Incremental ingest is broken

The current `ingest_corpus` deduplicates correctly (skips files whose SHA1 matches existing docs), but then **rebuilds embeddings, graph, explorer index, pagerank, and topics from only the new documents**. Existing corpus data is silently dropped from derived artifacts. This means adding 5 papers to a 100-paper corpus produces a graph with only 5 nodes.

### Required fix

The pipeline must distinguish two modes:

1. **Full ingest** (default for a fresh corpus): process all sources, build everything from scratch.
2. **Incremental ingest** (adding files to an existing corpus): parse only new sources, then **merge** them into existing artifacts.

Merge means:
- **Manifest**: diff source records by stable source id + content hash. New and
  changed sources are processed; unchanged sources are reused; deleted sources
  are tombstoned in `sync` mode.
- **Source artifacts**: write markdown/chunks/doc JSON/images for new or
  changed sources only. Remove or tombstone old artifacts for changed/deleted
  sources after the replacement artifacts validate.
- **Vectors**: load existing vectors, delete rows for changed/deleted sources,
  embed new chunks, append/upsert by deterministic chunk id, and save the
  merged store. Validate that vector ids exactly match active chunk ids.
- **Documents**: after rebuilding doc-level edges, re-save any existing
  document whose `similar_to`, `cites`, or `cites_same` fields changed. Do not
  special-case inbound citations: `Document.cites` is an outgoing edge list.
- **Derived artifacts**: rebuild graph, explorer index, PageRank, topics,
  images index, and BibTeX from all active docs/chunks/vectors.

The pipeline skeleton becomes:

```python
def ingest(input_dir, store, ...):
    manifest = store.read_manifest()
    change_set = diff_sources(input_dir, manifest, mode="additive")
    if change_set.is_empty:
        return

    with store.staging_ingest() as staged:
        new_bundles = parse_parallel(change_set.to_parse, parsers)
        staged.write_source_artifacts(new_bundles)

        active_docs = staged.read_active_documents(manifest, change_set)
        active_chunks = staged.read_active_chunks(active_docs)
        vectors = staged.merge_vectors(
            manifest=manifest,
            changed=change_set.changed_or_deleted,
            new_chunks=chunks_from(new_bundles),
            embedder=embedder,
        )

        # Rebuild derived artifacts from the full active corpus.
        populate_doc_edges(active_docs, active_chunks, vectors)
        graph = build_graph(active_docs, active_chunks, vectors)
        staged.write_derived_artifacts(active_docs, active_chunks, vectors, graph)
        staged.validate_invariants()
        staged.publish_atomically()
    ...
```

The key insight: **parse + chunk + media extraction + embedding are
incremental**, but **global derived artifacts must see the full active
corpus**. This is already implicit in the current code's rebuild order. The
refactor should make it explicit and safe.

### Atomicity and recovery

Incremental ingest must not publish a half-updated corpus. Write changed source
artifacts and all derived artifacts into a staging area, validate invariants,
then atomically promote. If an ingest crashes, the next run should either resume
or discard the incomplete staging directory before doing new work.

Minimum invariants:
- every active document has markdown, chunks, and doc JSON;
- every chunk id appears exactly once;
- vector ids exactly equal active chunk ids;
- graph nodes cover every active document and chunk;
- explorer/context indexes reference only active chunk ids;
- image index records reference active docs/chunks;
- `vectors.meta.json` matches the manifest embedder fingerprint.

---

## Gap 2: Academic-paper assumptions

The current enrichers assume scientific articles:
- `citations.py`: looks for a `## References` section with structured entries (author, year, DOI)
- `coupling.py`: computes bibliographic coupling from shared reference fingerprints
- `bibtex.py`: produces `@article{}` BibTeX entries
- `metadata.py`: extracts authors, year, DOI, venue, summary from academic metadata
- `figure_refs.py`: matches `Fig. N` / `Table N` patterns from scientific prose
- `equations.py`: extracts LaTeX and chemical formulas

For non-academic documents (notes, reports, emails, presentations, web captures), most of these return empty. The pipeline doesn't break -- it just produces a corpus with no citations, no coupling, no bibtex, and sparse metadata. But the derived artifacts (graph, explorer index) work less well because citation edges and doc-similarity edges are the only inter-document connections besides embedding similarity.

### Required fix: stages must be optional and document-type-aware

Stage applicability should be explicit data, not hidden in a generic protocol.

Default stage applicability:

| Stage | pdf (academic) | pdf (report) | docx | pptx | html | md |
|-------|---------------|--------------|------|------|------|-----|
| Metadata | yes | yes | yes | yes | yes | yes |
| Equations | yes | yes | no | no | no | yes |
| Captions | yes | yes | no | yes | no | no |
| Citations | yes | try if ambiguous | no | no | no | try if references |
| Coupling | yes | if citations exist | no | no | no | if citations exist |
| Topics | yes | yes | yes | yes | yes | yes |

The pipeline checks applicability against both `doc.kind` and `doc.doc_type`.
Ambiguous PDFs should prefer running academic enrichments and accepting empty
outputs over silently skipping citations. Non-academic documents can skip
citation/coupling when classification is confident and rely on embedding-based
similarity for inter-document edges. The graph still works -- it just has fewer
edge types.

Additionally, the `Document` model should support a richer `kind` vocabulary beyond file extension:

```python
DocKind = Literal["pdf", "docx", "pptx", "html", "md"]
DocType = Literal["academic", "report", "note", "presentation", "web", "email", "other"]
```

`DocKind` is the file format (already exists). `DocType` is the semantic
category. It can be detected by a lightweight classifier (references section +
DOI -> academic, slide structure -> presentation, email headers -> email,
otherwise report/note), but it must be user-overridable via manifest or CLI.
Store `doc_type`, `doc_type_confidence`, and `doc_type_source` so downstream
quality issues can be traced.

---

## Gap 3: Speed and scalability

The refactor should improve correctness without accidentally making ingest
slower at the 200-1000 paper target. The hot path is not the file manifest or
JSON I/O. The hot path is parser CPU, image extraction, embedding, and graph
construction.

### Current scaling risks

| Stage | Current / likely complexity | Risk |
|-------|-----------------------------|------|
| Parse + media extraction | O(docs), parallel | Usually CPU-bound; slow outlier PDFs dominate wall time |
| Embedding | O(chunks), batched | Fine if unchanged chunks are skipped; expensive if every incremental run re-embeds everything |
| Chunk similarity graph | O(chunks^2) dense cosine today | Major memory/time risk at tens of thousands of chunks |
| Doc similarity | O(docs^2) | Fine through ~1000 docs |
| Citation resolution | O(citations x same-year candidates) | Fine if indexed by year/title fingerprints |
| Topics | Corpus-wide pass | Acceptable for v1; can be cached later |
| Explorer index | O(edges + chunks) | Fine if the edge set stays sparse |

The current `build_corpus_graph` materialises a dense `N x N` similarity matrix
for chunk vectors. At 60k chunks this is billions of cosine scores. Even when
the math is fast, the intermediate matrix is too large for a comfortable local
workflow. This is the main scale bug to fix while preserving exact semantics.

### Required fix: exact blockwise graph build

Keep exact cosine similarity for v1, but compute it in blocks:

```python
def build_chunk_similarity_edges(vectors, *, block_size=1024):
    for start in range(0, n_chunks, block_size):
        block = matrix[start : start + block_size]
        sims = block @ matrix.T
        mask_self_scores(sims, start)
        emit_top_k_edges(sims, ids, k=KNN_K)
        emit_threshold_edges(sims, ids, threshold=STRONG_COS)
```

This keeps peak memory at roughly `block_size x n_chunks` instead of
`n_chunks x n_chunks`, while producing the same `similar_knn` and
`similar_strong` edges as the dense implementation. It also allows progress
logging and deterministic chunking of the work.

Approximate nearest-neighbor indexes (HNSW/IVF/Qdrant/LanceDB/Chroma) are a
future optimization, not the v1 requirement. They are useful once exact
blockwise cosine becomes too slow, but they introduce recall knobs, index build
cost, and backend-specific behavior. The architecture should leave a clean
`SimilarityIndex` boundary, but the first implementation should be exact and
file-based.

### Speed guardrails

- **Do not re-embed unchanged chunks.** Embeddings are the main incremental
  win. Reuse vectors by chunk id + embedder fingerprint.
- **Batch embedding writes.** Write one merged vector store after validation,
  not one write per document.
- **Use deterministic ids.** Source id, doc id, chunk id, image id, and vector
  id should be stable across runs when source bytes/parser/chunker are stable.
- **Keep derived artifacts sparse.** Graph edges should be bounded by top-k or
  meaningful thresholds. Avoid storing full similarity matrices.
- **Preserve parallel parsing.** ProcessPool parsing remains the right default;
  add per-stage timing and slow-source reporting to every refactored stage.
- **Avoid model-based parser upgrades by default.** Tools like docling,
  Unstructured, and LlamaParse can improve tables/layout/OCR, but they trade
  speed and cost for quality. Add them behind parser configs and quality
  benchmarks, not as a silent replacement.
- **Add scale budgets to tests.** A 50-paper smoke corpus should validate
  correctness; a 200-1000 paper timing run should validate graph memory and
  wall time.

### When to move beyond exact file-based v1

Switch to an ANN/vector database backend only when one of these is true:

- exact blockwise graph build exceeds the local wall-time target;
- vector store files become too large to rewrite comfortably;
- queries need low-latency online updates while ingest is running;
- multiple processes need concurrent read/write access to the same corpus.

Until then, a manifest + exact rebuild gives better reproducibility and simpler
debugging than a partially-updated ANN graph.

---

## Downstream consequences for distillation

Every change to ingest output flows into distill through `preload_corpus()`. Here is the exact coupling and what breaks if each ingest artifact changes.

### The coupling map

```
preload_corpus() loads:
  docs         = list_documents(corpus)         → list[Document]
  chunks       = all_chunks(corpus)             → list[Chunk]
  vectors      = read_vector_store(corpus)      → VectorStore
  graph        = read_graph(corpus)             → CorpusGraph
  images_index = ImageIndex.load(corpus)        → ImageIndex
  persona_text = corpus/persona.txt             → str
```

### What distill reads from each artifact

| Artifact | Distill consumer | What it reads | Breaks if... |
|----------|-----------------|---------------|--------------|
| **Document.metadata** | `author_context.py` | authors, year, venue for person page grounding | Metadata shape changes or authors field is absent (non-academic docs) |
| **Document.citations** | `author_context.py` | cited works for author context building | Citations are empty (non-academic docs) -- **degrades gracefully**, author_context handles None |
| **Document.equations** | `pipeline.py:_equations_for_chunk()` | Per-chunk equation context for extractors | Equations are empty -- **degrades gracefully**, extractor gets empty list |
| **Document.figure_refs** | `pipeline.py:_figure_captions_for_chunk()` | Per-chunk figure caption context | Figure_refs empty -- **degrades gracefully** |
| **Document.images** | `pipeline.py` via `images_index` | Image records for writer figure ranking | Images empty -- **degrades gracefully** |
| **Chunk.section_type** | `pipeline.py`, `dossier.py` | `SKIP_SECTION_TYPES` filter (references/ack/appendix) | Section classification changes -- **must sync** SKIP_SECTION_TYPES |
| **Chunk.equation_ids** | `pipeline.py:_equations_for_chunk()` | Binds equations to chunks by char_span | Equation binding logic changes -- silent quality regression |
| **VectorStore** | `pipeline.py:_build_explorer_state()` | kNN neighbors, cosine similarity for explorer walks | Embedding dimensions change -- **hard crash** (shape mismatch) |
| **CorpusGraph edges** | `explorer.py` | `similar_strong`, `co_section`, `cites`, `doc_similar` for sampling | Edge types renamed or removed -- **explorer breaks** |
| **Explorer index** | `pipeline.py` | `chunks_by_doc`, `neighbors_by_chunk`, `caption_chunk_ids`, `content_chunk_ids` | Index version mismatch -- **stale sampler state** |
| **ImageIndex** | `pipeline.py`, `write_prep.py` | `near_chunk_ids`, `caption`, `label` for figure ranking in writer | Image index schema changes -- **writer gets wrong figures** |

### What must be updated in distill if ingest changes

**If enrichment stages become optional (non-academic docs):**
- `author_context.py` already handles missing `doc.citations` -- no change needed
- `pipeline.py:_equations_for_chunk()` already handles empty `doc.equations` -- no change needed
- `pipeline.py:_figure_captions_for_chunk()` already handles empty `doc.figure_refs` -- no change needed
- **BUT**: explorer's graph-walk strategies (`similar_strong`, `cites`, `doc_similar`) produce different behavior when citation edges are absent. For a non-academic corpus, the explorer relies entirely on embedding similarity edges. This is fine -- it degrades to a coverage-gap-only exploration strategy. No code change needed, but **the guided mode's orchestrator should be aware** that action choices like `jump_pagerank` may be less effective without citation edges.

**If section classification changes:**
- `SKIP_SECTION_TYPES` in `distill/dossier.py` AND `ingest/explorer_index.py` must stay in sync. **Centralise this** into `config.py` (already in the plan).

**If embedding dimensions change (e.g. switching from MiniLM-384d to a different model):**
- `vectors.meta.json` records the backend/model/dim. Eval and query reconstruct the embedder via `embedder_for(meta.backend, meta.model)`.
- Existing bundles produced with the old embedder become incompatible. **This is expected** -- re-ingest is required when the embedding model changes.
- Distill's `preload_corpus()` should validate `vectors.meta.json` dimensions match what the current embedder produces. **Add a check.**

**If the Document model gains a `doc_type` field (academic/report/note/etc.):**
- Distill doesn't need to change immediately. The field is informational for ingest-stage dispatch.
- Long-term: the writer could adapt its artifact template based on `doc_type` (e.g. less citation-heavy templates for notes), but that's a separate change.

**If incremental ingest changes chunk IDs:**
- The extract cache keys on `(prompt_hash, chunk_id)`. If chunk IDs change for the same text, the cache is invalidated. **This is acceptable** -- the cache is a performance optimization, not a correctness requirement.
- Existing bundles that reference old chunk IDs in their evidence become stale. **This is by design** -- bundles are immutable snapshots of one distill run.

### Safe changes (no distill impact)

- Adding new enrichment outputs (topics, tables) -- distill ignores what it doesn't use
- Changing parser backends (docling) -- distill sees the same Document/Chunk shapes
- Adding new graph edge types -- distill's explorer ignores unknown edge kinds
- Changing image extraction policy -- distill reads whatever the image index contains
- Changing dedup logic -- distill sees only the final corpus

---

## How ingest should facilitate distillation

The current pipeline has a clean separation (ingest writes, distill reads) but distill still does significant per-run reconstruction work that ingest could pre-compute once. The refactored ingest should shrink distill's startup cost and give it richer context.

### What distill currently reconstructs at run time

| Work | Where | Cost | Could ingest provide it? |
|------|-------|------|--------------------------|
| Equation binding to chunks | `pipeline.py:_equations_for_chunk()` filters `doc.equations` by `chunk.equation_ids` at every extract call | O(equations * chunks_per_batch) per batch | **Yes**: pre-compute `chunk_equations: dict[chunk_id, list[EquationRef]]` in the explorer index |
| Figure caption matching | `pipeline.py:_figure_captions_for_chunk()` merges `images_index.near_chunk_ids` + `doc.figure_refs` per extract call | O(images_per_doc) per batch | **Yes**: pre-compute `chunk_figure_captions: dict[chunk_id, list[FigureCaption]]` in the explorer index |
| Image-to-evidence ranking | `write_prep.py:build_write_request()` scores images by `near_chunk_ids` overlap with page evidence chunks | O(images * evidence_chunks) per page | **Partial**: ingest knows `near_chunk_ids` but evidence is distill-time. Keep ranking in distill. |
| Author context assembly | `author_context.py:build_author_context()` scans all docs for author names, builds publication lists | O(docs * authors) once per run | **Yes**: pre-compute `author_profiles: dict[name, AuthorProfile]` at ingest time |
| Section-type filtering | `pipeline.py` and `explorer_index.py` both filter `SKIP_SECTION_TYPES` | Duplicated logic | **Yes**: explorer index already does this. Just centralise the constant. |
| Document abstract/TLDR | `Document.abstract` and `Document.tldr` are optional, sparsely populated | Some docs have no summary | **Yes**: ingest should always populate `abstract` (first meaningful paragraph) and optionally `tldr` (model-generated) |

### Proposed ingest-time pre-computations

**1. Richer explorer index** -- extend the current `explorer_index.json` with:

```python
{
    "version": 2,
    # existing fields...
    "chunks_by_doc": {...},
    "chunk_to_doc": {...},
    "neighbors_by_chunk": {...},
    # new fields:
    "chunk_equations": {
        "chunk_id": [{"id": "...", "latex": "...", "type": "...", "label": "...", "context": "..."}]
    },
    "chunk_figure_captions": {
        "chunk_id": [{"key": "Fig. 1", "kind": "figure", "num": 1, "caption": "...", "image_id": "..."}]
    },
}
```

Distill's `_equations_for_chunk()` and `_figure_captions_for_chunk()` become simple lookups instead of per-call filtering. The pre-computation happens once at ingest, not once per extract batch.

**2. Author profiles at ingest time** -- the `build_author_context()` function in `distill/author_context.py` scans all documents for author names and builds publication/coauthor/year-range profiles. This is pure corpus metadata work. Move it to ingest:

```python
# ingest output: corpus/author_profiles.json
{
    "Leon Chua": {
        "primary_publications": ["doc_id_1", "doc_id_2"],
        "cited_in": ["doc_id_3"],
        "coauthors": ["S. Kang"],
        "year_range": [1971, 2020],
        "affiliations": ["UC Berkeley"]
    }
}
```

Distill's `build_author_context()` becomes a lookup into this pre-computed index instead of an O(docs * authors) scan.

**3. Document summaries** -- ensure every document has a populated `abstract` field (even if it's just the first meaningful paragraph). For documents with rich structure (academic papers), also populate `tldr` with a single-paragraph summary. The writer benefits from seeing "what this source is about" in the evidence context without reading the full chunk.

**4. Chunk quality signals** -- ingest already classifies `section_type`. Add:
- `is_content: bool` -- True for chunks that contain extractable knowledge (not references, acknowledgments, boilerplate). Replaces the `SKIP_SECTION_TYPES` check in distill.
- `information_density: float` -- ratio of content words to total words (or a simple heuristic). Helps the explorer prioritize chunks with real substance over noise.

These signals let distill's explorer make better sampling decisions without running its own heuristics on every chunk.

### What stays in distill (not ingest's job)

- **Evidence ranking** -- which chunks support which page's claims. This is a distill-time decision based on extracted concepts.
- **Figure-to-page ranking** -- which images belong on which page. Requires knowing the page's evidence set.
- **Dossier construction** -- accumulating extracted concepts into per-page dossiers. This is the core distill business logic.
- **Coverage gap computation** -- which chunks are under-represented in the wiki. This depends on the current wiki state, not just the corpus.
- **Write request assembly** -- what context the writer needs. Depends on dossier + evidence + neighbors.

---

## What NOT to change

- **ParseResult shape**: it works. Just type the `raw_images` field.
- **Chunk model**: `Chunk(id, doc_id, ord, text, char_span, section_path, section_type, equation_ids)` is stable and consumed everywhere.
- **Document model**: stable. Only extend, don't restructure.
- **VectorStore interface**: minimal and correct. The protocol wraps it, doesn't replace it.
- **Explorer index format**: consumed by distill. Keep the version field for compatibility.
- **CorpusPaths**: keep as the file-system store's configuration. The protocol layer sits above it.

---

## Implementation order

### Phase A: Consolidation (same philosophy as distill)

1. **Type the raw image contract**: `RawImage` dataclass, replace `metadata["_raw_images"]` everywhere.
2. **Define the Parser protocol + registry table**: formalize what parsers already do. Register existing parsers. No behavior change.
3. **Extract explicit typed stages**: keep equations/captions/citations/media/metadata as named calls with typed signatures first. Do not force one generic enricher interface until the stage boundaries prove it helps.
4. **Slim refresh.py into pipeline.py**: extract stage logic into named functions. Separate "process changed sources" from "rebuild derived." Target ~200L orchestration.
5. **Centralize config**: merge `SKIP_SECTION_TYPES` (currently duplicated in dossier.py and explorer_index.py) into `config.py`.
6. **Add per-stage timing + progress logs**: keep the current slow-paper report and add graph/embedding timing so scale regressions are visible.

### Phase B: Incremental ingest

7. **Add corpus manifest / record manager**: track source ids, content hashes, parser/chunker/embedder fingerprints, active/deleted status, and chunk ids.
8. **Add atomic staging/publish**: write changed source artifacts and derived artifacts to staging, validate invariants, then promote.
9. **Split pipeline into changed-source processing + exact derived rebuild**: parse/chunk/persist/embed only new or changed sources; rebuild graph/index/pagerank/topics/images/bibtex from the full active corpus.
10. **Test incremental and sync modes**: ingest 10 docs, add 5 more, modify 1, delete 1 in sync mode. Verify graph/vectors/index/doc edges reflect the active corpus.

### Phase C: Scale hardening

11. **Replace dense chunk similarity with exact blockwise top-k/threshold graph build**: preserve graph semantics while bounding peak memory.
12. **Add scale tests**: run a 50-paper correctness ingest and a 200-1000 paper timing/memory ingest. Record parser, embed, graph, index, and total timings.
13. **Add embedding migration guards**: fail fast on embedder fingerprint mismatch unless `--rebuild-vectors` or `--rebuild` is requested.

### Phase D: Extensions

14. **Add `DocType` classification with overrides**: lightweight heuristic detector plus manifest/CLI overrides. Ambiguous academic PDFs should run citation enrichment rather than silently skip it.
15. **Define CorpusReader first**: wrap `store/corpus.py` functions so distill's `preload_corpus` accepts a reader. Add CorpusWriter only after the file implementation stabilizes.
16. **Add parser quality harness**: compare pymupdf4llm vs docling/other parsers on section recovery, references, equations, figures, tables, and downstream graph quality.
17. **Add docling parser only behind config**: implement `DocumentParser` for docling and register alongside existing parsers after the quality harness justifies it.
18. **(Optional) Alternative store / ANN backend**: SQLite, LanceDB, Qdrant, or similar only when exact file-based v1 misses scale targets.

---

## Success criteria

The refactor is complete when the ingest pipeline is clearer, safer, and faster
to update without changing the corpus contract consumed by distill.

### Functional correctness

- Fresh ingest of the reference corpus produces the same `Document`, `Chunk`,
  vector, graph, image-index, topic, and BibTeX shapes consumed today.
- Every active document has markdown, chunks, doc JSON, vectors, graph nodes,
  and index entries.
- Vector ids exactly equal active chunk ids.
- Graph edges include `contains`, `similar_knn`, `similar_strong`,
  `co_section`, `cites`, `doc_similar`, and `cites_same` where applicable.
- PageRank, explorer index, topics, image index, and BibTeX are reproducible
  derived artifacts from the active corpus.

### Incremental correctness

- Re-ingesting unchanged sources skips parsing, media extraction, chunking, and
  embedding.
- Adding sources preserves unchanged source artifacts and rebuilds all global
  derived artifacts over the full active corpus.
- Modifying a source replaces its old markdown, chunks, images, doc JSON, and
  vectors, and removes stale references from derived artifacts.
- `sync` mode tombstones or removes missing sources; `additive` mode leaves
  them active.
- Existing documents are re-saved when derived edge fields change.
- The manifest records why each source was skipped, processed, replaced,
  deleted, or rebuilt.

### Scale and speed

- Exact blockwise graph output matches dense graph output on a small corpus.
- A 50-paper corpus ingests successfully in normal dev/CI conditions.
- A 200-1000 paper corpus completes without dense-matrix memory blowup.
- Per-stage timings are emitted for parse, persist, embed, graph, index,
  topics, and total runtime.
- Unchanged incremental ingest runtime is dominated by derived-artifact rebuild,
  not parsing or embedding.
- ANN/vector database backends remain optional until exact file-based ingest
  misses a measured wall-time or memory target.

### Compatibility

- Existing distill smoke tests pass against a refactored corpus.
- Existing eval, query, and html commands can read the corpus without schema
  changes.
- Older corpus bundles either load through compatibility paths or fail with a
  clear re-ingest message.
- Distill remains read-only with respect to corpus artifacts.

### Operational safety

- Crash during staging does not publish a mixed corpus state.
- Embedder fingerprint mismatch fails fast unless vector rebuild or full
  rebuild is requested.
- Parser/chunker fingerprint mismatch fails fast unless full rebuild is
  requested.
- Ingest lock/staging recovery prevents concurrent writers from corrupting a
  corpus.

### Quality

- Parser backend changes require a quality comparison before becoming default.
- Citation, equation, figure-caption, image-linking, and topic extraction do not
  regress on the reference corpus.
- Ambiguous academic PDFs run citation enrichment rather than silently skipping
  it.

---

## Verification

After each step:
- `uv run ruff check src/wikify tests/wikify`
- `uv run pytest tests/wikify -q`
- Ingest the test corpus and verify identical output (diff `docs/`, `chunks/`, `graph.json`)
- Run a distill smoke test to confirm the consumer contract is unbroken

Incremental-specific checks:
- unchanged source files are skipped by hash;
- changed source files replace their old chunks/images/vectors;
- deleted sources disappear in `sync` mode and stay in `additive` mode;
- vector ids exactly match active chunk ids;
- existing documents are re-saved when derived edge fields change;
- crash during staging does not leave a mixed corpus state.

Scale-specific checks:
- blockwise graph output matches dense graph output on a small corpus;
- peak graph-build memory stays bounded on a medium corpus;
- per-stage timings are reported and tracked across refactors;
- parser-backend comparisons include quality metrics, not just wall time.
