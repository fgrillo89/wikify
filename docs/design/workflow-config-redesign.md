# Workflow Config Redesign — Design Note

**Status:** open design problem — not yet implemented.
**Owner:** next discovery-config slice.
**Raised:** 2026-04-07.

## Problem

The first cut of `wiki/discovery/workflows/default_publication.yaml` is
faithful to the DAG runtime (`DagNodeSpec` / `ArtifactRef`) but it is **not
user friendly**. A reader has to understand:

- what an "artifact key" is and that producer/consumer keys must match
- what each `impl` string maps to in Python
- how `inputs` and `outputs` slots bind to node parameters
- which knobs are buried in `params` versus declared in code

That is the right shape for the executor, but the wrong shape for the
human (or agent) configuring an experiment. The DAG layer should remain
the **execution substrate**; users should configure wikification at a
much higher level — closer to "what concepts get identified, by what
model, with what prompt and template, on what frontier policy".

## What "user-friendly" should mean here

A workflow config file should answer these questions plainly:

| Question | Today | Should be |
|---|---|---|
| What is this run trying to discover? | implicit in node names | named conceptual steps |
| Which tier model runs each step? | hidden in `params.model` | top-level per-step `model: fast` |
| What prompt / template / schema does each step use? | hard-coded in Python | path or inline block in the config |
| How is the frontier chosen? | hard-coded in `EventualCoverageScheduler` | named strategy with parameters |
| Which step writes articles, with which model? | hard-coded in `epoch.py` | top-level `articles.write.model` |
| How are concepts cross-linked? | hard-coded in `linker.py` | `cross_link.strategy` block |
| Where are people / entities identified? | hard-coded in `people.py` | a step in the conceptual sequence |
| Which extraction schema is in force? | one global template file | per-step `schema:` reference |

## Target shape (sketch — not yet implemented)

The conceptual file should be organized around **wiki steps**, not DAG
nodes. The DAG executor remains the execution substrate; a small loader
compiles the conceptual config into a `DagRunSpec` for execution, plus
applies overrides for prompts/templates/schemas.

```yaml
# Experiment recipe — high level, human-editable.
# Compiled into a DagRunSpec at runtime by wiki.discovery.config.
recipe_id: default_publication
description: >
  Publication-oriented wiki run: identify concepts and people from chunks,
  consolidate, cross-link, and write articles.

frontier:
  strategy: eventual_coverage
  budget_per_epoch: 64
  exploration_rate: 0.05
  priority: section_tier            # alternatives: recency, weight, hub-spoke

steps:
  - name: profile_documents
    model: fast
    prompt: prompts/profile_document.md

  - name: identify_concepts
    model: fast
    prompt: prompts/concept_extraction.md
    schema: schemas/concept_extraction.json
    units: [chunk, synopsis]
    multimodal: false

  - name: identify_people
    model: fast
    prompt: prompts/people_extraction.md
    schema: schemas/people_extraction.json
    units: [chunk]

  - name: identify_figures_tables
    model: vision
    prompt: prompts/figure_extraction.md
    schema: schemas/figure_extraction.json
    units: [figure, table]

  - name: consolidate
    model: balanced
    prompt: prompts/consolidate_notes.md
    inputs_from: [identify_concepts, identify_people, identify_figures_tables]

  - name: persist_canonical
    # no model — pure persistence step

  - name: cross_link
    model: fast
    strategy: hub_spoke
    prompt: prompts/cross_link.md
    schema: schemas/cross_link.json

  - name: write_articles
    model: balanced
    prompt: prompts/article_writer.md
    style_guide: prompts/style_guide.md
    revision:
      enabled: true
      model: deep
      prompt: prompts/article_revision.md

  - name: maintain
    model: fast
    prompt: prompts/maintenance.md
    triggers: [contradiction, weak_support, unresolved_gap]
```

Notes on the shape:

- **Steps are conceptual, not DAG nodes.** The compiler translates each
  step into one or more `DagNodeSpec`s. Most steps map 1:1; some
  (`consolidate`, `write_articles`) map to small sub-DAGs.
- **Models are tier names.** Vendor identity stays in `config.py`. The
  resolver in `llm/client.py` maps `fast` / `balanced` / `deep` to the
  active model id.
- **Prompts, schemas, style guides, and templates are files.** They live
  alongside the recipe and are version-controllable. Today they are
  buried in Python (`wiki/template.py`, `wiki/prompts/`).
- **Per-step `units`** decides which extraction units the step
  consumes. Strategy budgets are global (`frontier.*`) but per-step
  filters are local.
- **Frontier strategy is named.** The `EventualCoverageScheduler` is the
  current default; alternatives (recency-first, hub-spoke-first,
  similarity-gated) plug in by name.

## Files to expose as configs (today they are Python or implicit)

| Today | Should become |
|---|---|
| `wiki/template.py` (extraction template loader) | `prompts/concept_extraction.md` + `schemas/concept_extraction.json` |
| `wiki/people.py` (people extraction prompt baked in) | `prompts/people_extraction.md` + `schemas/people_extraction.json` |
| `wiki/figure_enrichment.py` (figure prompt baked in) | `prompts/figure_extraction.md` + `schemas/figure_extraction.json` |
| `wiki/maintenance.py` (gates + contradiction prompts baked in) | `prompts/maintenance.md` + structured triggers config |
| `wiki/linker.py` (cross-link heuristics in code) | `prompts/cross_link.md` + a strategy registry |
| `wiki/concepts/discovery.py` chunk filters (`_SKIP_SECTIONS`, length thresholds) | `recipes/<name>.yaml` under `frontier.filters` |
| Article writer prompt (currently inside `wiki/article.py`) | `prompts/article_writer.md` |

The directory layout should make these visible at a glance:

```
src/wikify/wiki/recipes/
  default_publication.yaml
  slide_deck.yaml
  notes_corpus.yaml
src/wikify/wiki/prompts/
  profile_document.md
  concept_extraction.md
  people_extraction.md
  figure_extraction.md
  cross_link.md
  article_writer.md
  article_revision.md
  maintenance.md
  consolidate_notes.md
src/wikify/wiki/schemas/
  concept_extraction.json
  people_extraction.json
  figure_extraction.json
  cross_link.json
```

The current `default_publication.yaml` (DAG-shaped) becomes an internal,
generated artifact: the recipe loader compiles `recipes/<name>.yaml`
into a `DagRunSpec` and writes the compiled spec to
`data/wiki/_meta/runs/<run_id>/compiled_workflow.yaml` for observability.
Users edit the recipe; the DAG file is debug output.

## Migration plan

This is its own slice. Suggested order:

1. **Extract prompts and schemas to files.** No behavior change. Move
   the strings buried in `wiki/template.py`, `wiki/people.py`,
   `wiki/figure_enrichment.py`, `wiki/article.py`, and
   `wiki/maintenance.py` into `src/wikify/wiki/prompts/` and
   `src/wikify/wiki/schemas/`. Update the call sites to load from the
   files. Tests stay green.
2. **Define the recipe schema.** Add `wiki/discovery/recipe.py` with
   typed dataclasses (`Recipe`, `Step`, `FrontierConfig`, `ModelTier`)
   and a loader that validates a YAML against them.
3. **Write the recipe compiler.** Add `wiki/discovery/recipe_compiler.py`
   that turns a `Recipe` into a `DagRunSpec`. Each conceptual step maps
   to one or more `DagNodeSpec`s; prompts/schemas are passed through
   each node's `params`.
4. **Codify the current default behavior as a recipe.** Author
   `recipes/default_publication.yaml` matching today's `epoch.py`
   conceptual flow (concept identification → graph → articles →
   maintenance → cross-link).
5. **Wire `wiki/runtime.py` to load a recipe by name.** CLI flag:
   `wikify wiki epoch --recipe default_publication`. The legacy direct
   `discover_concepts` call becomes a thin call into the compiler +
   executor.
6. **Delete the hand-written DAG YAML.** Once the compiler can produce
   it, the conceptual recipe is the only user-facing config.
7. **Add at least one alternative recipe** (`slide_deck.yaml` or
   `notes_corpus.yaml`) to prove the abstraction holds.

## Acceptance criteria

- A new user can read `recipes/default_publication.yaml` and understand
  what the wiki run does without reading any Python.
- Changing the model used to write articles is a one-line edit in the
  recipe file.
- Replacing the concept extraction prompt is editing one markdown file.
- A second recipe targeting a different document type runs end-to-end
  with no Python changes.
- Each run records the recipe id, recipe sha256, and the compiled
  `DagRunSpec` in observability.

## Open questions

- Should prompts be markdown with frontmatter (carrying their schema
  reference inline) or kept as separate files? Markdown-with-frontmatter
  is more cohesive but harder to validate; separate files are simpler.
- How much composition is needed? A Hydra-style composition layer would
  help if recipes share large blocks; a small in-house layer is enough
  if recipes stay self-contained.
- Per-step retries / escalation rules: in the recipe, or in a separate
  policy file?
- Where should evaluation rubrics (loss definitions) live in this
  layout? Probably alongside recipes as `eval/<name>.yaml`.
