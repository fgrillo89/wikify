# wikify -- architecture

## What the system does

1. **Ingest** raw documents (pdf, docx, pptx, html, md) into a normalized
   corpus on disk: markdown text + extracted images + chunks + embeddings.
2. **Build a corpus graph** over the corpus (documents, chunks, similarity,
   optional citations) for navigation and sampling.
3. **Distill wikis** (concept pages + people pages) from the corpus by letting
   an agent sample a *fraction* of the corpus, guided by the corpus graph and
   small models. Wikis cross-link each other.
4. **Build a wiki graph** over the distilled wikis for navigation, telemetry,
   and benchmarking.
5. **Report metrics + telemetry** over runs and over the wiki graph.

## Three layers, one direction

```
raw files
   |
   v
[ Corpus ]            files on disk + vector store + corpus graph
   |
   v
[ Wikis ]             markdown files on disk (the source of truth)
   |
   v
[ Wiki graph + metrics ]
```

Hard rules:

- `ingest` never reads wikis.
- `distill` reads the corpus, writes wiki markdown files. Never mutates the
  corpus.
- `wikigraph` reads wiki files, never mutates them.
- `metrics`/`telemetry` read everything, write only into `runs/`.

## Source-of-truth rule

Every artifact has exactly one source of truth, and it is the most inspectable
form possible:

| Artifact         | Source of truth                       | Derived from it                |
|------------------|---------------------------------------|--------------------------------|
| Document text    | `corpus/markdown/{doc_id}.md`         | chunks, embeddings             |
| Document images  | `corpus/images/{doc_id}/`             | (none)                         |
| Chunks           | `corpus/chunks/{doc_id}.jsonl`        | vector store rows              |
| Embeddings       | vector store (chroma / lancedb / ...) | similarity edges               |
| Corpus graph     | `corpus/graph.json` (or .parquet)     | sampling decisions             |
| **Wiki pages**   | **`wiki/articles/{title}.md` and `wiki/people/{title}.md`** | wiki graph, metrics |
| Wiki graph       | `wiki/_graph.json`                    | metrics                        |
| Runs / telemetry | `runs/{run_id}/...`                   | reports                        |

The wikis are **markdown files on disk**. They are the product. There is no
database row that "really" holds the page -- the file is the page.

## Data structures

These are the contracts. Everything else is implementation.

### Corpus side

- `Document` -- `id`, `source_path`, `kind`, `title`, `metadata`,
  `markdown_path`, `image_dir`. Carries:
  - `sections`: list of `DocSection(path, chunk_ids, summary)`
  - `images`: list of `DocImage(id, path, caption, alt_text, page,
    near_chunk_ids)` — `near_chunk_ids` is the list of body chunks
    whose prose mentions the image via inline `Fig. N` / `Table N` /
    `Scheme N` references, populated at ingest time
  - `citations`: list of structured reference dicts with
    `{ord, raw_text, authors, year, title, venue, doi}`
  - `equations`: list of equation records
    `{id, latex, type, label, context, char_offset}`. Extracted from
    the cleaned markdown by `ingest/equations.py` (display, inline,
    chemical, unicode, image-equation placeholders, named equations
    like "Ohm's law")
  - `figure_refs`: list of inline figure / table / scheme caption
    records `{key, kind, num, sub, caption, section_path,
    char_offset}`. Extracted from body markdown by
    `ingest/figure_refs.py` — caption-first, complements the binary
    image extractor
  - `similar_to`, `cites`, `cites_same`: doc-level edges populated by
    `_populate_doc_edges` after embedding (see Pipeline order below)
- `Chunk` -- `id`, `doc_id`, `ord`, `text`, `char_span`, `section_path`,
  `section_type`, `equation_ids`. The `equation_ids` field lists every
  equation whose source `char_offset` falls inside the chunk's
  `char_span`; the chunker binds them after extraction.
  Embedding lives in the vector store, keyed by `chunk.id`.
- `CorpusGraph` -- nodes are `Document` and `Chunk` ids; edges are typed:
  - `contains`: doc -> chunk
  - `similar_knn` / `similar_strong`: chunk <-> chunk (kNN over
    embeddings; `similar_strong` is the cosine >= `STRONG_COS` filter)
  - `co_section`: chunk <-> chunk (same doc + same section path)
  - `cites`: doc -> doc (directed; resolved against the corpus by the
    year-bucketed fuzzy matcher in `pipeline.py::_populate_doc_edges`)
  - `cites_same`: doc <-> doc (undirected bibliographic coupling, top-k
    per doc by shared-reference count)
  - `doc_similar`: doc <-> doc (mean-pooled embedding cosine >=
    `DOC_SIM_COS`, currently 0.75 — same threshold as `doc.similar_to`)

### Wiki side

- `WikiPage` (in-memory representation of a `.md` file)
  - `id`, `kind` (`article` | `person`), `title`, `aliases`
  - `body_markdown` (the human prose)
  - `evidence: list[Evidence]`
  - `links: list[str]` (other wiki page ids)
  - `provenance: dict` (run_id, model, sampled_chunks)
- `Evidence`
  - `marker`: the footnote label used in the body, e.g. `e1`
  - `chunk_id`: the corpus chunk this claim came from
  - `doc_id`: redundant but convenient for display
  - `quote`: the exact span of text from the chunk that supports the claim
  - `locator`: optional human-readable locator (page, section, slide)
- `WikiGraph` -- nodes are wiki page ids; edges:
  - `links_to` (explicit cross-links from `links`)
  - `co_evidence` (two pages cite the same chunk)
  - `same_domain` (clustering over page bodies)

### Run side

- `Run` -- `id`, `started_at`, `finished_at`, `config_hash`, `stages`,
  `sampled_chunks`, `pages_touched`, `metrics`.
- `Stage` -- `name`, `t_start`, `t_end`, `counters`, `cost`.

## Ingest pipeline order

`ingest/pipeline.py::ingest_corpus` runs in this order — order matters
because the corpus graph depends on populated doc-level edges:

1. **Parse + chunk per source in parallel** (`ProcessPoolExecutor`,
   60 % of CPU cores by default; `--workers` overrides). Each worker
   produces a `_ParsedBundle` with `parsed`, `chunks`, `equations`,
   `figure_refs`. Equations are bound to chunks via `char_span` overlap
   inside the worker.
2. **Per-doc persist** in the main process: write markdown + chunks +
   sidecar JSONs, populate `DocImage.near_chunk_ids` from inline
   figure references found in chunk prose.
3. **Embed everything** in one batch through the embedder.
4. **`_populate_doc_edges`** — fills `Document.cites`,
   `Document.similar_to`, `Document.cites_same`. Must run BEFORE the
   corpus graph builder, otherwise the saved `graph.json` has empty
   citation edges (long-standing bug, fixed in this pass).
5. **`build_corpus_graph`** — reads `Document.cites` and
   `Document.cites_same` directly to populate the graph's `cites` and
   `cites_same` edge sets, then writes `graph.json`.
6. **`build_explorer_index`** — pre-computes the corpus-side explorer
   state (chunks_by_doc, neighbours, abstract proxy, content vs caption
   chunks). Skips chunks whose `section_type` is in
   `{references, acknowledgments, appendix}` so the explorer never
   dispatches reference entries to the extractor.
7. **`_write_pagerank`** — real PageRank on the doc graph using
   `cites + doc_similar + cites_same` as edges (not uniform).
8. **Topics, image index, library.bib**.
9. **Re-save documents** with fully-populated edges + figure metadata.

## People and articles are separate kinds

Articles and people are separate `kind`s with separate directories
(`wiki/articles/` and `wiki/people/`) and separate artifact templates.
An article page is built from chunks that *describe an idea*; a person
page is built from chunks that *attribute work to a name* plus document
metadata.

Person pages are written by the model just like article pages. Author
metadata is assembled at ingest/distill time by
`distill/author_context.py::build_author_context` and attached to
the `WriteRequest` as `author_context` (primary publications, cited
works, collaborators, year range, affiliations) for grounding. The
writer produces biographical prose in Wikipedia voice; the
"appears in this corpus" phrasing is banned. The writer is robust to
missing `author_context` (non-author persons mentioned only in chunk
prose): the lead degrades to `**Name** is credited with [contribution
grounded in evidence]`.

## Package layout

```
src/wikify/
  types.py              # enums (ModelTier, Role, StrategyId) + Protocols
                        # (Extractor, Compactor, Editor, Writer,
                        # Orchestrator, Querier)
  config.py             # all constants
  schema.py             # Pydantic v2 request/response models
  context.py            # context envelope + role specs + count_tokens
  meter.py              # CostMeter: per-call accounting + budget gate
  cache.py              # ExtractCache: deterministic per-chunk cache
  embedding.py          # switchable embedding backend
  dispatch.py           # single Dispatch class (file-based)
  models.py             # Document, Chunk, CorpusGraph, Evidence,
                        # WikiPage, WikiGraph, Stage, Run
  paths.py              # CorpusPaths, BundlePaths

  distill/              # strategies and their primitives
    strategy.py         # budget allocation + strategy config + run modes
    explorer.py         # Explorer protocol + LevyExplorer + ExplorerState
                        # + action dispatch + build_snapshot
    pipeline.py         # the distillation loop
    dossier.py          # canonicalization + dossier assembly
    write_prep.py       # write request building + related + crosslink
    author_context.py   # build_author_context for person pages
    persona.py          # persona selection
    field_detect.py     # field detection heuristics
    query.py            # corpus query engine
    maintenance.py      # post-run maintenance
    iteration.py        # create/refine/merge operations
    preload.py          # preloaded corpus state

  ingest/               # corpus build
    parsers/            # one parser per kind
      pdf.py            # uses pymupdf4llm layout engine with
                        # header=False/footer=False; falls back to
                        # fitz blocks-mode for scanned PDFs;
                        # captures TOC via doc.get_toc()
      docx.py
      pptx.py
      html.py
      markdown.py
      registry.py
      _sections.py      # section_spans (markdown headings) +
                        # toc_spans (TOC-driven, used when >=3 entries)
      _clean.py         # parse-time markdown cleanup; protects
                        # references-tail from aggressive noise filtering
    chunker.py          # markdown -> [Chunk]
    images.py           # save_doc_images (caption-only),
                        # link_chunks_to_images (populates
                        # near_chunk_ids), caption_chunks_for
    figures.py          # binary figure extraction; drops uncaptioned
                        # images by default; scanned-page fallback
                        # dedupes by raw page bytes
    figure_refs.py      # caption-first body extraction
    equations.py        # display/inline/chemical/unicode/named/image
    citations.py        # references section detection + structured parse;
                        # author-anchored fallback for landing-page papers
    corpus_graph.py     # builds CorpusGraph (cites + cites_same too)
    explorer_index.py   # pre-computed explorer state, written by ingest
    topics.py           # topic extraction (used by GT-C in eval)
    pipeline.py         # incremental ingest entry point;
                        # parallel parsing, manifest-based dedup,
                        # vector reuse, physical stale removal
    manifest.py         # CorpusManifest, SourceRecord, ChangeSet,
                        # diff_sources for incremental ingest

  store/                # disk I/O for corpus and wikis
    corpus.py           # read documents/chunks/embeddings
    vectors.py          # thin vector-db wrapper
    wiki_files.py       # read/write wiki page .md files
    wiki_index.py       # bundle index (_index.json, _index.md)
    images_index.py     # per-corpus image index
    bundle_embeddings.py  # cached page-body embeddings for eval
    corpus_profile.py   # PageRank, Louvain, betweenness

  eval/                 # metrics and audit
    bundle.py
    metrics.py
    audit.py
    community.py

  render/html/          # static site renderer
    templates/
    static/

  prompts/              # prompt templates
    registry.py
    style_guide.md
    fields/
    artifact_types/

  cli.py                # thin Typer adapter; one command per verb
```

Documentation lives alongside the code in `docs/`:

```
docs/
  architecture.md       # this file
  strategies.md         # explorer / schedule / tiering cube
  metrics.md            # M1-M6 + GT-C + GT-P
  runbook.md            # operator runbook: CLI flags, environment, workflows
  test-run-playbook.md  # reproducible test-run procedure + quality review
```

## Dependency direction

```
                        models.py
                            ^
                            |
      types.py  config.py  context.py  schema.py
            ^       ^          ^          ^
            |       |          |          |
         meter.py  cache.py  embedding.py
                       ^
                       |
                   dispatch.py
                       ^
                       |
                   distill/
                       ^
                       |
                    cli.py
```

Strategy configs in `distill/strategy.py` are data rows over explorer,
schedule, and tier knobs plus a single factory. They never import dispatch.
The CLI wires a concrete dispatch into the distill pipeline at run time.

## Coding standards

1. **Functions over namespace classes.** A `class` is justified only by
   shared mutable state or a real polymorphism need.
2. **Constructor injection over module-level globals.** Strategies, the
   cost meter, the cache, the context envelope, and the dispatch are
   all passed in.
3. **Modules <= 400 LOC.** Split anything that grows past 600.
4. **Protocols for real extension points.** `Extractor`, `Compactor`,
   `Editor`, `Writer`, `Orchestrator`, `Querier`, `Explorer` are
   `Protocol` classes. Concrete implementations live in their own modules.
5. **Top-of-file imports.** No lazy imports except for genuinely optional
   dependencies.
6. **Enums and dispatch tables over `if/elif` chains** when branching on
   stable kinds.
7. **No vendor-specific names in module names or public symbols.** Vendor
   identity is configuration. The only exception is the binding module file
   itself.
8. **No grab-bag helper modules.** Helpers belong in the nearest
   responsible package.
9. **Agent-native core.** No core module imports an LLM SDK. The
   orchestrator supplies model behavior through injected protocols.
10. **One responsibility per module**, stated in a one-line top-of-file
    docstring.

## Key types and protocols

The full types live in `models.py` and in `types.py`:

```python
# types.py
class Extractor(Protocol):
    def extract(self, request: ExtractRequest) -> ExtractResponse: ...

class Writer(Protocol):
    def write(self, request: WriteRequest) -> WriteResponse: ...

class Orchestrator(Protocol):
    def step(self, state: OrchState) -> OrchAction: ...
```

`ExtractRequest` carries the `target_chunk` and the `canonical_titles`
pool, plus per-chunk `equations` (filtered from `Document.equations` via
`Chunk.equation_ids`) and per-chunk `figure_captions` (combining images
whose `near_chunk_ids` includes this chunk PLUS `Document.figure_refs`
in the same top-level section). The Protocol does not expose tokens or
models -- it exposes *content*. The dispatch is responsible for turning
content into a model call. The strategy never sees the SDK.

`WriteRequest.figures` is **ranked by relevance**: each candidate image
gets a score equal to the number of page-evidence chunks present in
its `near_chunk_ids`, then ties broken by "has any near_chunk_ids"
(decorative figures sink to the bottom), then by stem for determinism.
The list is capped at `_PAGE_FIGURES_TOP_K = 8` so the writer prompt
isn't flooded with figures unrelated to the cited claims.

```python
# distill/explorer.py
class Explorer(Protocol):
    def next_batch(self, state: ExplorerState, k: int) -> list[ChunkRef]: ...

@dataclass(frozen=True)
class LevyExplorer:
    local_op: LocalOp
    global_op: GlobalOp
    jump_rate: float
    chunks_per_landed_doc: int = 3

    def next_batch(self, state, k):
        out = []
        for _ in range(k):
            if state.wiki_is_empty or state.rng.random() < self.jump_rate:
                out.extend(self._global(state))
            else:
                out.append(self._local(state))
        return out
```
