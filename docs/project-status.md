# Wikify Project Status

## wikify_simple -- standalone wikification pipeline

`wikify_simple` (`src/wikify_simple/`) is a standalone wikification pipeline,
separate from legacy `wikify`. It is the successor design: simpler, file-based
(no SQLite, no ChromaDB), with a dispatcher-based model binding that keeps
Python out of the LLM loop.

### Current state (2026-04-09)

- **Corpus processed:** 20-paper mvp20 corpus (memristor / ALD / neuromorphic).
  689 chunks, 312 images, 384-d sentence-transformer embeddings.
- **Wiki output:** 215 concept pages + 21 person pages (real binding), 1268
  deterministic author pages, full HTML rendering via mkdocs-material theme.
- **Test count:** 190 tests passing.
- **Key metrics (mvp20, 3x real-binding run):**

| metric | value |
|---|---|
| M1 coverage_residual | 0.4603 |
| M3 g_links Q | 0.7377 |
| M3 g_links n_edges | 2601 |
| M3 g_evidence Q | 0.0 (only 2 written pages) |
| M6 g1_anchoring | 0.125 (14/112 sentences) |
| figure refs in writes | 2/2 (100%) |

- **Design decisions:** files-on-disk storage, dispatcher-based binding
  (fake for CI, claude_code for real runs), layered prompts (style guide +
  field guide + artifact template + persona), deterministic author pages,
  natural Wikipedia-style page names, tolerant quote validation.

### What's next

1. Fix M6 anchoring (writer not citing enough -- 12.5% of sentences).
2. Implement `--feed` iteration semantics (create/refine/merge contract).
3. Filter skeleton pages from rendered output.
4. Unicode normalization for remaining extract rejections.
5. Image consolidation in refine mode.
6. Port remaining parsers (DOCX, PPTX, HTML).

See `src/wikify_simple/SESSION_LOG.md` for the comprehensive handoff.

---

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
| `core` | **Landed** | `src/wikify/core/` owns config, llm, store, graph, retrieve, corpus_tools. |
| `ingest` | **Landed** | `src/wikify/ingest/` owns parsers, extract, vault, zotero, refresh. |
| `wiki` | **Landed** | `src/wikify/wiki/` is decomposed into concepts, discovery, graph, observability, presentation, articles, recipes, legacy. |
| `papers` | **Landed** | `src/wikify/papers/` owns agent, generate, evaluate, export, prompts. |
| adapters | Working, still thick | `cli.py` and `mcp_server.py` exist but still contain product logic; thinning is the next slice. |
| docs | **Updated** | Architecture, status, and the active refactor activity log all reflect the current code layout. |

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

## Recently Landed (2026-04-07 refactor sweep)

The four-boundary architecture now exists in code. The slices that
landed in this sweep:

- **S3.A** — discovery scaffold, agent-native concepts decomposition,
  recipe layer, vendor naming purge.
- **S3.B** — `wiki/graph/` subpackage (concept_graph, domains, routing).
- **S3.E** — `wiki/observability/` subpackage (telemetry).
- **S3.F** — `wiki/presentation/` subpackage (HTML, dashboard, layout, templates).
- **S3.G** — `wiki/legacy/` namespace for the sitemap-first flow.
- **Phase 1.A** — papers boundary: `agent`, `generate`, `retrieve`,
  `evaluate`, `export`, `prompts` moved under `wikify.papers.*`.
- **Phase 1.A.2 / 2.A** — `core/retrieve` extracted from papers,
  `core/corpus_tools.py` introduced, wiki callers rewired to core.
  The `wiki must not import papers` rule now holds for all
  non-legacy wiki modules.
- **Phase 2.A** — `config`, `llm`, `store`, `graph` moved into `core/`.
- **Phase 1.B / 2.B** — `extract`, `vault`, `zotero` moved into `ingest/`.
- **S5.A** — `tests/` mirrored to `test_core / test_ingest / test_wiki / test_papers`.

Final top-level layout matches the architecture target exactly.
**852 tests pass.** Each slice was committed and pushed individually
with a clear scope; the full chronology is in
`docs/refactor/activity-log.md`.

## Older "Recently Landed" notes
- **S3.A scaffold**: `wikify.wiki.discovery` subsystem now exists with typed
  contracts (`DocumentProfile`, `ExtractionUnit`, `ExtractionNote`,
  `CandidateConcept`, `CoverageRecord`, `DagNodeSpec`, `DagRunSpec`),
  validated DAG executor, node registry with built-in profile/plan/extract
  /resolve/persist nodes, multimodal unit builders, eventual-coverage
  scheduler, document-type-aware planner, strategy registry, and a
  YAML workflow loader. One bundled workflow
  (`discovery/workflows/default_publication.yaml`) executes end-to-end in
  tests with multimodal usage and config-hash provenance reported. The
  legacy `wikify.wiki.concepts` module is unchanged; integration is the
  next slice.

## Next Priorities
1. Complete the package-boundary refactor in
   `docs/refactor/wiki-deep-refactor-plan.md`.
2. Wire `wiki.epoch` to route concept extraction through the new discovery
   DAG executor and decompose the legacy `wikify.wiki.concepts` module into
   a `wiki/concepts/` subpackage (records / merge / evidence / persistence)
   without breaking import paths. This requires renaming the legacy file
   first to free the `concepts` namespace.
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
