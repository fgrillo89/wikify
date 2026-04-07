# Wiki Runtime Refactor Plan

## Summary
Wikify is being refactored around two tightly aligned layers:

- the **visible wiki layer**: human-facing markdown pages that render into a Wikipedia-like HTML site
- the **operational layer**: the minimum structured state needed for retrieval, graph reasoning, provenance, maintenance, and telemetry

The visible layer stays primary. The operational layer exists to help the agent build and query the visible wiki faster and with higher quality, while tracking how the wiki evolves over time.

This is a focused design note for the wiki runtime only. The active repo-wide
execution plan now lives in `docs/refactor/wiki-deep-refactor-plan.md`.

## Visible Layer
Canonical visible layout:

- `data/wiki/index.md`
- `data/wiki/log.md`
- `data/wiki/articles/`
- `data/wiki/sources/`
- `data/wiki/_meta/`

Visible pages use one shared frontmatter contract:

- `title`
- `slug`
- `page_type`
- `domains`
- `source_ids`
- `updated_at`
- `status`
- `confidence`
- optional `aliases`, `related`, `open_questions`

Visible page roles:

- `entity`
- `concept`
- `overview`
- `comparison`
- `query`
- `source-note`

## Operational Layer
Operational state remains close to the visible wiki and never stores competing article truth.

Structured state includes:

- `ConceptOccurrence`
- `RelationEvidence`
- `PageProvenance`
- `DomainMembership`
- `GraphEdge`
- `MaintenanceFinding`
- `RunLog`
- `DocumentProfile`
- `ExtractionUnit`
- `ExtractionNote`
- `CoverageRecord`

Telemetry includes:

- `RunTelemetry`
- `StageTelemetry`
- `ToolCallTelemetry`
- `RetrievalTelemetry`
- `TokenUsageTelemetry`
- `PageDeltaTelemetry`
- `WikiSnapshotMetric`
- `ExperimentTag`
- `LossDefinitionResult`

Machine-readable exports live under:

- `data/wiki/_meta/runs/`
- `data/wiki/_meta/metrics/`

Agents and runtime workflows should be able to leverage both layers:

- visible pages for synthesized knowledge and navigation
- structured state for retrieval, provenance, graph reasoning, ranking, and maintenance

## Discovery Runtime
Discovery is a first-class wiki runtime concern.

The runtime should treat discovery as a configurable sequence of explicit steps:

- profile the source document and modalities
- choose a discovery strategy
- plan extraction units
- run one or more extraction passes
- persist extraction notes and coverage
- resolve candidate concepts into canonical concept state

The preferred execution model is a configurable DAG:

- each node performs one well-defined step
- nodes declare typed inputs and outputs
- dependencies are explicit
- node implementations are reusable across multiple workflows
- workflow definitions can be stored as YAML and validated before execution

This should feel closer to ML experiment orchestration than to ad hoc command
flags. Useful inspiration includes Hydra-like config composition, but the
runtime should stay objective about whether that dependency is worth carrying.

This is important because different corpora may need different strategies:

- synopsis-first deepening for structured publications
- all-unit sweeps for weakly structured notes or captures
- multimodal-first passes for slide decks and image-heavy documents

Discovery should expose strategy ids and parameters so epoch runs can be
compared meaningfully.

It should also expose DAG workflow ids, node-level timings, and config
provenance so experimental runs are reproducible.

## Mutation Contract
All wiki workflows move toward one shared mutation envelope:

- `WikiUpdateBundle`

Each bundle may include:

- page creates or patches
- link updates
- source-note updates
- provenance updates
- concept occurrences
- relation evidence
- domain updates
- maintenance findings
- log entries
- telemetry events
- discovery notes
- coverage updates

Run states:

- `pending`
- `applied`
- `reconcile_needed`
- `reconciled`
- `failed`

## Runtime Surface
Primary runtime remains adapter-neutral.

Shared operations:

- `ingest`
- `epoch`
- `query`
- `maintain`
- `campaign`
- `rebuild_index`
- `reconcile_state`
- `export_metrics`
- `compare_runs`

Runtime adapters:

- `AGENTS.md`
- CLI
- MCP
- optional runtime-specific guidance files

CLI remains a thin wrapper over the same operations.
MCP remains optional and secondary.

## Telemetry Foundation
Each run should capture:

- workflow type
- discovery strategy id
- discovery strategy version
- DAG workflow id
- config hash or YAML provenance
- workflow configuration id
- loss definition id
- prompt family
- model tier and actual model
- tokens by type where available
- stage timings
- node timings
- tool calls
- pages read
- pages written
- chunks read
- chunks selected
- extraction units planned
- extraction units processed
- extraction notes written
- sources consulted
- raw fallback usage
- cost estimate
- page and graph deltas

Wiki snapshots should track:

- article count by `page_type`
- source-note count
- link count
- orphan count
- bridge count
- domain modularity
- cross-domain edge ratio
- evidence density
- weak-support count
- contradiction count
- unresolved-gap count
- tokens per useful article update
- cost per orphan eliminated
- cost per net new edge

## Incremental Delivery
The repo is being updated in slices:

1. add the active runtime plan and framework-neutral runtime docs
2. add visible/operational contracts and telemetry schemas
3. move visible page writes toward the simplified `articles/` layout
4. wire epoch runs to emit run summaries, metrics, and human-readable log entries
5. replace article-coverage-derived graph behavior with discovery-derived operational state

The current implementation may contain compatibility shims while these slices land, but the target architecture is the one described above.
