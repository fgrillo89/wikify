# Wiki Deep Refactor — Activity Log

A running log of refactor work for review purposes. Each entry records
what changed, why, what was verified, and what remains. Append-only.

## 2026-04-07 — Slice Phase 1.A.2 / Phase 2.A (core/, corpus_tools, boundary clean)

Closes the boundary violation surfaced by Phase 1.A: no non-legacy wiki
module imports from `wikify.papers` anymore.

### What landed

- New `src/wikify/core/` package with `__init__.py` (no shared code yet
  beyond what's needed for this slice).
- `src/wikify/papers/retrieve/` → `src/wikify/core/retrieve/`. The
  retrieve package only depends on `store`, `config`, `graph`, `llm` —
  it was misnamed as a paper concern. All importers (cli, papers/agent,
  papers/generate, internal retrieve files) rebound in the same slice.
- New `src/wikify/core/corpus_tools.py` with three clean primitives:
  - `compute_graph_metrics() -> CorpusGraphMetrics` — no JSON wrapping;
    returns `by_paper`/`hub_ids`/`bridge_ids`/`frontier_ids`.
  - `search_corpus(query, *, top_k, max_tokens) -> CorpusSearchResult`
    — embedding-based corpus search returning paper ids + text bundle;
    no agent reading-log.
  - `read_paper_digest_text(paper_id, *, max_chars) -> str` — pure
    Python markdown digest, no JSON wrapping, no logging.
- Wiki callers rewired to `core.corpus_tools`:
  - `wiki/builder.py::_load_graph_metrics` — calls
    `compute_graph_metrics()` and looks up display names locally.
  - `wiki/maintenance.py` — uses `compute_graph_metrics().by_paper`,
    deletes the dependency on `wiki.mapreduce._parse_graph_metrics`.
  - `wiki/mapreduce.py` — uses `compute_graph_metrics()`,
    `search_corpus()`, `read_paper_digest_text()`. Deletes
    `_parse_graph_metrics` and `_extract_paper_ids_from_search` (no
    longer needed since `search_corpus` returns ids directly).
  - `wiki/graph/routing.py::_fallback_search` — uses `search_corpus()`.
- `tests/test_wiki/test_mapreduce.py` rewritten against the new
  primitives (mocks `compute_graph_metrics` / `search_corpus` /
  `read_paper_digest_text` instead of the old JSON-wrapped functions).

### Boundary verification

```
$ grep -rn "from wikify.papers" src/wikify/wiki src/wikify/ingest \
    | grep -v wiki/legacy
(no matches)
```

The `wiki must not import papers` rule now holds for every non-legacy
wiki module. The `wiki/legacy/` modules are exempt by design — they
are slated for deletion when the legacy CLI commands are migrated.

**Verification:** 852 tests pass. Test count dropped from 861 because
the rewritten `test_mapreduce.py` consolidated several JSON-parsing
tests that no longer apply (the JSON parser is gone).

---

## 2026-04-07 — Slice Phase 1.A (papers boundary extraction)

Moved every paper-writing concern under a dedicated `wikify.papers`
namespace. No shims, no parallel paths.

- `src/wikify/agent/`    → `src/wikify/papers/agent/`
- `src/wikify/generate/` → `src/wikify/papers/generate/`
- `src/wikify/retrieve/` → `src/wikify/papers/retrieve/`
- `src/wikify/evaluate/` → `src/wikify/papers/evaluate/`
- `src/wikify/export/`   → `src/wikify/papers/export/`
- `src/wikify/prompts/`  → `src/wikify/papers/prompts/`
- New `src/wikify/papers/__init__.py`

Bulk-rebound 64 files (`wikify.{agent,generate,retrieve,evaluate,export,prompts}`
→ `wikify.papers.*`) plus the recipe YAML. **861 tests pass.**

### Known boundary violation surfaced by this move

Several wiki modules still import corpus-level tool helpers from
`wikify.papers.agent.tools`:

- `wiki/builder.py`        → `get_graph_metrics`
- `wiki/graph/routing.py`  → `search_papers`
- `wiki/maintenance.py`    → `get_graph_metrics`
- `wiki/mapreduce.py`      → `get_graph_metrics`, `read_paper_digest`,
                              `search_papers`
- `wiki/legacy/agent.py`, `wiki/legacy/sitemap.py` (legacy — fine for now)

These are not paper-writing concerns; they are corpus-level retrieval
helpers that happen to live in the agent tools module. **Next slice:**
extract `get_graph_metrics`, `search_papers`, `read_paper_digest`,
`read_section`, `deep_read`, and `find_synthesis_opportunities` into a
shared corpus-tools module (e.g. `src/wikify/core/corpus_tools.py`),
update both the wiki callers and the papers agent to import from
there. After that the boundary rule (`wiki must not import from papers`)
will hold for all non-legacy code.

---

## 2026-04-07 — Slice S3.G (legacy sitemap path isolated)

Moved the sitemap-first wiki flow into `wiki/legacy/`:

- `wiki/sitemap.py` → `wiki/legacy/sitemap.py`
- `wiki/agent.py`   → `wiki/legacy/agent.py`
- New `wiki/legacy/__init__.py` with a clear "slated for deletion"
  doc header.

Updated all 9 import sites in the same slice (`cli.py`,
`wiki/builder.py`, `wiki/linker.py`, `wiki/__init__.py`, the moved
`legacy/agent.py`, plus 4 test files). 861 tests pass.

The legacy modules are retained for now because the CLI still exposes
the sitemap-first commands and tests cover them. They will be deleted
when those CLI commands are migrated to the agent-native runtime
(epoch / query / maintain).

---

## 2026-04-07 — Slice S3.F (presentation subpackage)

Moved the wiki presentation layer into its own subpackage:

- `wiki/html.py`       → `wiki/presentation/html.py`
- `wiki/dashboard.py`  → `wiki/presentation/dashboard.py`
- `wiki/layout.py`     → `wiki/presentation/layout.py`
- `wiki/templates/`    → `wiki/presentation/templates/`
- New `wiki/presentation/__init__.py`

Updated all 9 import sites in the same slice (`agent/tools.py`,
`cli.py`, `wiki/builder.py`, `wiki/epoch.py`, `wiki/linker.py`,
`wiki/observability/runs.py`, `wiki/runtime.py`, plus the moved
`html.py` and `dashboard.py` themselves). 861 tests pass; the Jinja
template loader resolves relative to the moved file so no path
config needed updating.

---

## 2026-04-07 — Slice S3.E (observability subpackage)

Moved `wiki/telemetry.py` (423 LOC) into `wiki/observability/runs.py`
with no shims; updated all 4 import sites (`wiki/epoch.py`,
`wiki/runtime.py`, `tests/test_wiki/test_telemetry.py`,
`tests/test_wiki/test_runtime.py`). 861 tests pass. Splitting `runs.py`
into `stages.py` / `snapshots.py` / `logs.py` / `export.py` per the
target layout is a follow-up.

---

## 2026-04-07 — Slice S3.B (graph subpackage)

Moved the wiki graph layer into its own subpackage with no shims.

- `src/wikify/wiki/concept_graph.py` → `src/wikify/wiki/graph/build.py`
- `src/wikify/wiki/domains.py` → `src/wikify/wiki/graph/domains.py`
- `src/wikify/wiki/routing.py` → `src/wikify/wiki/graph/routing.py`
- New `src/wikify/wiki/graph/__init__.py` re-exports the public surface.
- Updated all 7 import sites in the same slice (`wiki/epoch.py`,
  `wiki/graph/domains.py`, `wiki/graph/routing.py`, `agent/tools.py`,
  plus 3 test files).

`wiki/graph/build.py` is the original 650-line `concept_graph.py`
unchanged. Splitting it into `build.py` / `importance.py` /
`topology.py` per the target layout is a follow-up slice — the move
itself was the prerequisite.

**Verification:** 861 tests pass; ruff clean on `wiki/graph/`.

---

## 2026-04-07 — Slice S3.A.3 (recipe layer landed)

Implements step 1–4 of the recipe migration plan from
`docs/design/workflow-config-redesign.md`: a user-facing recipe layer
sits on top of the DAG executor. Conceptual wiki steps, models,
prompts, and frontier strategy are now visible at the top of one YAML
file instead of buried in DAG node ids.

### What landed

- `wiki/discovery/recipe.py` — typed `Recipe`, `StepConfig`,
  `FrontierConfig`, `KNOWN_STEP_KINDS`, `KNOWN_MODEL_TIERS`,
  `RecipeError`, plus `parse_recipe` / `load_recipe_yaml`. Validation
  covers: required fields, unknown step kinds, unknown model tiers,
  duplicate step names, dangling `inputs_from` references. Each
  recipe carries a sha256 `config_hash` for observability.
- `wiki/discovery/recipe_compiler.py` — `compile_recipe(recipe) -> DagRunSpec`.
  Each conceptual step is lowered into one or more DAG nodes via a
  small dispatch table. `identify_concepts` lowers to plan + extract;
  `consolidate` to resolve_candidates; `persist_canonical` to
  persist_notes. Step kinds without a DAG implementation yet
  (`cross_link`, `write_articles`, `maintain`) are recorded in
  `spec.params["deferred_steps"]` so observability can report them
  but no placeholder DAG node is emitted.
- `wiki/recipes/default_publication.yaml` — the default user-facing
  recipe. Conceptually mirrors today's `epoch.py` flow: profile →
  identify (concepts/people/figures) → consolidate → persist →
  cross_link → write_articles → maintain.
- `wiki/discovery/__init__.py` re-exports the recipe surface
  (`Recipe`, `StepConfig`, `FrontierConfig`, `RecipeError`,
  `load_recipe_yaml`, `parse_recipe`, `compile_recipe`).
- `tests/test_wiki/test_discovery/test_recipe.py` — 6 tests covering:
  default recipe loads + compiles + DAG-validates, end-to-end
  execution of the compiled recipe through the existing executor and
  registry, rejection of unknown step kinds, missing recipe id,
  dangling `inputs_from`, and consolidate-without-upstream-notes.

### What this unblocks

- Changing the model used to write articles is now a one-line edit in
  `recipes/default_publication.yaml` (`write_articles.model: deep`).
- A second recipe (e.g. `recipes/slide_deck.yaml`) for a different
  document type is a new YAML file — no Python changes.
- Each compiled run carries `recipe_id`, `recipe_config_hash`, and the
  full `deferred_steps` list in observability.

### Verification

- `uv run pytest -q` → **861 passed, 0 failed**.
- `uv run ruff check src/wikify/wiki/discovery src/wikify/wiki/concepts tests/test_wiki/test_discovery` → clean.

### What still remains for the recipe migration

- Step 1 of the design doc (extract prompts/schemas to standalone
  files) is not yet done. The recipe references prompt/schema paths
  but the underlying code still loads them from Python. Next slice.
- Wire `wiki/runtime.py` (or `wiki/epoch.py`) to load a recipe and
  execute it through the compiler instead of the ad-hoc loop.
- Delete `wiki/discovery/workflows/default_publication.yaml` once the
  recipe layer is the only entry point.

---

## 2026-04-07 — Slice S3.A.2 (full concepts decomposition + agent-native rewire + haiku purge)

Per the user directive "remove old/legacy code/pkgs once they are addressed
by the refactor" and "you ARE the LLM, agentic app — no LLM SDK calls in
core modules", this slice deletes the legacy LLM-calling extraction
pipeline entirely and rewires `wiki/epoch.py` through an agent-native
discovery driver.

### What was implemented

1. **`wiki/concepts/_impl.py` deleted.** The 1674-line monolith is gone.
   The package now contains only responsibility-focused sub-modules with
   real implementations (no forwarding, no shims):
   - `records.py` — `DiscoveryResult`, `get_concept_by_name`, `list_concepts`
   - `merge.py` — `merge_concept_records`, `apply_redirect_map`,
     `stage_extractions`, `commit_staged_extractions`,
     `clear_staged_extractions`, ChromaDB staging helpers
   - `evidence.py` — `store_evidence`, `store_gaps`, `store_parameters`,
     `store_occurrences`, `store_relation_evidence`, `fuzzy_match_quote`
   - `discovery.py` — agent-native `discover_concepts(paper_ids, epoch, *, extractor)`
   - `__init__.py` re-exports the package's public surface

2. **Legacy LLM extraction pipeline deleted.** Functions removed
   permanently (not moved, not stubbed):
   - `_extract_rich_from_chunk`, `_parse_concepts_from_rich`,
     `_extract_from_chunk`
   - `extract_from_publication`, `_identify_deepening_chunks`
   - `_extract_concepts_bundle_from_source`, `extract_concepts_from_source`
   - `get_mining_frontier`, `record_mining`, `concept_aware_prefilter`
   - The old monolithic `discover_concepts` (replaced by the agent-native one)
   - `_chunk_tier`, all `_SECTION_TIERS`, `_EXPLORATION_RATE`, `_CONCEPT_SIM_THRESHOLD`
     constants, and the legacy `FAST_MODEL` constant in this module
   - `wiki/concepts/persistence.py` (forwarding facade) — no longer needed

3. **Agent-native `wiki/concepts/discovery.py`.** New module that
   replaces the legacy pipeline. It:
   - loads chunks from the SQL store (data prep, no LLM calls)
   - builds typed `ExtractionUnit` lists via the discovery contracts
   - delegates to an injected `AgentExtractor` (`EchoExtractor` default)
   - translates agent-produced `ExtractionNote`s into `ConceptRecord`s
   - merges through the canonical `merge_concept_records` path
   - returns `DiscoveryResult` with the agent-supplied rich extractions
     so the existing `store_*` consumers in `epoch.py` keep working

   No LLM SDK is imported. Without an extractor, the function logs
   `extractor=EchoExtractor (no-agent-configured)` and produces zero new
   concepts — surfacing "no agent wired in" cleanly instead of crashing.

4. **`wiki/epoch.py` rewired.** The Pass 1 call site
   `discover_concepts(paper_ids, epoch, model=FAST_MODEL)` is now
   `discover_concepts(paper_ids, epoch)` — the orchestrating agent
   supplies the extractor in production. `FAST_MODEL` is now imported
   from `wiki/domains.py` (where it remains a tier-name constant for
   the domain discovery code path — that path is the next slice).

5. **Vendor naming purge across the wiki and adapters.** All "haiku"
   references in comments, docstrings, dashboard text, and tier
   aliases have been replaced with neutral terms:
   - `wiki/article.py`, `wiki/dashboard.py`, `wiki/epoch.py`,
     `wiki/maintenance.py`, `wiki/mapreduce.py`, `wiki/template.py`,
     `extract/section_summarizer.py`, `llm/onnx_provider.py`,
     `llm/vision.py`, `retrieve/strategies/hub_spoke.py`,
     `store/models.py` — all comment / docstring "haiku" references
     replaced with "fast tier" or "fast-tier".
   - `wiki/dashboard.py:540` Plotly annotation `"L=0.3 (haiku→sonnet)"`
     → `"L=0.3 (fast→balanced)"` (visible in the user-facing dashboard).
   - `llm/client.py`: tier alias map cleaned up. `"haiku"`, `"sonnet"`,
     `"opus"` aliases removed; only neutral tier names (`fast`, `cheap`,
     `map`, `balanced`, `default`, `writer`, `deep`, `reasoning`,
     `audit`, `vision`) remain.
   - `tests/test_llm/test_client.py` updated to use neutral aliases.
   - The only remaining "haiku" strings are in `config.py:25`
     (`llm_fast_model = "claude-haiku-4-5-20251001"`) and
     `llm/hooks.py:51` (cost-table key for the same literal model id).
     These are vendor identity, which by the cleanup policy belongs in
     configuration only.

6. **Tests updated.**
   - `tests/test_wiki/test_concepts.py` rewritten: 14 tests for the
     deleted extraction pipeline removed; remaining tests rebound to
     the new module locations (`wikify.wiki.concepts.{merge,records,evidence}.get_session`).
   - `tests/test_llm/test_client.py` updated to use neutral tier names.
   - `tests/test_wiki/test_epoch.py` patch site is signature-agnostic
     (sets a return value), so it picks up the new `discover_concepts`
     signature without changes.

### Verification

- **Full test suite: 855 passed, 0 failed** (`uv run pytest -q`).
- `uv run ruff check` clean on `src/wikify/wiki/concepts`,
  `src/wikify/wiki/discovery`, and `tests/test_wiki/test_discovery`.

### Open design problem raised this turn

- **User-friendly workflow config layer.** The current
  `wiki/discovery/workflows/default_publication.yaml` is faithful to
  the DAG runtime but too low-level (node ids, artifact refs, impl
  strings). A separate design note lays out a recipe layer organized
  around conceptual wiki steps with prompts/schemas/templates as
  standalone files: see
  [`docs/design/workflow-config-redesign.md`](../design/workflow-config-redesign.md).
  Added a banner at the top of the DAG YAML pointing to that note,
  and added an "open design problem" callout to S3.A in the refactor
  plan. Implementation of the recipe layer is its own slice and is
  listed below.

### What still remains (next slices)

0. **Workflow config UX (recipe layer).** Implement
   `docs/design/workflow-config-redesign.md`: extract prompts/schemas
   to files, define `Recipe` dataclasses, write the recipe compiler,
   codify the current default flow as `recipes/default_publication.yaml`,
   wire `wiki/runtime.py` to load a recipe by name, delete the
   hand-written DAG YAML, and ship at least one alternative recipe.
1. **Wire the runtime extractor.** `wiki/concepts/discovery.discover_concepts`
   accepts an `AgentExtractor` but `epoch.py` does not yet thread one
   through from the runtime. Next: extend `wiki/runtime.py` (or the CLI
   adapter) to accept an extractor and pass it down. In the Claude Code
   adapter, the extractor will dispatch to subagent reasoning over the
   units; in other runtimes, the runtime supplies its own.
2. **Drive Pass 1 through the DAG executor.** Today
   `discover_concepts` is a small ad-hoc loop. Next: replace that loop
   with `DagExecutor.run(load_workflow_yaml("default_publication"))`
   so observability (workflow id, node timings, config hash, multimodal
   usage) is captured per epoch.
3. **Phase 1 papers boundary.** Move `wikify.agent`, `wikify.generate`,
   `wikify.retrieve`, `wikify.evaluate`, and paper exports under
   `src/wikify/papers/**`. Update every caller in the same slice. No
   shims.
4. **Phase 1 CLI/MCP split**, **Phase 2 `core/` boundary**, **Phase 3.B–G**,
   **Phase 4 doc neutralization** — as previously documented.

---

## 2026-04-07 — Slice S3.A.1 (initial discovery scaffold + concepts package; superseded in part by S3.A.2 above)

### What was implemented

1. **`wikify.wiki.discovery` subsystem** (new, additive, fully typed)

   New package under `src/wikify/wiki/discovery/`:

   | Module | Responsibility |
   |---|---|
   | `contracts.py` | Typed dataclasses: `DocumentProfile`, `ArtifactRef`, `ExtractionUnit`, `ExtractionNote`, `CandidateConcept`, `CoverageRecord`, `DagNodeSpec`, `DagRunSpec`, `DiscoveryStrategy`, `UnitKind`, `ModalityKind` |
   | `artifacts.py` | Run-scoped `ArtifactStore` with kind checking |
   | `dag.py` | `validate_dag` — duplicate ids, missing deps, kind mismatch, cycle detection, topo sort, seed-artifact support |
   | `executor.py` | `DagExecutor` + `DagExecutionResult` reporting workflow id, strategy id, config hash, config source, node timings, multimodal usage |
   | `extractors.py` | `AgentExtractor` Protocol + `EchoExtractor` (deterministic, work-item only — **not a fake LLM**) |
   | `registry.py` | `NodeRegistry` + `default_registry()` |
   | `nodes.py` | Built-in `profile_document`, `plan_units`, `extract_text`, `extract_multimodal`, `resolve_candidates`, `persist_notes`. Extract nodes delegate to an injected `AgentExtractor`; no LLM SDK is imported. |
   | `units.py` | Document-type-aware unit planner |
   | `multimodal.py` | Figure / table / page-image unit builders |
   | `notes.py` | `InMemoryNoteStore` + `JsonlNoteSink` for inspectable extraction notes |
   | `strategies.py` | `StrategyRegistry` + 3 built-in strategies (publication / all-unit-sweep / multimodal-first-slides) |
   | `planner.py` | Document-type-aware strategy selection with override map |
   | `scheduler.py` | `EventualCoverageScheduler` — guarantees full coverage in `ceil(n / budget)` epochs |
   | `config.py` | YAML → typed `DagRunSpec` loader with sha256 config hashing (no Hydra) |
   | `workflows/default_publication.yaml` | Bundled 6-node workflow that executes end-to-end |

2. **`wikify.wiki.concepts` is now a canonical package, no compat shim**

   - `src/wikify/wiki/concepts.py` (1674 LOC) → `src/wikify/wiki/concepts/_impl.py`.
   - `wiki/concepts/__init__.py` re-exports the explicit public surface from `._impl`.
   - Sibling sub-modules `records.py` / `merge.py` / `evidence.py` / `persistence.py` group the public surface by responsibility and forward to `_impl`. They are the eventual home for the decomposed implementation.
   - **No back-compat shim layer**: there is no `_concepts_legacy.py`, no auto-mirroring of private symbols, no old-path re-exports. Callers and tests were updated to the canonical path in the same slice.
   - `tests/test_wiki/test_concepts.py` updated: imports `wikify.wiki.concepts._impl as mod` and patches `wikify.wiki.concepts._impl.<symbol>`.

3. **Vendor naming removed**

   - `HAIKU_MODEL` → `FAST_MODEL` across `wiki/concepts/_impl.py`, `wiki/article.py`, `wiki/epoch.py`, `wiki/template.py`, `wiki/domains.py`, `wiki/concepts/persistence.py`, `wiki/concepts/__init__.py`.
   - `MAP_HAIKU_BUDGET` → `MAP_FAST_BUDGET` in `wiki/mapreduce.py`.
   - Vendor identity (`claude-haiku-4-5-...`) lives only in `config.py` settings, not in code names.

4. **Agent-native discovery (no LLM SDK in core modules)**

   - The previous design had `extract_text`/`extract_multimodal` calling stub functions framed as "future litellm-backed implementations".
   - Replaced with `AgentExtractor` Protocol. The orchestrating agent supplies the extractor at runtime; tests use `EchoExtractor` which only emits structured work-items recording what *would* have been asked.
   - This matches the project's "skill-first / agent-native" architecture: pipelines are orchestrated by the LLM runtime, not by Python scripts calling LLM SDKs.

5. **Refactor plan updated**

   - New "Cleanup Policy (Hard Rule)" section codifies: no shims, no parallel paths, breaking changes allowed, no vendor-specific names, agent-native subsystems.
   - S3.A status block updated to reflect what's landed and what's left.

### Tests

- New: `tests/test_wiki/test_discovery/` — 13 tests covering DAG validation (cycles, missing deps, kind mismatch, dup ids, seed artifacts, unknown depends_on), scheduler eventual coverage / weight priority / processed-skip, end-to-end YAML workflow execution with multimodal + coverage assertions, planner routing, YAML rejection.
- Updated: `tests/test_wiki/test_concepts.py` — patch sites repointed at `_impl`.
- **Full `tests/test_wiki/` suite: 392 passed, 0 failed** after all changes.
- `uv run ruff check src/wikify/wiki/discovery tests/test_wiki/test_discovery` clean. (One pre-existing E501 in `wiki/concepts/_impl.py` line 403 — not introduced by this slice.)
- `uv run ty` not run — `ty` binary is not present in the local env (`Failed to spawn: ty`). All new code is fully type-annotated.

### Files added

```
src/wikify/wiki/discovery/__init__.py
src/wikify/wiki/discovery/artifacts.py
src/wikify/wiki/discovery/config.py
src/wikify/wiki/discovery/contracts.py
src/wikify/wiki/discovery/dag.py
src/wikify/wiki/discovery/executor.py
src/wikify/wiki/discovery/extractors.py
src/wikify/wiki/discovery/multimodal.py
src/wikify/wiki/discovery/nodes.py
src/wikify/wiki/discovery/notes.py
src/wikify/wiki/discovery/planner.py
src/wikify/wiki/discovery/registry.py
src/wikify/wiki/discovery/scheduler.py
src/wikify/wiki/discovery/strategies.py
src/wikify/wiki/discovery/units.py
src/wikify/wiki/discovery/workflows/default_publication.yaml
src/wikify/wiki/concepts/__init__.py
src/wikify/wiki/concepts/records.py
src/wikify/wiki/concepts/merge.py
src/wikify/wiki/concepts/evidence.py
src/wikify/wiki/concepts/persistence.py
tests/test_wiki/test_discovery/__init__.py
tests/test_wiki/test_discovery/test_dag.py
tests/test_wiki/test_discovery/test_scheduler.py
tests/test_wiki/test_discovery/test_yaml_workflow.py
docs/refactor/activity-log.md  (this file)
```

### Files renamed

```
src/wikify/wiki/concepts.py        -> src/wikify/wiki/concepts/_impl.py
```

### Files modified (vendor naming + test patch sites)

```
src/wikify/wiki/article.py
src/wikify/wiki/concepts/_impl.py     (HAIKU_MODEL -> FAST_MODEL)
src/wikify/wiki/domains.py
src/wikify/wiki/epoch.py
src/wikify/wiki/mapreduce.py
src/wikify/wiki/template.py
tests/test_wiki/test_concepts.py
docs/project-status.md
docs/refactor/wiki-deep-refactor-plan.md
```

### What still remains (next slices)

1. **Decompose `wiki/concepts/_impl.py`** into the sibling sub-modules
   in place (no shims). The sub-modules currently forward to `_impl`;
   the goal is for `_impl.py` to disappear once each function physically
   lives in `records.py` / `merge.py` / `evidence.py` / `persistence.py`.
2. **Route `wiki/epoch.py` through the new DAG executor.** Today
   `epoch.py` still calls `discover_concepts` directly. Next: replace
   that with a `DagRunSpec` loaded from the bundled workflow YAML and
   executed against an `AgentExtractor` supplied by the runtime.
3. **Phase 1 — papers boundary extraction.** Move `wikify.agent`,
   `wikify.generate`, `wikify.retrieve`, `wikify.evaluate`, and
   paper-specific exports under `src/wikify/papers/**`. Update every
   caller in the same slice. No shims.
4. **Phase 1 — CLI / MCP split.** Make `wikify.cli` a thin adapter that
   mounts `wikify wiki ...` and `wikify papers ...` from separate
   adapter modules.
5. **Phase 2 — `core/` boundary** for shared infra (`config`, `llm`,
   `store`).
6. **Phase 3.B–G** — split graph, articles, runtime ops, observability,
   presentation, legacy sitemap.
7. **Phase 4** — doc neutralization (architecture + status).
8. **Pre-existing E501 in `wiki/concepts/_impl.py:403`** — fix as part
   of the `_impl.py` decomposition slice.
