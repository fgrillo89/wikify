# Wikify Architecture

## Purpose
Wikify is a local-first corpus platform with two product surfaces built on the
same underlying source and structured state:

- `wiki`: a general-purpose wiki builder and manager for curated knowledge pages
- `papers`: a research-writing surface for papers, reviews, and presentations

The wiki is the primary knowledge product. It must remain domain-neutral and
work across scientific, technical, historical, legal, policy, and mixed
document corpora.

## Current Docs
The current documentation surface is intentionally small.

- `docs/architecture.md`: architectural boundaries and system model
- `docs/project-status.md`: current state and active priorities
- `docs/refactor/wiki-deep-refactor-plan.md`: active implementation plan
- `docs/design/wiki-runtime-refactor-plan.md`: focused design note for visible
  wiki plus operational state

Older design material has been moved to archive.

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

## Migration State
The repo is in transition from an organically grown mixed layout toward the
boundary-driven shape above. The active implementation sequence lives in
`docs/refactor/wiki-deep-refactor-plan.md`.
