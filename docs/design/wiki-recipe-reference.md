# Wiki Recipe Reference

This page maps `src/wikify/wiki/recipes/*.yaml` fields to the code that implements them.

## Where Things Are Defined

- Recipe schema + validation:
  - `src/wikify/wiki/discovery/recipe.py`
  - `KNOWN_STEP_KINDS`, `KNOWN_MODEL_TIERS`, `KNOWN_FRONTIER_PRIORITIES`
- Step-kind to DAG lowering:
  - `src/wikify/wiki/discovery/recipe_compiler.py`
  - `_LOWER` registry (`kind` -> lowering function)
- DAG node implementations:
  - `src/wikify/wiki/discovery/nodes.py`
  - `register_builtin_nodes(...)`
- Document-type strategy registry:
  - `src/wikify/wiki/discovery/strategies.py`
  - `default_strategies()`
- Strategy selection:
  - `src/wikify/wiki/discovery/planner.py`
- Epoch entrypoint that compiles/runs the recipe:
  - `src/wikify/wiki/epoch.py`

## Recipe Fields

- `recipe_id`
  - Stable run strategy id, stored in run telemetry.
- `frontier.strategy`
  - Scheduler mode label (currently `eventual_coverage` in the default recipe).
- `frontier.budget_per_epoch`
  - Passed into planning as chunk budget.
- `frontier.exploration_rate`
  - Declared exploration knob for scheduler policies.
- `frontier.priority`
  - Priority policy label. Valid labels:
  - `section_tier`, `recency`, `weight`, `hub_spoke`
  - This is a recipe-level contract and should be interpreted by planning/scheduler policy.
  - Today, enforcement is partial: labels are validated and propagated, but not every policy has distinct runtime behavior yet.
- `frontier.filters`
  - Policy filters (for example section exclusions and minimum chunk size).

## Step Fields

- `kind` (required)
  - Conceptual operation kind.
  - Must be in `KNOWN_STEP_KINDS`.
- `name` (optional)
  - Step instance id used for DAG node ids, telemetry labels, and `inputs_from`.
  - Defaults to `kind`.
  - Use explicit `name` when you need multiple steps with the same `kind`.
- `model`, `prompt`, `schema`, `style_guide`, `units`, `multimodal`, `params`
  - Passed through to lowered DAG nodes.
- `inputs_from`
  - References step names (or kinds when `name` is omitted).

## Default Recipe Walkthrough (`default_publication.yaml`)

1. `profile_documents`
   - Lowers to node impl `profile_document` (builds `DocumentProfile`).
2. `identify_concepts`
   - Lowers to `plan_units` + `extract_text`.
3. `identify_people`
   - Lowers to `plan_units` + `extract_text` (people prompt/schema).
4. `identify_figures_tables`
   - Lowers to `plan_units` + `extract_multimodal`.
5. `consolidate`
   - Lowers to `resolve_candidates` over gathered notes.
6. `persist_canonical`
   - Lowers to `persist_notes` (coverage artifact).
7. `cross_link`, `write_articles`, `maintain`
   - Currently deferred at compile time (tracked in telemetry as deferred steps).
