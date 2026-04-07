# Wikify Project Status

## Summary
Wikify is being consolidated around a small current docs surface and a clearer
runtime architecture.

Primary docs:

- `docs/architecture.md`
- `docs/project-status.md`
- `docs/refactor/wiki-deep-refactor-plan.md`

Retained focused design note:

- `docs/design/wiki-runtime-refactor-plan.md`

Older design material has been moved to archive.

## Product Direction
Wikify has two product surfaces built on the same corpus substrate:

- `wiki`: the primary product, for building and maintaining a curated
  knowledge base from source corpora
- `papers`: a secondary product, for generating papers, reviews, and
  presentations

The wiki must remain general-purpose and not be defined around scientific papers
alone, even though scientific corpora remain an important use case.

## Current Focus
The active engineering focus is the refactor in
`docs/refactor/wiki-deep-refactor-plan.md`.

The refactor is driving the repo toward four stable boundaries:

- `core`
- `ingest`
- `wiki`
- `papers`

Key architectural themes in the current work:

- separate wiki management from research writing
- keep visible wiki files and structured state aligned
- make discovery and extraction a first-class, configurable wiki subsystem
- move wikification toward a configurable DAG over explicit typed artifacts
- make graph metrics and epoch observability first-class wiki subsystems
- keep adapters thin and framework-neutral
- reduce the current doc set to a small number of current sources of truth

## Boundary Status

| Boundary | Status | Notes |
|----------|--------|-------|
| `core` | In progress | Shared infrastructure exists today but still needs cleaner ownership under a dedicated `core/` boundary. |
| `ingest` | Working, needs consolidation | Parsing, chunking, embeddings, BibTeX, and refresh exist; refresh and parser organization still need cleanup. |
| `wiki` | Working, active refactor target | Epoch/query/maintain/campaign and visible wiki outputs exist, but the package needs clearer internal boundaries and smaller modules. |
| `papers` | Working, boundary extraction pending | Writing and export capabilities exist, but they still bleed into the root package layout and adapters. |
| adapters | Working, needs thinning | CLI and MCP work today, but still carry too much product logic and historical coupling. |
| docs | In progress | Architecture and status are being simplified; stale design docs are being archived. |

## What Works Today

### Ingest
- Source ingestion for PDFs, DOCX, PPTX, Markdown, and HTML exists.
- Structured corpus state exists in SQLite, embeddings, and precompute/cache
  artifacts.
- Corpus-wide refresh workflows exist, including BibTeX generation.

### Wiki
- Visible wiki generation exists under `data/wiki/`.
- Shared wiki runtime operations exist for epoch, query, maintain, campaign,
  reconcile-state, and metrics export flows.
- Structured wiki support exists for provenance, graph edges, maintenance
  findings, and run telemetry.
- Wiki HTML and dashboard surfaces exist.

### Papers
- Research writing, evaluation, revision, and export capabilities exist.
- The papers surface can already leverage the shared corpus substrate.
- The package and adapter separation from `wiki` is not complete yet.

## What Is Actively Changing

### Package Boundaries
The codebase is being reshaped so that:

- shared infrastructure moves under `core`
- corpus-wide source processing stays under `ingest`
- wiki creation and management stay under `wiki`
- research writing moves under `papers`

### Wiki Internals
The wiki package is being reorganized around:

- `contracts`
- `discovery`
- `operations`
- `concepts`
- `graph`
- `articles`
- `observability`
- `presentation`

### Visible And Structured Coherence
The intended contract is now explicit:

- visible markdown pages are the main human-facing artifacts
- structured state such as DB records, embeddings, graph edges, provenance, and
  telemetry should evolve alongside them
- reconciliation should usually rebuild structured state rather than overwrite
  curated pages
- agents and runtime flows should be able to leverage both visible pages and
  structured state

### Discovery And Coverage
Discovery is now treated as one of the main refactor targets rather than a
detail hidden inside epoch orchestration.

The intended direction is:

- explicit document profiling and strategy selection
- DAG-based workflow composition instead of one fixed hard-coded sequence
- document-type-aware discovery rather than publication-only heuristics
- inspectable extraction notes and coverage records
- multimodal extraction support for images, figures, tables, and slides
- eventual processing of all eligible extraction units across enough epochs

## Main Risks And Gaps
- Some wiki modules remain too large and mix responsibilities.
- Product boundaries between `wiki`, `papers`, and root adapters are still
  blurrier than they should be.
- Some historical docs still described tool-specific or framework-specific
  workflows as canonical architecture.
- Older design ideas around discovery, domain membranes, and adaptive knowledge
  remain useful context, but they are no longer the current implementation
  surface.

## Next Priorities
1. Complete the package-boundary refactor in
   `docs/refactor/wiki-deep-refactor-plan.md`.
2. Extract discovery into explicit strategy, coverage, and note-emission
   modules.
3. Add validated DAG workflow configuration, likely YAML-backed, for
   experimental wikification strategies.
4. Separate graph logic and observability into explicit wiki-owned modules.
5. Ensure runtime operations intentionally leverage both visible wiki files and
   structured state.
6. Keep architecture and status docs aligned with the refactor plan as slices
   land.
7. Archive or update remaining stale design material instead of letting it look
   current.

## Archived Documentation
Docs that were superseded, too implementation-specific, or no longer aligned to
current architecture have been moved to archive. The intent is to keep the
current docs surface small and high-signal while preserving historical context.

## Resume Guidance
When resuming work:

1. Read `AGENTS.md` for repo-specific collaboration guidance.
2. Read `docs/project-status.md` for current status and priorities.
3. Read `docs/architecture.md` for boundaries and system model.
4. Read `docs/refactor/wiki-deep-refactor-plan.md` for the active execution
   plan.
5. Read `docs/design/wiki-runtime-refactor-plan.md` only when you need the
   focused visible-versus-operational wiki design details.
