# wikify_simple -- architecture

> **Current state & roadmap**: this document describes the layout and contracts as they stand today. The active structural roadmap — pre-computed sampler index, campaign driver, LLM-as-sampler, image sampling, output-quality fixes, renames — is tracked in [`plans/structural-improvements.md`](plans/structural-improvements.md). Read that file when planning new work. See also [`test-run-playbook.md`](test-run-playbook.md) before running any end-to-end test.

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
  `markdown_path`, `image_dir`.
- `Chunk` -- `id`, `doc_id`, `ord`, `text`, `char_span`, `section_path`.
  Embedding lives in the vector store, keyed by `chunk.id`.
- `CorpusGraph` -- nodes are `Document` and `Chunk` ids; edges are typed:
  - `contains`: doc -> chunk
  - `similar`: chunk <-> chunk (kNN over embeddings)
  - `cites`: doc -> doc (only if the parser found citations; optional)
  - `co_section`: chunk <-> chunk (same section in same doc)

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

## People and articles are separate kinds

Articles and people are separate `kind`s with separate directories
(`wiki/articles/` and `wiki/people/`) and separate artifact templates.
An article page is built from chunks that *describe an idea*; a person
page is built from chunks that *attribute work to a name* plus document
metadata.

Person pages are written by the model just like article pages. Author
metadata is assembled at ingest/distill time by
`distill/write/author_context.py::build_author_context` and attached to
the `WriteRequest` as `author_context` (primary publications, cited
works, collaborators, year range, affiliations) for grounding. The
writer produces biographical prose in Wikipedia voice; the
"appears in this corpus" phrasing is banned. The writer is robust to
missing `author_context` (non-author persons mentioned only in chunk
prose): the lead degrades to `**Name** is credited with [contribution
grounded in evidence]`.

## Package layout

```
src/wikify_simple/
  __init__.py
  models.py                 # Document, Chunk, CorpusGraph, Evidence,
                            # WikiPage, WikiGraph, Stage, Run
  paths.py                  # the only module that knows where things live

  infra/                    # shared infrastructure (no LLM, no domain logic)
    __init__.py
    cost_meter.py           # CostMeter: per-call accounting + budget gate
    cache.py                # ExtractCache: deterministic per-chunk cache
    context_envelope.py     # ContextEnvelope: priority-fill prompt builder
    tokens.py               # tokeniser-agnostic token-count helper
    embedding.py            # switchable embedding backend

  contracts/                # protocols and schemas; no concrete LLM code
    __init__.py
    protocols.py            # Extractor, Writer, Orchestrator (Protocol)
    schema.py               # structured request/response shapes (Pydantic v2)
    normalize.py            # text normalization for quote validation
    roles.py                # Role enum + per-role spec lists

  bindings/                 # the only place model dispatch lives
    __init__.py
    fake.py                 # deterministic fakes for tests + dry runs
    heuristic.py            # inline regex extraction + article assembly
    file_dispatch.py        # file-based dispatch: writes/reads request and
                            # response files at well-known paths

  store/                    # disk I/O for corpus and wikis
    __init__.py
    corpus.py               # read documents/chunks/embeddings
    vectors.py              # thin vector-db wrapper
    wiki_files.py           # read/write wiki page .md files
    wiki_index.py           # bundle index (_index.json, _index.md)
    images_index.py         # per-corpus image index
    bundle_embeddings.py    # cached page-body embeddings for eval
    corpus_profile.py       # PageRank, Louvain, betweenness

  ingest/                   # corpus build
    __init__.py
    parsers/                # one parser per kind
      pdf.py
      docx.py
      pptx.py
      html.py
      markdown.py
      registry.py
    chunker.py              # markdown -> [Chunk]
    images.py               # extract figures + captions + slide images
    corpus_graph.py         # builds CorpusGraph
    topics.py               # topic extraction (used by GT-C in eval)
    refresh.py              # idempotent corpus refresh entry point

  distill/                  # strategies and their primitives
    __init__.py
    sampler.py              # Sampler protocol + LevyMixSampler
    schedule.py             # Schedule protocol + static/adaptive variants
    pipeline.py             # fixed list of stages every cell runs
    iteration.py            # create/refine/merge operations
    policy.py               # rule_policy / llm_policy shared interface
    extract/                # extraction subpackage
    write/                  # write subpackage
    strategies/
      __init__.py
      explore.py            # cell E
      mixed.py              # cell M
      exploit.py            # cell X

  eval/                     # metrics and audit
    __init__.py
    bundle.py
    metrics.py
    audit.py

  render/html/              # static site renderer
    templates/
    static/

  prompts/                  # prompt templates
    registry.py
    style_guide.md
    fields/
    artifact_types/

  cli.py                    # thin Typer adapter; one command per verb

src/wikify_simple/
  architecture.md           # this file
  strategies.md             # sampler / schedule / tiering cube
  metrics.md                # M1-M6 + GT-C + GT-P
  runbook.md                # operator runbook: CLI flags, environment, workflows
  test-run-playbook.md      # reproducible test-run procedure + quality review
  plans/
    structural-improvements.md   # active roadmap: Phases 1-6
```

## Dependency direction

```
                          models.py
                              ^
                              |
        +---------------------+---------------------+
        |                     |                     |
       infra/               store/              eval/
        ^                     ^                     ^
        |                     |                     |
        +----+----+           |                     |
             |    |           |                     |
      contracts/ paths.py     |                     |
             ^                |                     |
             |                |                     |
        +----+-----+----------+                     |
        |                                           |
     distill/                                       |
        ^                                           |
        |                                           |
     bindings/  <-- only place that talks to file dispatch
        ^
        |
      cli.py  <-- thin adapter; wires bindings into distill
```

Strategies in `distill/strategies/*` depend on `contracts/protocols.py` and
`infra/`. They never import `bindings/`. The CLI wires a concrete binding
(real `file_dispatch.py` or `fake.py`) into the strategy at run time.

## Coding standards

1. **Functions over namespace classes.** A `class` is justified only by
   shared mutable state or a real polymorphism need.
2. **Constructor injection over module-level globals.** Strategies, the
   cost meter, the cache, the context envelope, and the agent bindings
   are all passed in.
3. **Modules <= 400 LOC.** Split anything that grows past 600.
4. **Protocols for real extension points.** `Extractor`, `Writer`,
   `Orchestrator`, `Embedder`, `ChunkStore`, `Sampler` are `Protocol`
   classes. Concrete implementations live in their own modules.
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

The full types live in `models.py` and in `contracts/protocols.py`:

```python
# contracts/protocols.py
class Extractor(Protocol):
    def extract(self, request: ExtractRequest) -> ExtractResponse: ...

class Writer(Protocol):
    def write(self, request: WriteRequest) -> WriteResponse: ...

class Orchestrator(Protocol):
    def step(self, state: OrchState) -> OrchAction: ...
```

`ExtractRequest` carries the `target_chunk` and the `canonical_titles`
pool. The Protocol does not expose tokens or models -- it exposes
*content*. The binding is responsible for turning content into a model
call. The strategy never sees the SDK.

```python
# distill/sampler.py
class Sampler(Protocol):
    def next_batch(self, state: SamplerState, k: int) -> list[ChunkRef]: ...

@dataclass(frozen=True)
class LevyMixSampler:
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
