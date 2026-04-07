# Wikify Agent Contract

This repo should work well across agentic runtimes such as Codex and Claude
Code. Product architecture must stay runtime-neutral.

## Current Docs
Read these first:

- `docs/project-status.md`
- `docs/architecture.md`
- `docs/refactor/wiki-deep-refactor-plan.md`

Use `docs/design/wiki-runtime-refactor-plan.md` only for the focused
visible-wiki plus operational-state design.

## Product Model
- Raw sources are authoritative evidence.
- Wiki markdown pages are authoritative human-facing knowledge artifacts.
- Structured state supports retrieval, embeddings, graph reasoning,
  provenance, maintenance, and telemetry.
- Visible pages and structured state should evolve together and must not become
  competing truths.
- Telemetry is first-class because runs, strategies, prompts, and loss
  definitions are expected to be compared over time.

## Boundaries
- `core`: shared infrastructure
- `ingest`: source parsing and corpus-wide enrichment
- `wiki`: wiki creation, management, graph reasoning, observability, and presentation
- `papers`: research-writing workflows and export

Rules:

- `wiki` must not depend on `papers`.
- `ingest` must not depend on `wiki` or `papers`.
- Adapters such as CLI, MCP, and runtime-specific guidance should stay thin.

## Visible Wiki Layout
- `data/wiki/index.md`
- `data/wiki/log.md`
- `data/wiki/articles/`
- `data/wiki/sources/`
- `data/wiki/_meta/`

Visible page roles live in frontmatter `page_type`, not in a deep folder tree:
- `entity`
- `concept`
- `overview`
- `comparison`
- `query`
- `source-note`

## Preferred Operations
When possible, use workflows rather than ad hoc file mutation:

- `wikify wiki epoch`
- `wikify wiki query`
- `wikify wiki maintain`
- `wikify wiki campaign`
- `wikify wiki html`
- `wikify wiki reconcile-state`

## Editing Rules
- Keep visible wiki files and structured state aligned.
- Prefer updating existing visible pages over creating duplicate pages.
- Preserve curated markdown content if structured state drifts; rebuild or
  reconcile the structured state instead of overwriting pages.
- Treat `data/wiki/_meta/` as operational artifacts, not human-facing article
  content.
- Graph metrics and run observability are first-class wiki concerns, not
  incidental implementation details.
- Agents and runtimes should be able to leverage both visible pages and
  structured state.

## Current Direction
The active execution plan is in `docs/refactor/wiki-deep-refactor-plan.md`.
