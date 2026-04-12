# Wikify Architecture

> **Current state (2026-04)**: the active track is `wikify_simple`. The original `wikify` package has been archived under `archive/wikify/` — the text below from "Architectural Principles" onward describes the legacy structure and is kept for historical context. For current work, the authoritative architecture documents are:
>
> - [`src/wikify_simple/architecture.md`](../src/wikify_simple/architecture.md) — package layout, dependency direction, coding standards, key types and protocols
> - [`src/wikify_simple/strategies.md`](../src/wikify_simple/strategies.md) — sampler / schedule / tiering cube + anchor cells
> - [`src/wikify_simple/metrics.md`](../src/wikify_simple/metrics.md) — M1-M6 + GT-C + GT-P
> - [`src/wikify_simple/runbook.md`](../src/wikify_simple/runbook.md) — operator runbook and CLI reference
> - [`src/wikify_simple/test-run-playbook.md`](../src/wikify_simple/test-run-playbook.md) — reproducible test-run procedure
> - [`src/wikify_simple/plans/structural-improvements.md`](../src/wikify_simple/plans/structural-improvements.md) — active structural roadmap (Phases 1-6)

## Purpose
Wikify is a local-first corpus platform with two product surfaces built on the
same underlying source and structured state:

- `wiki`: a general-purpose wiki builder and manager for curated knowledge pages
- `papers`: a research-writing surface for papers, reviews, and presentations

The wiki is the primary knowledge product. It must remain domain-neutral and
work across scientific, technical, historical, legal, policy, and mixed
document corpora.

## Legacy documentation surface

The legacy `wikify` package under `archive/wikify/` had these supporting docs. They describe the pre-restructure product boundaries and are kept for historical reference only:

- `docs/architecture.md`: architectural boundaries and system model (this file)
- `docs/project-status.md`: legacy project status
- `docs/refactor/wiki-deep-refactor-plan.md`: legacy implementation plan
- `docs/design/wiki-runtime-refactor-plan.md`: legacy design note

Older design material lives under `docs/archive/`. Active refactor / roadmap docs for `wikify_simple` live under `src/wikify_simple/plans/` — NOT under `docs/refactor/`.

## Architectural Principles
1. Separate product boundaries clearly: `core`, `ingest`, `wiki`, `papers`.
2. Keep visible wiki files and structured state aligned. They support each
   other and should not become competing truths.
3. Treat graph metrics and run observability as first-class wiki subsystems.
4. Keep adapters thin: CLI, MCP, and agent/runtime-specific docs should compose
   domain surfaces rather than own domain logic.
5. Prefer locality of behavior. Code that changes together should live
   together.

## Top-Level Boundaries

```text
core
  ^
  |
ingest
  ^ \
  |  \
wiki  papers

cli / mcp / runtime adapters sit at the edge.
```

### `core`
Shared infrastructure used by more than one product boundary.

Examples:

- config
- SQLite/session management
- embeddings/vector store clients
- LLM client wrappers

### `ingest`
Source parsing and corpus-wide enrichment.

Examples:

- file parsing
- chunking and metadata extraction
- embeddings and precompute artifacts
- BibTeX rebuilds
- corpus refresh workflows
- corpus projections such as vault generation when still owned at the corpus
  level

### `wiki`
General-purpose wiki creation, query, maintenance, presentation, graph
reasoning, and observability.

Examples:

- page contracts and mutation envelopes
- concept discovery
- graph construction and topology metrics
- article writing and linking
- epoch, query, maintain, and campaign operations
- wiki HTML/dashboard presentation
- run telemetry and cross-epoch comparisons

### `papers`
Research writing built on the shared corpus substrate, and later optionally on
public wiki surfaces.

Examples:

- exploration and retrieval for writing
- planning, drafting, verification, and revision
- evaluation and export
- paper-specific CLI and MCP surfaces

`wiki` must not depend on `papers`. `papers` may depend on `wiki` only through
public wiki contracts or runtime surfaces.

## High-Level System Flow

```text
Raw sources
PDF, DOCX, PPTX, Markdown, HTML, notes, web captures
        |
        v
Ingest
parsing -> chunks -> metadata -> citations -> figures/equations
        |
        v
Structured substrate
SQLite + embeddings + cache/precompute + corpus projections
        |
        +---------------------------+
        |                           |
        v                           v
Wiki runtime                    Papers runtime
epoch/query/maintain/...        generate/evaluate/revise/...
        |                           |
        v                           v
Visible wiki                    Generated outputs
data/wiki/                      data/output/
```

## Target Package Map

```text
src/wikify/
|-- core/
|   |-- config.py
|   |-- llm/
|   `-- store/
|
|-- ingest/
|   |-- service.py
|   |-- refresh.py
|   |-- corpus_refresh.py       # temporary shim during migration
|   |-- bibtex.py
|   |-- parsers/
|   |-- extract/
|   `-- vault/                  # if still owned by corpus refresh
|
|-- wiki/
|   |-- contracts.py
|   |-- runtime.py
|   |-- operations/
|   |-- discovery/
|   |-- concepts/
|   |-- graph/
|   |-- articles/
|   |-- observability/
|   |-- presentation/
|   |-- people.py
|   |-- persona.py
|   |-- figure_enrichment.py
|   `-- legacy/
|
|-- papers/
|   |-- agent/
|   |-- generate/
|   |-- retrieve/
|   |-- evaluate/
|   |-- export/
|   |-- runtime.py
|   |-- cli.py
|   `-- mcp.py
|
|-- cli.py
`-- mcp_server.py
```

This is the target shape being executed in
`docs/refactor/wiki-deep-refactor-plan.md`.

## Wiki System Model

### Visible Layer
The visible wiki is the primary human-facing artifact.

Canonical layout:

- `data/wiki/index.md`
- `data/wiki/log.md`
- `data/wiki/articles/`
- `data/wiki/sources/`
- `data/wiki/_meta/`

Visible pages are curated markdown pages with shared frontmatter contracts such
as `page_type`, `domains`, and `source_ids`.

### Structured Layer
Structured state supports the visible wiki and agent/runtime behavior.

Examples:

- SQLite records for pages, provenance, domain membership, findings, and runs
- embeddings for retrieval and similarity
- graph edges and graph-derived metrics
- telemetry and snapshot metrics

This layer exists to support:

- retrieval
- provenance
- graph reasoning
- maintenance
- prioritization
- comparison across runs and epochs

It should not silently replace curated page truth.

### Discovery And Extraction Layer
Concept discovery is a first-class wiki subsystem. It sits between ingest and
article writing and must be understandable on its own rather than buried inside
`epoch`.

Its job is to turn parsed source structure plus multimodal evidence into:

- extraction notes
- candidate concepts, entities, and relationships
- provenance and coverage records
- scheduling state for future epochs

The discovery layer should be organized so strategy can change without
rewriting the rest of the wiki.

The preferred execution model is a configurable DAG:

- nodes are explicit processing steps
- edges represent typed artifact dependencies
- node inputs and outputs are declared rather than implied
- a workflow config chooses which nodes run, with which parameters, and in
  what dependency graph

Target responsibilities:

- `wiki/discovery/`
  - source triage and document profiling
  - extraction-unit planning
  - document-level synopsis passes
  - chunk-, figure-, and table-level extraction passes
  - strategy registry and configuration
  - finite-coverage scheduling across epochs
  - extraction-note emission
- `wiki/concepts/`
  - canonical concept records
  - merge and deduplication
  - evidence, occurrences, parameters, and relationship persistence

This split is intentional:

- discovery decides what to read, in what order, with which model and prompt
- concepts owns the canonical structured knowledge produced by discovery

### Discovery Pipeline
The target discovery pipeline is a default DAG shape:

```text
Parsed document + media inventory + embeddings + metadata
        |
        v
Document profile
document type, structure quality, available modalities, priority
        |
        v
Strategy planner
choose strategy family and pass sequence for this document
        |
        v
Extraction units
document synopsis units, chunk units, image units, table units, mixed units
        |
        v
Small-model passes
structured notes / candidate findings / coverage marks
        |
        v
Resolution
deterministic parsing + optional second-pass consolidation
        |
        v
Canonical concept and evidence state
        |
        v
Graph, article writing, maintenance, observability
```

This design allows multiple strategies, for example:

- synopsis-first then targeted deepening
- full chunk sweep with note dumping
- multimodal extraction on figures before chunk deepening
- document-type-specific flows for publications, slide decks, HTML captures,
  notes, or mixed corpora

It should also allow non-linear variants, for example:

- multiple extraction branches feeding one consolidation node
- optional note-dump branches for later parsing
- retry or escalation nodes when a first pass is low confidence
- graph updates that depend on canonical concept state but not directly on page
  writing

### Discovery Data Contracts
To keep the pipeline transparent and configurable, the main intermediate data
objects should be explicit and serializable.

Examples:

- `DocumentProfile`: document type, parser confidence, structural sections,
  modality inventory, token budget hints
- `ArtifactRef`: typed reference to one persisted artifact or collection
- `DiscoveryStrategy`: ordered pass definition plus coverage policy
- `ExtractionUnit`: one addressable unit to interrogate
- `ExtractionNote`: a model-produced note tied to one or more units
- `CandidateConcept`: pre-merge concept/entity hypothesis
- `CoverageRecord`: what has been processed, skipped, deferred, or retried
- `DagNodeSpec`: one reusable step definition
- `DagRunSpec`: one configured workflow instance

Each node should consume and emit typed artifacts rather than reaching into
global mutable state. That keeps the workflow composable, testable, and easy to
inspect.

### Workflow Configuration
Workflow definitions should be externalizable, preferably as YAML, so the
system can compare strategies without code churn.

A workflow config should be able to express:

- node set and dependency edges
- strategy family and per-node parameters
- model selection and escalation rules
- budgets such as synopsis length, chunk count, and image count
- document-type routing overrides
- note persistence and second-pass consolidation behavior
- epoch coverage policy and retry limits

The code should validate YAML configs into typed runtime objects before
execution. YAML is a control surface, not the internal source of truth.

This should take inspiration from ML experiment tooling such as Hydra-style
config composition, but the architecture should not require Hydra specifically.
The value is in the operating model:

- hierarchical config composition
- named experiment families
- per-run overrides
- reproducible config snapshots
- sweep-friendly parameterization

Whether a third-party config library adds enough value should be decided
pragmatically. A library is justified only if it materially improves
composition, validation, sweep ergonomics, and run reproducibility without
making the workflow harder to understand.

These should be inspectable in structured state and exportable for experiments.

### Document Types And Strategies
Discovery strategy should not be hard-coded around research articles.

The planner should choose behavior based on document profile, for example:

- publications: synopsis plus methods/results/deepening may still be useful
- slide decks: slide-level sweep plus image-heavy extraction
- HTML or markdown notes: heading-tree traversal and entity/concept sweep
- mixed captures: fallback all-unit scan when structure is weak

Section summaries are therefore a strategy input, not a universal requirement.
If ingest can provide trustworthy section summaries, discovery may use them. If
not, discovery should be able to build a synopsis from representative chunks or
other document units.

The current 3000-character publication summary cap should be treated as an
implementation detail to be made configurable. Strategy definitions should own
limits like synopsis budget, chunk budget, and model tier rather than baking
them into one path.

### Finite Coverage Across Epochs
Epoch scheduling may prioritize some units earlier, but it must not starve the
rest of the corpus.

The intended contract is:

- every eligible extraction unit is eventually processed when enough epochs run
- prioritization affects order and repetition, not permanent exclusion
- exploration and targeted deepening are complements to eventual full coverage,
  not replacements for it

A dedicated coverage surface should own these rules rather than scattering them
across orchestration code.

### Multimodal Discovery
Concept discovery must include non-text evidence when available.

The discovery layer should be able to create extraction units for:

- figures and figure captions
- tables
- slide images
- page screenshots or rendered regions for visually meaningful layouts

Text-only chunk extraction remains useful, but it is not sufficient for a
general wiki built from mixed document types.

### Visible And Structured Coherence
The intended contract is:

- markdown pages are the authoritative human-facing synthesis artifacts
- structured state is rebuilt, reconciled, and updated to stay aligned with
  those pages and with source-backed evidence
- reconciliation should favor rebuilding structured state over overwriting
  curated pages

Agents and runtime flows should be able to use both:

- visible pages for synthesized answers, navigation, and review
- structured state for retrieval, embeddings, graph lookups, provenance,
  ranking, and maintenance decisions

### Graph And Observability
Graph computation and observability are explicit parts of wiki architecture.

`wiki/graph/` owns:

- graph construction
- importance scoring
- topology metrics
- routing support
- domain and community analysis

`wiki/observability/` owns:

- run lifecycle tracking
- stage timing and counters
- page deltas
- retrieval/tool/token telemetry
- wiki snapshots
- human-readable epoch logs
- machine-readable run exports

These are not implementation details hidden inside orchestration code.

`wiki/discovery/` should also emit first-class observability:

- strategy id and version
- model used per pass
- extraction units planned and processed
- coverage deltas by epoch
- note counts and consolidation outcomes
- multimodal pass usage
- cost and yield by strategy

### Runtime Surface
The wiki runtime should expose stable operations such as:

- `epoch`
- `query`
- `maintain`
- `campaign`
- `reconcile_state`
- `export_metrics`
- `compare_runs`

`wiki/runtime.py` is the small facade over those operations.

## Papers System Model
The papers surface is intentionally separate from wiki management.

Responsibilities:

- corpus exploration for writing
- retrieval and handoff into writing
- generation, evaluation, revision, and export

The papers surface may later consume public wiki outputs for richer retrieval,
but it should not own wiki page contracts, wiki graph logic, or wiki
observability.

## Storage Model

### Durable Source And Structured Data
- `data/papers.db`: relational state
- `data/chromadb/`: embeddings
- `data/cache/precomputed/`: precompute artifacts
- `data/library.bib`: corpus-level BibTeX output

### Visible Knowledge Artifacts
- `data/wiki/`: curated wiki pages plus `_meta`
- `data/output/`: generated paper/presentation outputs

## Adapters
CLI, MCP, and runtime-specific instructions are adapters, not the core
architecture.

Examples:

- `src/wikify/cli.py`
- `src/wikify/mcp_server.py`
- `AGENTS.md`
- optional runtime-specific guidance such as `CLAUDE.md`

Architecture should remain valid even if any one adapter changes or disappears.

## wikify_simple -- successor pipeline

`wikify_simple` (`src/wikify_simple/`) is a standalone wikification pipeline
designed to replace the legacy `wikify.wiki` surface. It is simpler and
file-based, with no SQLite or ChromaDB dependency.

### Package layout

```text
src/wikify_simple/
|-- cli.py                  # ingest, distill, eval, html, query, field-detect
|-- models.py               # shared domain types
|-- paths.py                # on-disk path conventions (CorpusPaths, BundlePaths)
|
|-- ingest/                 # parse -> chunk -> embed -> graph -> citations
|   |-- parsers/            #   pdf.py, markdown.py, docx/pptx/html stubs
|   |-- embedder.py         #   switchable backend (sentence_transformers / hash)
|   |-- graph_builder.py    #   corpus concept graph
|   |-- citations.py        #   BibTeX generation
|   |-- topics.py           #   topic extraction and deduplication
|   |-- metadata.py         #   title, authors, year, DOI extraction
|   `-- doc_markdown.py     #   per-doc Obsidian markdown
|
|-- distill/                # extract -> canonicalize -> write -> crosslink
|   |-- pipeline.py         #   main distill loop
|   |-- canonicalize.py     #   alias-merge candidates into pages
|   |-- author_pages.py     #   deterministic + model-enriched person pages
|   |-- crosslink.py        #   inter-page link discovery
|   |-- field_detect.py     #   auto-detect corpus field from topics
|   `-- strategies/         #   config registry and factory for E/M/X
|
|-- eval/                   # metrics -> audit -> bundle analysis
|   |-- metrics.py          #   M1-M6, GT-P, GT-C, g_links, g_evidence
|   |-- audit.py            #   per-bundle _audit.md
|   `-- bundle.py           #   bundle loader and page parser
|
|-- store/                  # all on-disk state management
|   |-- corpus.py           #   corpus directory operations
|   |-- vectors.py          #   numpy .npz vector store
|   |-- wiki_files.py       #   page file I/O
|   |-- wiki_index.py       #   WikiIndex (_index.json + _index.md)
|   |-- images_index.py     #   ImageIndex (per-doc figure lookup)
|   |-- page_naming.py      #   natural Wikipedia-style filenames
|   `-- bundle_embeddings.py #  per-bundle page embeddings
|
|-- render/html/            # static site generation
|   |-- builder.py          #   Jinja2 renderer
|   |-- templates/          #   HTML templates
|   `-- static/             #   CSS + JS
|
|-- infra/                  # shared infrastructure
|   |-- cache.py            #   SHA256-keyed extract cache
|   |-- cost_meter.py       #   token-based cost accounting (S/M/L tiers)
|   |-- context_envelope.py #   request/response envelope
|   |-- embedding.py        #   embedder factory
|   |-- tokens.py           #   token counting
|   `-- role.py             #   extractor/writer/querier roles
|
|-- agents/                 # model interaction contracts
|   |-- schema.py           #   Pydantic v2 request/response schemas
|   |-- protocols.py        #   Extractor/Writer/Querier protocols
|   `-- text_normalize.py   #   NFKC + dash + bracket + emphasis normalization
|
|-- bindings/               # model binding implementations
|   |-- fake.py             #   deterministic stub for CI
|   `-- claude_code.py      #   file-based dispatcher for Claude Code
|
`-- prompts/                # layered prompt system
    |-- registry.py         #   prompt loader
    |-- style_guide.md      #   global writing rules
    |-- fields/             #   per-field guides (materials_science.yaml, etc)
    |-- artifact_types/     #   wiki_article.yaml, person_page.yaml
    |-- extract.yaml        #   extractor prompt template
    |-- write.yaml          #   writer prompt template
    `-- query.yaml          #   query answerer prompt template
```

### Data flow

```text
Input PDFs / DOCX / MD
        |
        v
    ingest/
    parse -> chunk -> embed -> graph -> citations -> images -> topics
        |
        v
    corpus directory (data/wikify_simple/corpora/{name}/)
    markdown/ chunks/ docs/ vectors.npz graph.json topics.json images/
        |
        v
    distill/
    extract (per-chunk) -> canonicalize -> author_pages -> write -> crosslink
        |
        v
    bundle directory (data/wikify_simple/wikis/{name}/{strategy}_{budget}_{seed}_{ts}/)
    concepts/*.md  people/*.md  _index.json  _run.json  _audit.md  _metrics.json
        |
        v
    render/html/
    Jinja2 templates + CSS -> static HTML site (data/wikify_simple/html/{name}/)
```

### Key design decisions

- **Files on disk, no database.** All state is inspectable files: JSON, JSONL,
  markdown, numpy .npz. No SQLite, no ChromaDB. The vector store is a single
  numpy matrix (adequate for <= 10^4 chunks). This makes the entire pipeline
  greppable and debuggable without special tooling.

- **Dispatcher-based binding.** Python never calls an LLM directly. The binding
  writes a `.request.json` file and polls for a `.response.json`. The fake
  binding responds deterministically (for CI). The claude_code binding lets the
  outer Claude Code session drive the model. This keeps the LLM loop outside
  Python and makes the pipeline binding-agnostic.

- **Layered prompts.** The writer prompt is assembled from four layers: a global
  style guide, a field-specific guide (auto-detected from corpus topics), an
  artifact type template, and a corpus persona. Each layer is a separate file
  under `prompts/`. This allows domain-appropriate writing without code changes.

- **Deterministic author pages.** Person pages for corpus authors are generated
  without model calls: lead sentence, publication list, collaborators, related
  concepts. Pages are enriched with model-extracted context only when evidence
  accumulates. Non-author people mentioned in text also get pages.

- **Natural page names.** Page files use Wikipedia-style titles
  (`Atomic Layer Deposition.md`, not `concept-atomic-layer-deposition.md`). The
  `kind` field in frontmatter distinguishes page types.

- **Tolerant quote validation.** Evidence quotes are validated as verbatim
  substrings of chunk text, but both sides are NFKC-normalized with dash,
  bracket, and emphasis stripping before comparison. Quotes are stored verbatim.

### Relationship to legacy wikify

`wikify_simple` is the successor to the legacy `wikify.wiki` surface. It was
designed to be simpler and file-based. Legacy code is ported function-by-function
where needed (PDF parser, image extraction, markdown cleanup, HTML renderer).
The two packages coexist in the repo; `wikify_simple` does not import from
`wikify` at runtime.

## Migration State
The repo is in transition from an organically grown mixed layout toward the
boundary-driven shape above. The active implementation sequence lives in
`docs/refactor/wiki-deep-refactor-plan.md`.
