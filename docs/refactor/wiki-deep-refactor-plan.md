# Wiki Deep Refactor Plan

## Summary
This revision replaces the earlier move-heavy plan with a simpler, more local
refactor strategy that aligns with:

- [docs/architecture.md](../architecture.md)
- [docs/project-status.md](../project-status.md)
- [docs/design/wiki-runtime-refactor-plan.md](../design/wiki-runtime-refactor-plan.md)

The key architectural decision is to separate the codebase into four stable
boundaries:

- `core`: shared infrastructure only
- `ingest`: source parsing and corpus-wide enrichment
- `wiki`: general-purpose wiki creation, query, maintenance, and presentation
- `papers`: research-paper and presentation generation

The wiki remains the primary curated knowledge product, but it must stay
general across domains and source types. Research-paper generation is a
separate product area that may consume wiki outputs through public contracts,
but must not shape the wiki package layout or documentation.

This plan is designed to be executable by parallel agents with disjoint write
sets, clear dependencies, and temporary compatibility shims.

## Why This Revision
The previous plan had the right intent, but it needs tightening in five areas.

1. It introduced extra package churn (`ingestion`, `frontend`) that does not
   clearly improve locality of behavior.
2. It treated some corpus-wide capabilities such as BibTeX generation and the
   vault as if they belonged to paper generation, when they are better owned by
   corpus ingestion/refresh.
3. It still inherited framework-specific language from the current docs,
   especially around Claude-specific skills and runtime assumptions.
4. It mixed product-boundary work with broad cleanup work, making parallel
   execution harder and riskier.
5. It did not make the wiki's corpus-agnostic design explicit enough. The wiki
   must work for scientific corpora, but also for legal, historical,
   technical, policy, or mixed document sets.

## Refactor Goals
1. Separate research writing from wiki building at the package, CLI, MCP, and
   documentation levels.
2. Keep visible wiki artifacts and operational state aligned with the active
   runtime plan.
3. Improve navigability through local feature packages, smaller modules, and
   explicit boundaries.
4. Keep the wiki domain-neutral and source-neutral at its public contracts.
5. Preserve behavior during the move with import shims and smoke tests.
6. Make the migration safe for multiple parallel agents.
7. Make discovery and extraction transparent, configurable, multimodal, and
   document-type aware.

## Non-Goals
- No redesign of the visible wiki layout described in
  `docs/design/wiki-runtime-refactor-plan.md`.
- No schema rewrite of existing SQLite models during this refactor.
- No behavior change inside the research-writing pipeline beyond package
  separation and adapter cleanup.
- No provider-specific or framework-specific architecture changes.
- No speculative shared `utils.py` layer.

## Design Constraints

### Boundary Rules
The dependency direction after the refactor should be:

```text
core
  ^
  |
ingest
  ^ \
  |  \
wiki  papers

cli / mcp / runtime adapters sit at the edge and may compose multiple domains.
```

Rules:

- `wiki` must not import from `papers`.
- `ingest` must not import from `wiki` or `papers`.
- `papers` may depend on `wiki` only through public wiki contracts/runtime
  surfaces, not through wiki internals.
- Shared code goes into `core` only when it is genuinely used by at least two
  product boundaries.

### Locality Of Behavior
Behavior that changes together should live together.

- Wiki writing, linking, page contracts, wiki HTML, and wiki dashboard stay
  under `src/wikify/wiki/`.
- Corpus refresh, BibTeX generation, and corpus projections stay under
  `src/wikify/ingest/`.
- Research writing, evaluation, export, and paper-specific retrieval stay under
  `src/wikify/papers/`.
- Avoid introducing broad horizontal packages like `frontend/` unless there are
  multiple products sharing the same implementation.

### Coding Standards
These rules apply to every touched slice.

1. Prefer functions over namespace-style classes.
2. Prefer constructor injection or explicit function arguments over module-level
   mutable singletons.
3. Keep modules small and responsibility-focused. Target `<= 400` LOC, and
   split any file that grows beyond `600` LOC unless there is a strong reason
   not to.
4. Use `Protocol` or `ABC` for real extension points with multiple
   implementations.
5. Keep imports at top of file except while breaking a cycle on the way to
   removing it.
6. Prefer enums and dispatch tables over long `if/elif` chains when branching
   on stable kinds or modes.
7. Add a short top-of-file docstring stating the module's single
   responsibility.
8. Do not create grab-bag helper modules. Shared helpers belong in the nearest
   responsible package.
9. Keep framework-specific runtime code out of domain modules.
10. Distinguish metric definition from metric consumption. The code that
    computes graph or telemetry metrics should be discoverable without reading
    orchestration code.

### Domain Neutrality
The wiki is not a science-only feature.

- Public wiki contracts, docs, and module names should prefer `source`,
  `document`, `page`, `provenance`, and `corpus` language over
  `paper`-specific language where possible.
- Science-specific heuristics, prompts, or examples must not define wiki core
  behavior.
- Domain-specific examples and benchmark corpora should be documented as
  examples, not as the product definition.

### Graph And Epoch Observability
Graph metrics and epoch telemetry need explicit ownership in the design.

- Graph construction, graph scoring, topology metrics, and graph persistence
  should not be hidden inside epoch orchestration.
- Metrics that feed routing, prioritization, maintenance, convergence, and
  dashboards should come from a stable wiki-owned surface.
- Human-readable epoch logs and machine-readable run exports should be treated
  as two views over the same underlying run data.
- Cross-epoch comparison should be an intentional part of the runtime model,
  not an afterthought attached to `wiki epoch --status`.

### Visible And Structured Coherence
The visible wiki and the structured state must move together.

- Markdown pages remain the authoritative human-facing knowledge artifacts.
- Structured state such as the SQLite DB, embeddings, graph edges, provenance,
  and telemetry exists to support retrieval, routing, maintenance, comparison,
  and agent reasoning over the wiki.
- The system must not allow the visible wiki and the structured state to drift
  into competing truths for long-lived behavior.
- Reconciliation should prefer rebuilding structured state from durable visible
  and source-backed evidence, rather than silently overwriting curated pages.
- Agent and runtime flows should be able to leverage both:
  - visible pages for synthesized knowledge and presentation
  - structured state for retrieval, graph reasoning, provenance, ranking, and
    maintenance decisions

### Discovery Transparency And Configurability
Concept discovery is one of the core behaviors of the product and should no
longer be hidden as an implementation detail of `epoch.py` plus one large
module.

The target design is an explicit discovery subsystem with well-defined steps
and data contracts:

- document profiling and triage
- strategy selection
- extraction-unit planning
- model passes over text and non-text units
- extraction-note persistence
- candidate concept/entity resolution
- canonical merge and evidence persistence
- epoch-to-epoch coverage tracking
- DAG-based workflow execution over typed artifacts

Design rules:

1. Discovery planning must be separate from canonical concept persistence.
2. Strategy selection must be configurable by document type and experiment
   profile rather than hard-coded around research articles.
3. Intermediate discovery artifacts such as extraction notes and coverage
   records must be inspectable and exportable.
4. Section summaries are optional inputs, not required architecture. They may
   come from ingest when available, or be synthesized by discovery when not.
5. Parameters such as synopsis length, chunk budget, modality usage, and model
   tier must be owned by strategy configuration rather than baked into one code
   path.
6. Discovery scheduling must guarantee eventual processing of all eligible
   extraction units when enough epochs run. Prioritization may affect order,
   but not permanent starvation.
7. Discovery must support multimodal units such as figures, tables, and slide
   images in addition to text chunks.
8. The system should support experimentation with alternative strategies, such
   as full chunk sweeps that dump notes for a second consolidation pass.
9. Wikification workflow steps should be represented as explicit DAG nodes with
   declared inputs and outputs, not as hidden sequencing inside large runtime
   functions.
10. Workflow configuration may live in YAML, but execution must always go
    through validated typed config objects inside Python.
11. The config system should borrow good ideas from ML experiment tooling such
    as config composition, named runs, overrides, and reproducible snapshots,
    but should not hard-depend on a specific library unless that clearly
    reduces complexity.

## Target Layout
The target source tree is intentionally conservative: it creates clearer
boundaries without renaming packages that are already good enough.

```text
src/wikify/
|-- core/                       # shared infra only
|   |-- config.py
|   |-- llm/
|   `-- store/
|
|-- ingest/                     # source parsing + corpus-wide enrichment
|   |-- service.py              # public ingest boundary
|   |-- refresh.py              # split from corpus_refresh.py
|   |-- corpus_refresh.py       # temporary shim during migration
|   |-- bibtex.py               # library.bib lifecycle
|   |-- parsers/
|   |   |-- pdf.py
|   |   |-- docx.py
|   |   |-- pptx.py
|   |   |-- html.py
|   |   |-- markdown.py
|   |   `-- registry.py
|   |-- extract/
|   `-- vault/                  # if retained, owned by ingest/refresh
|
|-- wiki/                       # general wiki creation and management
|   |-- contracts.py
|   |-- runtime.py
|   |-- operations/
|   |   |-- epoch.py
|   |   |-- query.py
|   |   |-- maintain.py
|   |   |-- campaign.py
|   |   |-- reconcile.py
|   |   `-- metrics.py
|   |-- discovery/
|   |   |-- contracts.py
|   |   |-- dag.py
|   |   |-- executor.py
|   |   |-- planner.py
|   |   |-- config.py
|   |   |-- strategies.py
|   |   |-- scheduler.py
|   |   |-- units.py
|   |   |-- artifacts.py
|   |   |-- nodes.py
|   |   |-- notes.py
|   |   |-- multimodal.py
|   |   `-- registry.py
|   |-- concepts/
|   |   |-- records.py
|   |   |-- merge.py
|   |   |-- evidence.py
|   |   `-- persistence.py
|   |-- graph/
|   |   |-- build.py
|   |   |-- importance.py
|   |   |-- topology.py
|   |   |-- domains.py
|   |   `-- routing.py
|   |-- articles/
|   |-- observability/
|   |   |-- runs.py
|   |   |-- stages.py
|   |   |-- snapshots.py
|   |   |-- logs.py
|   |   `-- export.py
|   |-- presentation/
|   |   |-- html.py
|   |   |-- dashboard.py
|   |   |-- layout.py
|   |   `-- templates/
|   |-- people.py
|   |-- persona.py
|   |-- figure_enrichment.py
|   `-- legacy/                 # optional home for sitemap-first code if kept
|
|-- papers/                     # research writing product
|   |-- agent/
|   |-- generate/
|   |-- retrieve/
|   |-- evaluate/
|   |-- export/
|   |-- runtime.py
|   |-- cli.py
|   `-- mcp.py
|
|-- cli.py                      # thin root adapter
`-- mcp_server.py               # thin root adapter
```

Notes:

- `vault/` is not automatically classified as a paper-generation concern. If it
  remains a corpus projection built during refresh, it belongs near `ingest`.
- `zotero/` should not remain the owner of generic BibTeX lifecycle behavior.
  The reusable `library.bib` logic belongs in `ingest/bibtex.py`.
- `wiki/presentation/` keeps wiki UI surfaces local to the wiki package. This
  is a deliberate simplification over creating a separate `frontend/` package.
- `wiki/discovery/` owns source triage, extraction-unit planning, strategy
  selection, DAG workflow specs, multimodal discovery passes, note emission,
  and epoch coverage.
- `wiki/concepts/` owns canonical concept records plus merge and evidence
  persistence.
- `wiki/graph/` owns graph computation and graph-derived metrics so the rest of
  the wiki can consume them through stable functions.
- `wiki/observability/` owns run lifecycle telemetry, epoch logs, wiki
  snapshots, and exported run summaries.

## Public Runtime Surfaces
These are the only intended top-level entry points after the refactor:

- `wikify ingest ...`
- `wikify refresh`
- `wikify wiki ...`
- `wikify papers ...`

Code-level public boundaries:

- `wikify.ingest.service`
- `wikify.ingest.refresh`
- `wikify.wiki.runtime`
- `wikify.papers.runtime`

`cli.py` and `mcp_server.py` should become thin composition layers over those
boundaries. They should not contain product logic.

Within `wikify.wiki.runtime`, workflows should be able to read from both the
visible wiki and the structured state through explicit contracts rather than
ad hoc module reach-through. The target model is:

- visible pages for synthesized answers, navigation, and human review
- structured state for retrieval, embeddings, graph lookups, provenance, and
  cross-run or cross-epoch comparison

Discovery strategy and coverage should also be treated as public runtime
configuration surfaces rather than buried constants. The runtime should be able
to choose or report:

- strategy id
- strategy parameters
- document-type routing decisions
- coverage policy
- multimodal enablement
- optional note-dump or second-pass consolidation modes
- DAG workflow id
- config provenance such as YAML path or embedded config hash

## Documentation Corrections Required
The current architecture and status docs still mix product architecture with
runtime-specific and domain-specific assumptions. The refactor must fix that.

### Architecture Doc
Update `docs/architecture.md` so that it:

- describes Wikify as a shared corpus platform with separate wiki and research
  writing products
- states that the wiki is corpus-agnostic and not science-specific
- treats CLI, MCP, `AGENTS.md`, and any framework-specific skill docs as
  adapters rather than the primary architecture
- removes statements that imply Claude-specific workflows are the preferred or
  canonical runtime
- distinguishes corpus ingestion from research-paper generation
- identifies graph metrics and epoch observability as first-class wiki
  subsystems
- explains the discovery subsystem as a first-class wiki layer with explicit
  strategy, coverage, and multimodal behavior
- explains how visible wiki files and structured state work together without
  becoming competing sources of truth

### Project Status Doc
Update `docs/project-status.md` so that it:

- reports capability status by product boundary: `ingest`, `wiki`, `papers`,
  `core`, adapters
- removes Claude-specific claims such as "skill-based primary"
- moves benchmark-specific corpus details into an example or benchmark section,
  rather than letting them define the product
- reports graph/topology metrics and epoch telemetry as dedicated capabilities
- uses generic wiki terminology rather than science-specific framing
- clearly labels legacy or secondary paths such as sitemap-first flows
- makes clear that agents and runtime workflows leverage both wiki files and
  structured state
- reports discovery strategy, coverage, and multimodal extraction as explicit
  capabilities rather than implied behavior inside epoch

### Runtime Instructions
Keep runtime-specific instructions in runtime-specific documents only.

- `AGENTS.md` is the framework-neutral collaboration contract.
- Optional tool-specific docs may exist, but they are adapter docs.
- Architecture and status docs must not depend on Claude Code or any other
  single runtime.

## Workstreams
The migration is organized into phases. Each slice lists a write scope, its
dependencies, and a verification target so parallel agents can work safely.

### Phase 0 - Guardrails And Ownership
Sequential. Blocks all other work.

#### S0.1 - Baseline
- Record the current test, type-check, and smoke-command baseline in
  `docs/refactor/baseline.md`.
- Baseline commands:
  - `uv run pytest -q`
  - `uv run ruff check src tests`
  - `uv run ty src/wikify`

#### S0.2 - Migration Rules
- Add a short migration note covering:
  - shim policy
  - import-path policy
  - common root files owned only by integration slices
  - per-slice verification rules

### Phase 1 - Product Boundary Extraction
Primary goal: separate `papers` from `wiki` and `ingest` without behavior
changes.

#### S1.A - Move Research Writing Modules
Write scope:

- `src/wikify/agent/**`
- `src/wikify/generate/**`
- `src/wikify/retrieve/**`
- `src/wikify/evaluate/**`
- paper-specific export files from `src/wikify/export/**`

Tasks:

- Move these into `src/wikify/papers/**`.
- Update imports inside the moved tree only.
- Leave compatibility shims at old import paths.

Needs:

- Phase 0

Verification:

- paper-generation tests still match baseline
- `wikify papers --help` resolves once adapter slice lands

#### S1.B - Re-home Corpus-Wide Support
Write scope:

- `src/wikify/zotero/**`
- `src/wikify/vault/**`
- `src/wikify/ingest/corpus_refresh.py`
- `src/wikify/ingest/service.py`

Tasks:

- Extract reusable BibTeX lifecycle behavior into `src/wikify/ingest/bibtex.py`.
- Decide final placement of `vault/` based on ownership:
  - if it is a corpus projection or refresh artifact, move it under
    `src/wikify/ingest/vault/`
  - if any part is paper-only, move only that part to `papers/`
- Keep `ingest` responsible for corpus-wide rebuilds such as BibTeX and vault.

Needs:

- Phase 0

Verification:

- single-file ingest still updates the DB and `data/library.bib`
- refresh still regenerates corpus-wide artifacts

#### S1.C - Split CLI And MCP Adapters
Write scope:

- `src/wikify/cli.py`
- `src/wikify/mcp_server.py`
- `src/wikify/papers/cli.py`
- `src/wikify/papers/mcp.py`

Tasks:

- make root `cli.py` a thin adapter that mounts `ingest`, `wiki`, and `papers`
- expose research-writing commands under `wikify papers ...`
- keep wiki management under `wikify wiki ...`
- separate paper MCP registration from wiki MCP registration

Needs:

- S1.A
- S1.B for any moved corpus-side adapters

Verification:

- `wikify --help`
- `wikify wiki --help`
- `wikify papers --help`
- MCP server still starts with separated tool registration

### Phase 2 - Shared Core And Ingest Cleanup
Primary goal: make shared and corpus-wide code easier to navigate without
changing product boundaries again.

#### S2.A - Build `core/`
Write scope:

- `src/wikify/config.py`
- `src/wikify/llm/**`
- `src/wikify/store/**`
- `src/wikify/core/**`

Tasks:

- move shared infra into `src/wikify/core/**`
- add thin re-export shims from the old paths
- hoist local imports when they are only masking avoidable layering issues

Needs:

- Phase 1

Verification:

- baseline tests pass
- no new type errors

#### S2.B - Rationalize Parsers
Write scope:

- `src/wikify/ingest/pdf.py`
- `src/wikify/ingest/docx.py`
- `src/wikify/ingest/pptx.py`
- `src/wikify/ingest/html.py`
- `src/wikify/ingest/markdown.py`
- `src/wikify/ingest/parsers/**`

Tasks:

- move file-specific parsers into `ingest/parsers/`
- define parser protocol and registry at the parser boundary
- replace extension dispatch chains with enum/registry dispatch where it
  improves clarity

Needs:

- Phase 1

Verification:

- ingest smoke tests for each supported file type

#### S2.C - Split Refresh Pipeline
Write scope:

- `src/wikify/ingest/corpus_refresh.py`
- `src/wikify/ingest/refresh.py`
- `src/wikify/ingest/bibtex.py`
- `src/wikify/ingest/vault/**`

Tasks:

- split refresh into named phase functions with a small context object
- keep `corpus_refresh.py` as a temporary shim
- make BibTeX rebuild and other corpus projections explicit refresh phases

Needs:

- Phase 1

Verification:

- `wikify refresh` still runs end-to-end
- refresh-related tests pass

### Phase 3 - Wiki Package Consolidation
Primary goal: make `wiki/` easy to navigate and extend without changing its
public runtime contract.

#### S3.0 - Introduce Wiki Package Skeleton
Sequential pre-step for the rest of Phase 3.

Write scope:

- `src/wikify/wiki/contracts.py`
- `src/wikify/wiki/runtime.py`
- new package directories under `src/wikify/wiki/`

Tasks:

- confirm `WikiUpdateBundle` and related contracts are the central mutation
  surface
- make the visible-plus-structured mutation contract explicit so page patches,
  provenance, graph/state updates, and telemetry remain aligned
- create package skeletons for `operations`, `concepts`, `graph`, `articles`,
  `observability`, `presentation`, and `discovery`
- add temporary re-export shims for large modules that will be split next

Needs:

- Phase 2

Verification:

- imports remain stable

#### S3.A - Extract Discovery Pipeline
Write scope:

- `src/wikify/wiki/concepts.py`
- `src/wikify/wiki/template.py`
- `src/wikify/wiki/discovery/**`
- `src/wikify/wiki/concepts/**`

Tasks:

- split discovery planning away from canonical concept persistence
- introduce explicit discovery contracts for document profile, extraction unit,
  extraction note, candidate concept, and coverage record
- introduce DAG contracts for node spec, artifact spec, and workflow config
- move strategy selection, document-type routing, extraction-unit planning,
  note emission, and finite-coverage scheduling into `wiki/discovery/`
- move step execution into explicit discovery nodes with declared input and
  output artifacts
- add YAML workflow loading and validation into typed config objects
- evaluate whether a Hydra-like config library adds enough value for experiment
  composition and sweeps, or whether a smaller in-house layer is clearer
- keep canonical merge and evidence persistence in `wiki/concepts/`
- make synopsis budgeting, chunk budgeting, modality usage, and model tier
  strategy-configured rather than fixed constants
- make it possible to plug in alternative strategies such as:
  - synopsis-first targeted deepening
  - all-chunk sweep with note dumping
  - multimodal-first extraction for image-heavy documents
- replace publication-only naming with document-aware terminology where the
  code is part of wiki core behavior

Needs:

- S3.0

Verification:

- concept extraction tests pass
- focused tests cover strategy selection and eventual-coverage scheduling
- a smoke path can emit inspectable extraction notes for one strategy
- DAG validation tests cover bad dependency graphs and bad artifact bindings
- at least one YAML-defined workflow executes end-to-end in smoke coverage
- config provenance for one experimental run can be captured and compared

#### S3.B - Split Graph Logic
Write scope:

- `src/wikify/wiki/concept_graph.py`
- `src/wikify/wiki/domains.py`
- `src/wikify/wiki/routing.py`
- `src/wikify/wiki/graph/**`

Tasks:

- separate graph construction, importance scoring, topology metrics, domains,
  and routing
- replace long route-kind branching with explicit handlers where useful
- define stable graph metric entry points for epoch ordering, convergence,
  maintenance, and dashboard consumers

Needs:

- S3.0

Verification:

- graph/routing tests and wiki epoch smoke tests pass
- graph metric outputs are covered by focused public-function tests

#### S3.C - Split Article And Index Logic
Write scope:

- `src/wikify/wiki/builder.py`
- `src/wikify/wiki/article.py`
- `src/wikify/wiki/linker.py`
- `src/wikify/wiki/mapreduce.py`
- `src/wikify/wiki/articles/**`

Tasks:

- separate page I/O, writing, linking, and index generation
- keep wiki writing local to `wiki/articles/`
- preserve visible-page behavior and frontmatter contracts

Needs:

- S3.0

Verification:

- article-writing, linking, and index smoke tests pass

#### S3.D - Split Runtime Operations
Write scope:

- `src/wikify/wiki/epoch.py`
- `src/wikify/wiki/maintenance.py`
- `src/wikify/wiki/runtime.py`
- `src/wikify/wiki/operations/**`

Tasks:

- split epoch, maintain, query/campaign helpers, reconciliation, and metrics
  into operation modules
- keep `wiki/runtime.py` as the small shared facade
- ensure runtime modules call graph and observability surfaces rather than
  embedding metric logic inline
- ensure runtime operations can intentionally use both visible pages and
  structured state instead of tunneling through whichever one is easiest at the
  call site
- ensure runtime operations report discovery strategy, coverage, and note or
  multimodal usage through observability surfaces
- ensure runtime operations can report DAG workflow id, node-level timings, and
  config provenance

Needs:

- S3.0

Verification:

- `wikify wiki epoch --status`
- `wikify wiki query`
- `wikify wiki maintain`

#### S3.E - Extract Observability
Write scope:

- `src/wikify/wiki/telemetry.py`
- `src/wikify/wiki/observability/**`
- direct telemetry call sites in `src/wikify/wiki/epoch.py`
- direct telemetry call sites in `src/wikify/wiki/runtime.py`

Tasks:

- split run lifecycle, stage timing, wiki snapshots, log writing, and run
  export into local observability modules
- define a small explicit surface for:
  - begin or update or finish run
  - stage timing
  - tool, retrieval, token, and page-delta recording
  - wiki snapshot capture
  - human-readable epoch log append
  - machine-readable run export
- keep human-readable logs and machine-readable exports aligned so a run can be
  inspected from either path

Needs:

- S3.0

Verification:

- run telemetry tests pass
- `wikify wiki epoch --status` still reflects recorded runs cleanly
- exported run summaries and snapshot files are still emitted

#### S3.F - Localize Presentation
Write scope:

- `src/wikify/wiki/html.py`
- `src/wikify/wiki/dashboard.py`
- `src/wikify/wiki/layout.py`
- `src/wikify/wiki/templates/**`
- `src/wikify/wiki/presentation/**`

Tasks:

- move HTML, dashboard, layout, and templates under `wiki/presentation/`
- keep wiki presentation code near wiki page contracts instead of moving it to a
  generic `frontend/` package

Needs:

- S3.0

Verification:

- `wikify wiki html`
- `wikify wiki dashboard`

#### S3.G - Isolate Legacy Sitemap Path
Write scope:

- `src/wikify/wiki/sitemap.py`
- `src/wikify/wiki/agent.py`
- `src/wikify/wiki/legacy/**`

Tasks:

- if the sitemap-first flow remains, move it under `wiki/legacy/` or another
  clearly secondary namespace
- keep the epoch/query/maintain runtime as the primary wiki path

Needs:

- S3.0

Verification:

- legacy wiki commands still work if retained

### Phase 4 - Documentation Neutralization
Primary goal: make architecture and status docs reflect the real product
boundaries and stay framework-neutral.

#### S4.A - Update Architecture
Write scope:

- `docs/architecture.md`

Tasks:

- rewrite the top-level module map and diagrams around `core`, `ingest`,
  `wiki`, `papers`
- remove Claude-specific architecture claims
- make the wiki's corpus-agnostic design explicit

Needs:

- Phases 1 to 3

Verification:

- module map matches code layout

#### S4.B - Update Project Status
Write scope:

- `docs/project-status.md`

Tasks:

- report status by product boundary
- remove tool-specific or framework-specific "primary runtime" language
- move benchmark corpus details to clearly labeled examples

Needs:

- Phases 1 to 3

Verification:

- status sections align with the new package boundaries

#### S4.C - Runtime Adapter Docs
Write scope:

- `AGENTS.md`
- any existing runtime-adapter docs that remain in the repo

Tasks:

- keep collaboration/runtime guidance in adapter docs
- ensure product docs do not depend on a specific runtime

Needs:

- Phases 1 to 3

Verification:

- architecture and status docs can be read without any runtime-specific context

### Phase 5 - Tests, Shims, And Cleanup
Primary goal: finalize the migration and remove compatibility scaffolding.

#### S5.A - Test Layout Alignment
Write scope:

- `tests/**`

Tasks:

- mirror `core`, `ingest`, `wiki`, and `papers` boundaries in the tests
- keep contract and smoke tests at public boundaries
- remove redundant tests that only pin internal implementation details

Needs:

- Phase 4

Verification:

- baseline test confidence is preserved

#### S5.B - Shim Removal
Write scope:

- all temporary shim modules added in earlier phases

Tasks:

- grep for old import paths
- remove shims only after zero non-shim callers remain

Needs:

- S5.A

Verification:

- no remaining imports against retired paths

#### S5.C - Focused Cleanup
Write scope:

- touched files only

Tasks:

- remove unused imports and dead code introduced during the migration
- fix layering issues exposed by the moves
- do not perform unrelated repo-wide cleanup in this phase

Needs:

- S5.B

Verification:

- `ruff`, tests, and type checks match or improve on baseline

## Parallel Agent Execution Plan
This plan is safe for parallel agents if ownership is enforced.

Rules:

- Only one slice may edit a common root file such as `src/wikify/cli.py`,
  `src/wikify/mcp_server.py`, `docs/architecture.md`, or
  `docs/project-status.md`.
- Slices may add shims, but only the final shim-removal slice may delete them.
- Agents should not perform repo-wide formatting outside their owned write set.
- Agents should not move modules across product boundaries unless that move is
  part of their slice.

Recommended concurrent groups:

1. Phase 1:
   - S1.A
   - S1.B
   - S1.C after S1.A and S1.B
2. Phase 2:
   - S2.A
   - S2.B
   - S2.C
3. Phase 3:
- S3.A
- S3.B
- S3.C
- S3.D
- S3.E
   - S3.F
   - S3.G
4. Phase 4:
   - S4.A
   - S4.B
   - S4.C

## Verification Matrix
After each phase:

1. `uv run ruff check src tests`
2. `uv run ty src/wikify`
3. `uv run pytest -q`

Required smoke commands by the end of the migration:

- `wikify ingest <path>`
- `wikify refresh`
- `wikify wiki epoch --status`
- `wikify wiki query`
- `wikify wiki maintain`
- `wikify wiki html`
- `wikify papers --help`

## Acceptance Criteria
The refactor is complete when all of the following are true.

1. `wiki`, `papers`, `ingest`, and `core` are visibly separate in code,
   runtime adapters, and docs.
2. `wiki` contains only wiki creation/management concerns and does not import
   from `papers`.
3. Research writing lives under `papers/` and is reached through `wikify papers
   ...`.
4. Corpus-wide enrichment such as BibTeX rebuilds is owned by `ingest`.
5. Architecture and status docs are framework-neutral and describe the wiki as
   general-purpose rather than science-specific.
6. Large monolithic wiki modules are split into local subpackages with clearer
   responsibilities.
7. Graph metrics and epoch observability have explicit module ownership and
   stable surfaces for downstream consumers.
8. Visible wiki files and structured state remain aligned through explicit
   contracts, and agent/runtime flows can leverage both.
9. Discovery is organized as an explicit configurable subsystem with
   inspectable intermediate artifacts, document-type-aware strategies, and
   finite-coverage scheduling across epochs.
10. Multimodal discovery is a supported part of wiki core behavior rather than
    an afterthought outside concept extraction.
11. Wikification workflows can be expressed as validated DAG configs rather
    than being hard-coded into one orchestration path.
12. Compatibility shims are gone and tests/smokes still pass.
