# Ingest Pipeline Refactoring -- Design Brief

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

**For ingest**: the parser registry is already a single dispatch function -- good. But the post-parse enrichment (equations, figures, figure_refs, citations, metadata) is 5 separate modules with no shared interface. Each returns a different shape of dict. A uniform enrichment protocol would let new enrichers (e.g. docling's table extraction) slot in without touching `refresh.py`.

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

Adding a new enricher (e.g. docling's table extraction, or a model-based metadata extractor) requires modifying refresh.py's control flow.

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

### Principle: stages as composable functions, storage behind a protocol

The pipeline should be a sequence of typed stages, each a pure function from input to output. Storage should be a thin protocol that the pipeline writes to and distill reads from.

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

  # --- Enrichment ---
  enrich/
    protocol.py         Enricher protocol: enrich(doc, chunks, markdown) -> EnrichResult
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

  # --- Storage (new protocol layer) ---
  store/
    protocol.py         CorpusWriter protocol: write_document, write_chunks, write_vectors, etc.
    filesystem.py       Current file-based implementation (JSON, JSONL, npz, sidecars)
```

### Key abstractions

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

#### 2. Enricher protocol (composable post-parse steps)

```python
class Enricher(Protocol):
    def enrich(self, doc: Document, chunks: list[Chunk], markdown: str) -> EnrichResult: ...

@dataclass
class EnrichResult:
    doc_updates: dict           # fields to merge into Document
    chunk_updates: dict[str, dict]  # chunk_id -> fields to merge
```

Each enricher is a standalone function that reads the document + chunks and returns structured updates. The pipeline composes them:

```python
ENRICHERS: list[Enricher] = [
    EquationEnricher(),
    CaptionEnricher(),
    CitationEnricher(),
    MetadataEnricher(),
]

for enricher in ENRICHERS:
    result = enricher.enrich(doc, chunks, markdown)
    apply_updates(doc, chunks, result)
```

Adding a new enricher (e.g. docling table extraction) means adding one entry to the list.

#### 3. Corpus store protocol (storage-agnostic)

```python
class CorpusWriter(Protocol):
    def write_document(self, doc: Document) -> None: ...
    def write_chunks(self, doc_id: str, chunks: list[Chunk]) -> None: ...
    def write_markdown(self, doc_id: str, text: str) -> None: ...
    def write_vectors(self, ids: list[str], matrix: np.ndarray, meta: dict) -> None: ...
    def write_graph(self, graph: CorpusGraph) -> None: ...
    def write_image(self, image: RawImage, doc_id: str) -> Path: ...

class CorpusReader(Protocol):
    def list_documents(self) -> list[Document]: ...
    def read_chunks(self, doc_id: str) -> list[Chunk]: ...
    def read_vectors(self) -> VectorStore: ...
    def read_graph(self) -> CorpusGraph: ...
    ...
```

The current file-based implementation becomes `FileSystemStore(CorpusPaths)`. A future SQLite store would implement the same protocols. Distill's `preload_corpus()` would accept a `CorpusReader` instead of `CorpusPaths`.

**Important**: this is NOT an immediate requirement. The protocol should be defined, and the file-based implementation should be the default. SQLite/ChromaDB can be added later without changing the pipeline.

#### 4. Pipeline as stage composition

```python
def ingest(
    input_dir: Path,
    store: CorpusWriter,
    parsers: dict[str, DocumentParser] | None = None,
    enrichers: list[Enricher] | None = None,
    embedder: Callable | None = None,
) -> CorpusPaths:
    parsers = parsers or DEFAULT_PARSERS
    enrichers = enrichers or DEFAULT_ENRICHERS
    embedder = embedder or default_embedder()

    # Stage 1: enumerate + dedupe
    sources = enumerate_sources(input_dir, store)

    # Stage 2: parse + chunk (parallel)
    bundles = parse_parallel(sources, parsers)

    # Stage 3: enrich + persist
    docs = []
    for bundle in bundles:
        doc, chunks = assemble(bundle)
        for enricher in enrichers:
            apply(enricher.enrich(doc, chunks, bundle.markdown), doc, chunks)
        store.write_document(doc)
        store.write_chunks(doc.id, chunks)
        docs.append((doc, chunks))

    # Stage 4: embed
    all_chunks = flatten(chunks for _, chunks in docs)
    vectors = embedder([c.text for c in all_chunks])
    store.write_vectors([c.id for c in all_chunks], vectors, embedder_meta())

    # Stage 5-8: graph, index, pagerank, topics
    graph = build_graph(docs, vectors)
    store.write_graph(graph)
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
- **Vectors**: load existing `vectors.npz`, append new chunk embeddings, save the merged matrix. (NumPy vstack is trivial.)
- **Graph**: load existing `graph.json`, add new nodes/edges from new docs + recompute edges between old and new docs (similarity, citation matching). This is the expensive part -- old-to-new doc similarity requires comparing new doc mean-pool vectors against all existing ones.
- **Explorer index**: rebuild from the merged graph + full chunk set. (Fast, <1s.)
- **PageRank**: recompute on the merged doc graph. (Fast.)
- **Topics**: merge new doc topics into existing vocabulary.
- **Images index**: append new images.
- **Documents**: only persist new docs. Existing docs don't change unless their citation edges update (new papers might cite old ones).

The pipeline skeleton becomes:

```python
def ingest(input_dir, store, ...):
    existing_docs = store.list_documents()    # may be empty (full ingest)
    existing_vectors = store.read_vectors()   # may be None
    
    sources = enumerate_and_dedupe(input_dir, existing_docs)
    if not sources:
        return  # nothing new
    
    new_bundles = parse_parallel(sources, parsers)
    new_docs, new_chunks = persist_new(new_bundles, store, enrichers)
    
    # Merge vectors: append new embeddings to existing
    all_vectors = merge_vectors(existing_vectors, new_chunks, embedder)
    store.write_vectors(all_vectors)
    
    # Rebuild derived artifacts from ALL docs (existing + new)
    all_docs = existing_docs + new_docs
    all_chunks = store.read_all_chunks()  # or keep in memory
    graph = build_graph(all_docs, all_chunks, all_vectors)
    store.write_graph(graph)
    ...
```

The key insight: **parse + chunk + persist is incremental**, but **graph + index + pagerank must see the full corpus**. This is already implicit in the current code (it rebuilds them every time) -- making it explicit just means loading existing artifacts before the merge step.

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

### Required fix: enrichers must be optional and document-type-aware

The enricher protocol should declare what document types it applies to:

```python
class Enricher(Protocol):
    def enrich(self, doc: Document, chunks: list[Chunk], markdown: str) -> EnrichResult: ...
    def applies_to(self, doc_kind: str) -> bool: ...
```

Default enrichers by document type:

| Enricher | pdf (academic) | pdf (report) | docx | pptx | html | md |
|----------|---------------|--------------|------|------|------|-----|
| MetadataEnricher | yes | yes | yes | yes | yes | yes |
| EquationEnricher | yes | yes | no | no | no | yes |
| CaptionEnricher | yes | yes | no | yes | no | no |
| CitationEnricher | yes | no | no | no | no | no |
| CouplingEnricher | yes | no | no | no | no | no |
| TopicEnricher | yes | yes | yes | yes | yes | yes |

The pipeline checks `enricher.applies_to(doc.kind)` before calling. Non-academic documents skip citation/coupling enrichment and rely on embedding-based similarity for inter-document edges. The graph still works -- it just has fewer edge types.

Additionally, the `Document` model should support a richer `kind` vocabulary beyond file extension:

```python
DocKind = Literal["pdf", "docx", "pptx", "html", "md"]
DocType = Literal["academic", "report", "note", "presentation", "web", "email", "other"]
```

`DocKind` is the file format (already exists). `DocType` is the semantic category, detected by a lightweight classifier (heuristic: presence of References section + DOI → academic; slide structure → presentation; email headers → email; otherwise → report/note). This classification feeds `applies_to()`.

---

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

**If enrichers become optional (non-academic docs):**
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
- Distill doesn't need to change immediately. The field is informational for enricher dispatch.
- Long-term: the writer could adapt its artifact template based on `doc_type` (e.g. less citation-heavy templates for notes), but that's a separate change.

**If incremental ingest changes chunk IDs:**
- The extract cache keys on `(prompt_hash, chunk_id)`. If chunk IDs change for the same text, the cache is invalidated. **This is acceptable** -- the cache is a performance optimization, not a correctness requirement.
- Existing bundles that reference old chunk IDs in their evidence become stale. **This is by design** -- bundles are immutable snapshots of one distill run.

### Safe changes (no distill impact)

- Adding new enrichers (topics, tables) -- distill ignores what it doesn't use
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
3. **Define the Enricher protocol with `applies_to`**: formalize post-parse modules. Each enricher declares its applicable doc types. Wrap existing functions.
4. **Add `DocType` classification**: lightweight heuristic detector (References section + DOI → academic, slide structure → presentation, etc.). Feed into enricher dispatch.
5. **Slim refresh.py into pipeline.py**: extract stage logic into named functions. Separate "parse new" from "rebuild derived." Target ~200L orchestration.
6. **Centralize config**: merge `SKIP_SECTION_TYPES` (currently duplicated in dossier.py and explorer_index.py) into `config.py`.

### Phase B: Incremental ingest

7. **Split pipeline into parse-new + merge-derived**: parse/chunk/persist only new sources; load existing artifacts; merge vectors (vstack); rebuild graph/index/pagerank from full corpus.
8. **Test incremental**: ingest 10 docs, then add 5 more. Verify graph has 15 nodes, vectors have all chunks, explorer index covers everything.

### Phase C: Extensions

9. **Define CorpusWriter/CorpusReader protocols**: wrap `store/corpus.py` functions. Distill's `preload_corpus` accepts a reader.
10. **Add docling parser**: implement `DocumentParser` for docling. Register alongside existing parsers.
11. **(Optional) Alternative store**: SQLite or similar, implementing CorpusWriter/CorpusReader. Swap via config.

---

## Verification

After each step:
- `uv run ruff check src/wikify tests/wikify`
- `uv run pytest tests/wikify -q`
- Ingest the test corpus and verify identical output (diff `docs/`, `chunks/`, `graph.json`)
- Run a distill smoke test to confirm the consumer contract is unbroken
