# wikify -- architecture

## What the system does

1. **Ingest** raw documents (pdf, docx, pptx, html, md) into a normalized
   corpus on disk: markdown text + extracted images + chunks + embeddings.
2. **Build a knowledge graph** over the corpus (documents, authors, chunks,
   citations) for navigation, sampling, and author queries.
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
[ Corpus ]            files on disk + vector store + knowledge graph
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
| Embeddings       | `corpus/vectors.npz` (numpy)          | vector search results          |
| Knowledge graph  | `corpus/graph.json`                   | sampling decisions, author queries |
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
- `KnowledgeGraph` (in `citestore/graph.py`) -- typed property graph
  (`nx.MultiDiGraph`) with Paper, Author, Chunk, Figure, and Equation
  nodes. Edge types:
  - `contains`: paper -> chunk
  - `cites`: paper -> paper (directed)
  - `cites_same`: paper <-> paper (bibliographic coupling)
  - `doc_similar`: paper <-> paper (embedding cosine)
  - `authored_by`: paper -> author (with position)
  - `collaborated`: author <-> author (co-authorship)
  - `co_section`: chunk <-> chunk (same doc + same section path)
  - `CONTAINS_FIGURE`: paper -> figure
  - `FIGURE_NEAR_CHUNK`: figure -> chunk (inline `Fig. N` references)
  - `CONTAINS_EQUATION`: paper -> equation
  - `EQUATION_IN_CHUNK`: equation -> chunk (char_span overlap or text match)
  Chunk similarity edges (`similar_knn`, `similar_strong`) are removed;
  vector search via VectorStore replaces them. PageRank is computed at
  graph build time. Full schema is in `citestore/graph.py`.
  Equation nodes carry `is_chemical: bool` for filtering:
  `kg.source(id).math_equations()` / `kg.source(id).chemical_formulas()`.

### Wiki side

- `WikiPage` (in-memory representation of a `.md` file)
  - `id`, `kind` (`article` | `person`), `title`, `aliases`
  - `body_markdown` (the human prose, equations as `$$`/`$` LaTeX)
  - `evidence: list[Evidence]`
  - `links: list[str]` (other wiki page ids)
  - `equations: list[dict]` (`{latex, label, kind, context}`,
    persisted to `.equations.json` sidecar)
  - `provenance: dict` (run_id, model, sampled_chunks)
- `Evidence`
  - `marker`: the footnote label used in the body, e.g. `e1`
  - `chunk_id`: the corpus chunk this claim came from
  - `doc_id`: redundant but convenient for display
  - `quote`: the exact span of text from the chunk that supports the claim
  - `locator`: optional human-readable locator (page, section, slide)
- `WikiKnowledgeGraph` (in `store/wiki_graph.py`) -- nodes are wiki page
  ids; edges:
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

1. **Parse + chunk per source** (single-threaded for docling due to
   GPU model; parallel for default parser). Each source produces
   `parsed`, `chunks`, `equations`, `figure_refs`. Equations are
   bound to chunks via `char_span` overlap (default parser) or
   whitespace-normalized text containment (docling HybridChunker).
   For docling, bare inline reference numbers are bracketized using
   the bibliography entry count as the valid range.
2. **Per-doc persist** in the main process: write markdown + chunks +
   sidecar JSONs, populate `DocImage.near_chunk_ids` from inline
   figure references found in chunk prose.
3. **Embed everything** in one batch through the embedder.
   GPU-accelerated via DirectML/CUDA when available (auto-detected).
4. **`_populate_doc_edges`** — fills `Document.cites`,
   `Document.similar_to`, `Document.cites_same`. Must run BEFORE the
   corpus graph builder, otherwise the saved `graph.json` has empty
   citation edges (long-standing bug, fixed in this pass).
5. **`build_knowledge_graph`** — builds the unified `KnowledgeGraph`
   (Paper + Author + Chunk + Figure + Equation nodes, citation +
   authorship + collaboration + figure-near-chunk + equation-in-chunk
   edges), computes PageRank, and writes `graph.json`. Citation
   ordinals are stored one-based (`ord_refs[cit.ord + 1]`) to match
   `[N]` markers in text.
6. **Topics, image index, equations index, library.bib**.
7. **Re-save documents** with fully-populated edges + figure metadata.

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
  models.py             # Document, Chunk, Evidence,
                        # WikiPage, Stage, Run
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
    parsers/            # one parser per kind; backend selectable via
                        # --parser <name> on CLI (enum + factory)
      pdf.py            # uses pymupdf4llm layout engine with
                        # header=False/footer=False; falls back to
                        # fitz blocks-mode for scanned PDFs;
                        # captures TOC via doc.get_toc()
      docx.py
      pptx.py
      html.py
      markdown.py
      registry.py       # ParserBackend enum + factory dispatch
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
    topics.py           # topic extraction (used by GT-C in eval)
    pipeline.py         # incremental ingest entry point;
                        # parallel parsing, manifest-based dedup,
                        # vector reuse, physical stale removal
    manifest.py         # CorpusManifest, SourceRecord, ChangeSet,
                        # diff_sources for incremental ingest

  citestore/            # knowledge graph
    graph.py            # KnowledgeGraph query API
    graph_build.py      # build_knowledge_graph (Paper + Author + Chunk nodes)

  store/                # disk I/O for corpus and wikis
    corpus.py           # read documents/chunks/embeddings
    vectors.py          # thin vector-db wrapper
    wiki_files.py       # read/write wiki page .md files
    wiki_index.py       # bundle index (_index.json, _index.md)
    wiki_graph.py       # WikiKnowledgeGraph
    images_index.py     # per-corpus image index
    equations_index.py  # per-corpus equation index (deduplicated)
    bundle_embeddings.py  # cached page-body embeddings for eval

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
  study-design.md       # study design: baseline / scripted / guided conditions
  metrics.md            # M1-M6 + GT-C + GT-P
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

## Operator quick reference

### Environment

```bash
export WIKIFY_EMBEDDER=fastembed
export WIKIFY_DISPATCH_DIR=data/dispatch   # default
```

### What ingest produces

| Path | Content |
|------|---------|
| `markdown/{doc_id}.md` | Cleaned markdown (YAML frontmatter + edges block) |
| `chunks/{doc_id}.jsonl` | One chunk per line |
| `docs/{doc_id}.json` | Document record (sections, images, citations, equations) |
| `images/{doc_slug}/` | Binary figures (caption-only by default) |
| `vectors.npz` + `.ids.json` + `.meta.json` | Chunk embeddings |
| `graph.json` | Knowledge graph (Paper + Author + Chunk + Figure + Equation nodes) |
| `topics.json` | Topic vocabulary |
| `equations.json` | Corpus-wide equation index (deduplicated by normalized LaTeX) |
| `library.bib` | BibTeX export |

### CLI workflows

```bash
# Ingest
uv run python -m wikify.cli ingest <input_dir> --out <corpus_dir>

# Distill (preset)
uv run python -m wikify.cli distill --preset scripted-mixed --budget 1x --seed 0 \
  --corpus <corpus_dir> --bundle <bundle_dir>

# Distill (manual)
uv run python -m wikify.cli distill --strategy M --mode guided --guided-tools navigate \
  --budget 1x --seed 0 --corpus <corpus_dir> --bundle <bundle_dir>

# Study
uv run python -m wikify.cli study \
  --presets scripted-mixed,guided-navigate,guided-full \
  --include-baseline --budgets 1x --seeds 0,1,2

# Eval
uv run python -m wikify.cli eval --bundle <bundle_dir> --corpus <corpus_dir>

# HTML
uv run python -m wikify.cli html --bundle <bundle_dir>
```

### Troubleshooting

- **Dispatcher hang**: Check skill is enabled, request file exists,
  response file lands next to it.
- **Schema validation**: `extra="forbid"` — any unexpected key rejects.
  Read `schema.py` for canonical shapes.
- **Budget exhaustion mid-write**: Raise `--budget` or shift
  `--exploit-fraction`.
- **Cache miss explosion**: Prompt template or model changed, invalidating
  cache keys. Check `prompt_hash` stability.
