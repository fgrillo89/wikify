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

## Architecture Style
Prefer locality of behavior. Future agents should be able to understand what a
function, class, or module does without chasing several near-empty abstraction
layers.

Rules:

- Code that changes together should live together.
- If variants differ only by constants or constructor arguments, prefer one
  local registry/data table over several one-line modules or subclasses.
- Do not create parallel concepts such as "preset", "strategy", "config", and
  "factory config" unless each has a distinct responsibility.
- `__init__.py` files should be boring public re-export surfaces, not places for
  factories, config tables, side effects, or business logic.
- Classify new knobs before adding them:
  - Domain/strategy knobs belong with the business object they change.
  - Runtime knobs belong on the pipeline/service function that executes work.
  - Policy knobs belong in policy runtime or policy action schemas.
  - Adapter knobs belong in CLI/MCP/runtime-specific wiring and are passed
    inward explicitly.
- Do not mutate domain config objects after construction just to carry runtime
  or adapter choices. Pass those choices explicitly.
- Use shared enums for small closed vocabularies at contract boundaries. Avoid
  ad hoc strings and helper functions that duplicate enum values.
- Keep provider-specific model ids and runtime names at adapter boundaries.
  Core business logic should use domain terms such as role, tier, strategy id,
  or policy id.
- A factory should instantiate from a single source of defaults. Prefer
  `Thing(**DEFAULTS[key], seed=seed)` over building an object and cloning it just
  to override one field.
- When simplifying structure, delete superseded modules, aliases, helper layers,
  and docs in the same change. Do not leave fallback files as second sources of
  truth.

## Distill Strategy Rules
For `wikify/distill`, apply the architecture style above as follows:

- E/M/X strategy definitions live together in
  `src/wikify/distill/strategies/registry.py`.
- `strategies/__init__.py` should only re-export the public API.
- Do not split one-line strategy differences across separate modules such as
  `explore.py`, `mixed.py`, and `exploit.py`.
- Do not create both "preset" and "config" layers unless they have distinct
  responsibilities. Prefer one config object plus one factory.
- `StrategyConfig` should contain only strategy-science knobs: sampler,
  schedule, model tiers, allocation override, and seed.
- Runtime choices such as field guide, artifact template, policy name, binding,
  model id, prompt names, cache paths, and CLI flags should be explicit
  pipeline or adapter parameters, not strategy fields.
- Use shared enums for small closed vocabularies. Model tiers are `ModelTier`
  (`S`, `M`, `L`) from `contracts`, not ad hoc strings or parallel model-id
  helpers. Use `tier.value` when a string is needed for JSON, cache keys, or
  provenance.

Preferred factory shape:

```python
STRATEGY_CONFIGS = {
    "M": dict(
        name="M",
        sampler=LevyMixSampler(...),
        schedule=AdaptiveSchedule(...),
        extract_tier=ModelTier.SMALL,
        write_tier=ModelTier.MEDIUM,
    ),
}

def build_strategy(strategy_id: StrategyId | str, *, seed: int = 0) -> StrategyConfig:
    key = strategy_id.value if isinstance(strategy_id, StrategyId) else strategy_id
    return StrategyConfig(**STRATEGY_CONFIGS[key], seed=seed)
```

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
