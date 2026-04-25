# Skill-centric Wikify redesign — implementation plan

This plan satisfies the binding contract in `docs/skill-centric-execution-plan.md`. It enumerates the legacy surface, fixes a preservation inventory, names the final package-per-noun layout (dropping `cli_cmds/`, `store/`, `citestore/`, `distill/`), the final CLI tree, the canonical skill set, twelve disjoint workstreams (W0 mechanical rename + W1–W11), a four-phase legacy-removal sequence, MVP paths, and the first three PRs. The eight load-bearing brief decisions are not relitigated.

Divergences from the brief's prose, all consistent with its load-bearing decisions: the brief suggested `cli_cmds/<noun>.py` + `stores/<noun>.py` — this plan uses a package-per-noun layout instead, so each top-level package owns one domain (file IO, fluent query if any, and the verbs that mutate it). The four bundle-internal packages (`run/`, `work/`, `draft/`, `wiki/`) live under a shared `bundle/` umbrella because they only exist inside one wiki bundle and that cohesion is worth expressing in the directory tree. `paths.py` is eliminated; path conventions live on a `Bundle` dataclass in `api.py`. `schema.py` splits per-domain (`bundle/draft/schema.py`, `bundle/work/schema.py`). The build-state package is named `bundle/work/` to match the CLI noun (`wikify work`) and the on-disk directory (`work/`). `prompts/` stays Python-side; it is not moved to the skill tree. `distill/` is dissolved entirely in W0 — `dossier.py` → `bundle/work/`, `author_context.py` → `bundle/draft/`, `seed.py` and `field_detect.py` → `corpus/`, `preload.py` → `bundle/draft/preload.py`, `write_runner.py` → `bundle/wiki/post_commit.py`.

## 1. Preservation inventory

Logic that must survive the redesign. Tags: KEEP (move only), REFACTOR (signature/IO change), REPLACE (rewrite — must justify why reuse fails).

| path | role | tag | new home | rationale |
|---|---|---|---|---|
| `src/wikify/prompts/` (16 files) | writer / refine / extract / person / field guides / artifact templates | KEEP | `src/wikify/prompts/` (unchanged) — Python-side, assembled by `DraftBuilder` | Different consumer than skills (model input vs agent context); keep file location. |
| `src/wikify/distill/dossier.py` whole file | Dossier class + canonicalize() + Candidate + DossierStore | KEEP | `src/wikify/bundle/work/dossier.py` (W0 moves whole file; W4 splits `canonicalize`+`Candidate` into `bundle/work/canonicalize.py`) | Backs `work.md` ControlCard. |
| `src/wikify/distill/author_context.py` (174 lines) | author-context for person pages | KEEP | `src/wikify/bundle/draft/author_context.py` | Lives next to draft assembly. |
| `src/wikify/distill/seed.py` | greedy seed selection | KEEP | `src/wikify/corpus/seed.py` | Surfaced via `corpus find --seed`. |
| `src/wikify/distill/field_detect.py` | field classification | KEEP | `src/wikify/corpus/field_detect.py` | Called from `corpus check`. |
| `src/wikify/distill/preload.py` | evidence pre-loading | REFACTOR | `src/wikify/bundle/draft/preload.py` (W0 moves; W5 folds into `bundle/draft/builder.py`) | Caller surface changes in W5; logic preserved. |
| `src/wikify/distill/write_runner.rebuild_wiki_graph` | post-commit graph + vectors rebuild | KEEP | `src/wikify/bundle/wiki/post_commit.py::rebuild_wiki_graph` (W0 moves; W6 absorbs into `bundle/wiki/commit.py::rebuild_projections()`) | Called by `wiki commit` and `wiki build graph`. |
| `src/wikify/schema.py` (`WriteRequest`, `WriteResponse`, `WriteEvidenceRef`, `_check_wikipedia_structure`, `_check_figure_mentions`, `QuoteNotInChunkError`, `_split_sections`, `_has_section`) | write-side Pydantic + structural checks | KEEP | `src/wikify/bundle/draft/schema.py` | Owned by the draft domain. |
| `src/wikify/schema.py` (`ExtractRequest`, `ExtractResponse`, `ExtractedConcept`, `FigureCaption`, `Equation`, `Parameter`, `Relationship`, `ImageRef`, `EquationRef`) | extract-side Pydantic | KEEP | `src/wikify/bundle/work/schema.py` | Owned by the work / concept-extraction domain. |
| `src/wikify/baselines/_evidence.select_evidence_chunks_for_page` | per-page evidence helper | KEEP | `src/wikify/corpus/queries.py::select_evidence()` | Pure ranking; corpus-side. |
| `src/wikify/baselines/config.py::BaselineConfig` | baseline knobs | REPLACE | per-workflow-skill frontmatter (`wikify-baseline/SKILL.md`) | Strategy belongs in skills. |
| `src/wikify/citestore/graph.py` (807 lines) | corpus fluent KG | KEEP | `src/wikify/corpus/graph.py` | Surfaced through `corpus find/show/list`. |
| `src/wikify/citestore/{db,resolver,bibtex,parse,models}.py` | citation index, BibTeX, DOI resolution | KEEP | `src/wikify/citations/{db,resolver,bibtex,parse,models}.py` | Standalone — consumed by `ingest/`, unrelated to graph algebra. |
| `src/wikify/citestore/__main__.py` | debug entry point | REPLACE | deleted; `wikify corpus show` replaces it | CLI surface replaces ad-hoc debug entry. |
| `src/wikify/store/wiki_graph.py` (450 lines) | wiki fluent KG | KEEP | `src/wikify/bundle/wiki/graph.py` | Surfaced through `wiki find`. |
| `src/wikify/store/wiki_bundle.py` (376) | wiki page parser/writer | KEEP | `src/wikify/bundle/wiki/page.py` | Backs `wiki show`/`wiki commit`. |
| `src/wikify/store/wiki_index.py` (399) | `_index.json` index + aliases | KEEP | `src/wikify/bundle/wiki/index.py` | Backs `wiki list` and `wiki commit`. |
| `src/wikify/store/{wiki_files,bundle_embeddings,page_naming}.py` | wiki page IO + embeddings + slug naming | KEEP | `src/wikify/bundle/wiki/{files,embeddings,page_naming}.py` | Backs `wiki commit` and `wiki find`. |
| `src/wikify/store/{corpus,vectors,vectors_meta,doc_markdown,images_index,equations_index,bibliography}.py` | corpus chunk/vector/figure stores | KEEP | `src/wikify/corpus/{chunks,vectors,vectors_meta,doc_markdown,images_index,equations_index,bibliography}.py` | Read by ingest, eval, and `corpus/queries.py`. |
| `src/wikify/eval/{metrics,community,audit,stats,claim_sampler}.py` | M1/M3/M5/M6 metric math | KEEP | `src/wikify/eval/` (unchanged) | Already cohesive subsystem. |
| `src/wikify/eval/trace_replay.py` | event log replay | REFACTOR | reads `run/events.jsonl` | Input path changes; logic invariant. |
| `src/wikify/render/html/` (3 files) | Jinja2 site renderer | REFACTOR | `src/wikify/render/` (unchanged); reads `wiki/` + `derived/` | Templates unchanged; path resolution changes. |
| `src/wikify/ingest/` (33 files) | parse/chunk/embed/graph pipeline | KEEP | `src/wikify/ingest/` (unchanged) | Surfaced via `corpus build/refresh`. |
| `src/wikify/cli_cmds/_helpers.py` | shared error/lock helpers | KEEP | `src/wikify/cli/_helpers.py` | Extended with exit codes 3 (budget) and 4 (stale-claim broken). |
| `src/wikify/cli_io.py` | Typer wrapper for CLI IO capture | REFACTOR | `src/wikify/cli/_io.py` | Emits `cli_invoked` events into `run/events.jsonl`; writes large IO to `run/io/<event_id>.{stdout,stderr}.txt`. |
| `src/wikify/session.py` (cost aggregation: `_aggregate_calls_jsonl`, `_initial_by_role`, `_update_agg`, `_agg_to_dict`) | per-role/per-tier cost rollup math | KEEP | `src/wikify/bundle/run/cost.py` | Runs on `events.jsonl` filtered to `type == "call"`. |
| `src/wikify/session.py` (`SessionLockHeldError`) | lock-contention exception | KEEP | `src/wikify/bundle/run/lock.py` | Stable error type. |
| `src/wikify/meter.py` (`TierPrice`, `_coerce_tier`) | tier→price table | KEEP | `src/wikify/bundle/run/cost.py` | Used by cost aggregation. |
| `.claude/skills/wikify/reference/{schemas,cli-tool-surface,write-constraints,citation-format,tiers,escalation,knowledge-graph,wiki-graph,atoms}.md` (9 files) | shared agent-side references | KEEP | `.claude/skills/wikify/references/<name>.md` (under new shared mega-skill `SKILL.md`) | `schemas.md` and `cli-tool-surface.md` get content updates in W9; the rest move verbatim. |
| `.claude/skills/wikify/workflows/run-baseline.md` | baseline workflow doc | REFACTOR | `.claude/skills/wikify-baseline/SKILL.md` (≤500 lines) | Frontmatter introduced; body trimmed. |
| `tasks/lessons.md`, `CLAUDE.md` corrections | tribal knowledge | KEEP | unchanged | Project memory is cumulative. |

REPLACE entries: `BaselineConfig` (strategy → skills), `citestore/__main__.py` (CLI replaces debug), `distill/__init__.py` + `baselines/__init__.py` + `store/__init__.py` + `cli_cmds/__init__.py` + `citestore/__init__.py` (module aggregators are unnecessary once functions move into specific packages).

## 2. Legacy enumeration and phase tags

Phase A introduces the new layout (W0 rename, W1–W8 add new domain modules) alongside legacy CLI handlers. Phase B migrates skills + render + eval to the new surface and runs the telemetry parity gate. Phase C deletes legacy CLI nouns, top-level legacy commands, the `session.py`/`meter.py` shells, `baselines/`, the dead `distill/` files, and legacy bundle accessors. Phase D collapses adapters and ships doc rewrites.

| path | role | phase deleted in PR | rationale |
|---|---|---|---|
| `cli/legacy/{session,kg,meter}.py` | legacy CLI nouns | C — `redesign/c-cli-retire-session-kg-meter` | Replaced by `wikify run` + `work claim/release` + cost via events. |
| `cli/legacy/{extract,draft,validate,bundle}.py` | legacy CLI nouns | C — `redesign/c-cli-retire-extract-draft-validate-bundle` | Replaced by `work add concept`, `draft build/show/check`, `wiki commit`. |
| `cli/legacy/` dir + `__init__.py` (post W0) | legacy registrations | C — `redesign/c-cli-prune-toplevel-and-paths` | Empty after the two PRs above. |
| `src/wikify/session.py` shell | `SessionV1`, `init_session`, `apply_merge_patch`, `write_run_snapshot`, `acquire_lock` | C — `redesign/c-store-retire-session-meter` | `RunStateV1` + `RunStore` replace; merge-patch becomes typed `run set`; snapshots computed on demand. |
| `src/wikify/meter.py` shell | `CallRecord`, `CostMeter`, `BudgetExceededError` | C — same PR | Cost from `events.jsonl`; budget gate is exit code 3. |
| `src/wikify/baselines/` | `BaselineConfig`, evidence helper | C — `redesign/c-baselines-debug-retire` | Strategy → skill frontmatter; helper moved in W3. |
| `src/wikify/distill/` (whole directory, dissolved in W0) | aggregator + pre-load + post-commit graph rebuild | A — W0 moves all surviving files (`dossier`, `author_context`, `seed`, `field_detect`, `preload`, `write_runner`) into their owning packages and deletes the directory. No Phase C action needed. | The package was a grab-bag; package-per-noun homes are clearer. |
| `src/wikify/citations/__main__.py` | debug entry point | C — same PR | Replaced by `wikify corpus show`. |
| `src/wikify/paths.py` shell | legacy `BundlePaths`/`CorpusPaths` accessors | C — `redesign/c-cli-prune-toplevel-and-paths` | Replaced by `Bundle`/`Corpus` in `api.py`. |
| `cli/legacy/__init__.py` flat commands `trace`/`sample-claims`/`html`/`field-detect` | legacy top-level | C — same PR | Replaced by `render`, `eval`, `corpus check` (absorbs field-detect), `run list events` (absorbs trace); sample-claims folds into eval. |
| `<bundle>/_session/`, `<bundle>/_scratch/`, `<bundle>/_calls.jsonl`, `<bundle>/_run.json`, `<bundle>/_run_history.jsonl`, `<bundle>/_index.{json,md}`, `<bundle>/_wiki_graph.json`, `<bundle>/_wiki_vectors.npz`, `<bundle>/_meta/`, top-level `articles/`, top-level `people/` | legacy bundle artifacts | not deleted on disk — preserved per decision 5; **no longer written** after Phase A | Existing bundles remain readable; `wikify migrate inspect` reports them. |
| `tests/wikify/test_session.py`, `test_cli_kg.py`, `test_cli_meter.py` | tests for retired nouns | C — same PR as the noun they cover | No preserved behaviour. |
| `tests/wikify/test_cli_extract.py`, `test_cli_draft_bundle.py`, `test_cli_validate.py`, `test_cli_io.py`, `test_baseline_skill_e2e.py`, `test_html_render.py`, `test_skill_smoke.py` | mixed legacy/preserved | B — rewritten in the workstream that owns the new noun | Logic preserved; invocations rewritten. |
| `.claude/skills/wikify/{reference,workflows}/` (current flat tree without `SKILL.md`) | skill docs without canonical layout | B — `redesign/b-skills-canonical-layout` | Migrated to the hybrid layout in W9. |
| compatibility adapters left from Phase B (if any) + `wikify migrate` helper sunset | thin shims + one-shot inspector | D — `redesign/d-collapse-adapters` | Removed once skills migrate fully; `migrate` documented as supported one-shot. |

## 3. Final `src/wikify/` package layout

```text
src/wikify/

  corpus/                      # input: source docs → chunks/vectors/figures (read-only during a run)
    __init__.py
    chunks.py                  # was store/corpus.py
    vectors.py, vectors_meta.py
    doc_markdown.py, images_index.py, equations_index.py, bibliography.py
    seed.py                    # was distill/seed.py
    field_detect.py            # was distill/field_detect.py
    graph.py                   # was citestore/graph.py — fluent KG (807 lines, unchanged)
    queries.py                 # NEW (W3): find/show/list/check helpers + select_evidence

  citations/                   # standalone — citation parsing, BibTeX, DOI resolution; consumed by ingest only
    __init__.py
    db.py, resolver.py, bibtex.py, parse.py, models.py    # was citestore/{db,resolver,bibtex,parse,models}.py

  bundle/                      # everything that lives inside one wiki bundle
    __init__.py
    run/                       # execution control: state, events, lock, cost
      __init__.py
      state.py                 # NEW (W2): run/state.json — slim subset of session.py:SessionV1
      events.py                # NEW (W2): run/events.jsonl envelope + append + run/io/
      lock.py                  # NEW (W2): run/lock + claim contention; SessionLockHeldError
      cost.py                  # NEW (W2): TierPrice + _Aggregates + _update_agg from session.py + meter.py
      lifecycle.py             # NEW (W2): init/close orchestration verbs
    concepts/                  # in-flight build-state (operates on the on-disk work/ tree)
      __init__.py
      dossier.py               # was distill/dossier.py (Dossier, Candidate, canonicalize, DossierStore)
      canonicalize.py          # NEW (W4 only): split out canonicalize()+Candidate from dossier.py
      schema.py                # NEW (W4): split from schema.py — extract-side Pydantic
      card.py                  # NEW (W4): work.md ControlCard parser/writer
      evidence.py              # NEW (W4): evidence.jsonl ledger
      inbox.py                 # NEW (W4): work/inbox/*.jsonl append + merge
      claim.py                 # NEW (W4): per-concept claim file + TTL/contention
      tend.py                  # NEW (W4): consolidate inbox, expire claims, regenerate work/index.md
    draft/                     # per-attempt artifacts
      __init__.py
      schema.py                # NEW (W5): split from schema.py — write-side Pydantic
      author_context.py        # was distill/author_context.py
      artifact.py              # NEW (W5): parse/write draft.json + response.json + validation.json
      builder.py               # NEW (W5): DraftBuilder — assemble draft.json from work + evidence
      validator.py             # NEW (W5): Validator — schema + structural + quote-grounding
    wiki/                      # output: committed pages + indices + projections
      __init__.py
      page.py                  # was store/wiki_bundle.py
      page_naming.py           # was store/page_naming.py
      index.py                 # was store/wiki_index.py
      files.py                 # was store/wiki_files.py
      embeddings.py            # was store/bundle_embeddings.py
      graph.py                 # was store/wiki_graph.py — fluent wiki KG
      derived.py               # NEW (W7): derived/index.json, derived/graph.json, derived/vectors.npz
      queries.py               # NEW (W6): list/find/show helpers
      commit.py                # NEW (W6): promote response.json → wiki page; rebuild_projections()

  ingest/                      # corpus building pipeline (already cohesive; 33 files)
  prompts/                     # already cohesive (16 files)
  render/                      # already cohesive (3 files; path resolution updated in W7)
  eval/                        # already cohesive (7 files; trace_replay.py rewired in W8)

  api.py                       # NEW (W1): Bundle + Corpus context dataclasses (replaces paths.py)

  cli/                         # argv glue — calls into the domains above
    __init__.py                # was cli.py — registers nouns
    _io.py                     # was cli_io.py — cli_invoked emission
    _helpers.py                # was cli_cmds/_helpers.py — exit codes, error envelope
    corpus.py, run.py, work.py, draft.py, wiki.py, render.py, eval.py, migrate.py    # NEW per workstream
    legacy/                    # was cli_cmds/ — Phase A keeps legacy CLI working; deleted in Phase C
      __init__.py
      session.py, kg.py, extract.py, draft.py, validate.py, bundle.py, meter.py
```

Top-level files retired in Phase C: `paths.py`, `schema.py`, `session.py`, `meter.py`, `cli.py`, `cli_io.py` (all moved or absorbed by W0/W1). Top-level `baselines/`, `citestore/`, `store/`, `cli_cmds/`, `distill/` directories all disappear (their contents moved by W0; the empty shells deleted in Phase C).

## 4. Final CLI command tree

Top-level nouns: `corpus`, `run`, `work`, `draft`, `wiki`, `render`, `eval`, `migrate`. Verbs: `init`, `show`, `list`, `find`, `add`, `set`, `build`, `check`, `commit`, `tend`, `close`, `claim`, `release`. Defaults to terse text; `--format json` is the automation contract.

Grammar fixes (per brief): `eval --bundle <b>` (no `run` verb); `render --bundle <b> --format html` (format is a flag); `query` noun removed — workflow skill `wikify-query` plus feedback verbs under `work`; `build` always positional (`corpus build <source>`, `wiki build <kind>` where kind ∈ {indexes, graph, vectors}); concurrency verbs `work claim/release` and `work list claims`; exit codes 0/1/2/3/4 (success / validation / lock-or-claim-held / budget-exceeded / stale-claim-broken-by-tend); `run set --corpus` forbidden (corpus swap requires a new bundle).

Full command list:

```text
wikify migrate inspect <bundle>
wikify run init --bundle <b> --corpus <c> [--strategy <s>]
wikify run show [--detail|--full]
wikify run list events [--tail <n>] [--type <t>]
wikify run set [--target-haiku-eq <n>] [--strategy-note <s>]
wikify run lock --owner <id>
wikify run unlock
wikify run close [--status completed|failed|abandoned]
wikify corpus build <source> --out <corpus> [--mode additive|sync] [--parser default|lite|marker|docling]
wikify corpus refresh <corpus>
wikify corpus check [<corpus>]
wikify corpus list [docs|chunks|authors|figures|equations|files] [--corpus <c>]
wikify corpus find "<query>" [--top-k <n>] [--in <handle>] [--text]
wikify corpus find --seed [--max <n>]
wikify corpus find --near <handle> [--top-k <n>] [--depth <n>]
wikify corpus find --cites|--cited-by <doc>
wikify corpus find --neighbors <handle> [--depth <n>]
wikify corpus show <handle> [--detail|--full]
wikify work list [--status <s>] [files|claims|inbox|evidence]
wikify work find "<query>" [--text]
wikify work show <concept> [--detail|--full]
wikify work add concept "<title>" --kind article|person [--aliases <json>]
wikify work add evidence <concept> --records <jsonl-path>
wikify work add feedback <kind> --record <json|jsonl-path>
wikify work set <concept> [--status <s>] [--needs-refine]
wikify work claim <concept> [--ttl <s>] [--owner <id>]
wikify work release <concept>
wikify work tend
wikify draft build <concept> --task create|refine
wikify draft show <concept>
wikify draft check <concept>
wikify wiki list [articles|people|files|evidence]
wikify wiki find "<query>" [--top-k <n>] [--text|--links|--linked-by|--co-evidence|--orphans|--overlaps|--evidence]
wikify wiki show <handle> [--full]
wikify wiki build indexes|graph|vectors
wikify wiki check
wikify wiki commit <concept>
wikify render --bundle <b> --format html [--out <dir>]
wikify eval --bundle <b> [--report <path>]
```

## 5. Final skill set

Hybrid layout: shared `wikify/` mega-skill (user-invocable: false) plus one discoverable directory per atomic and workflow skill. Every `SKILL.md` ≤ 500 lines; references one level deep under `wikify/references/`; no reference loads another reference.

```text
.claude/skills/
  wikify/                              SKILL.md (user-invocable: false)
    references/
      schemas.md, cli-tool-surface.md, write-constraints.md,
      citation-format.md, tiers.md, escalation.md,
      knowledge-graph.md, wiki-graph.md, person-pages.md (NEW), atoms.md
  wikify-baseline/                     SKILL.md (workflow)
  wikify-{guided,free,query,ingest,maintain}/  SKILL.md (workflow stubs)
  wikify-{extract-concepts,gather-evidence,write-page,refine-page,
          consolidate-inbox,answer-from-wiki,tend}/  SKILL.md (atomic, context: fork)
```

Atomic skill specs:

| skill | responsibility | tier | CLI atoms | inputs | outputs |
|---|---|---|---|---|---|
| `wikify-extract-concepts` | scan corpus seeds and emit ExtractResponse → `work add concept` | M (sonnet) | `corpus find --seed`, `corpus show chunk:`, `work add concept` | seed budget, kind filter | `concept_created` events, `work/concepts/<slug>/work.md` |
| `wikify-gather-evidence` | for one claimed concept, retrieve and append evidence | S (haiku) | `corpus find`, `corpus find --near`, `work add evidence`, `work claim/release` | concept slug, top-k | `evidence_added` events, evidence.jsonl |
| `wikify-write-page` | from `draft.json`, produce create-task `response.json` | M; escalates L | `draft show`, writer call | concept slug | `response.json`, `call` events |
| `wikify-refine-page` | refine-task draft → refined `response.json` | M; escalates L | `wiki show --full`, `draft show`, writer call | concept slug | `response.json`, `call` events |
| `wikify-consolidate-inbox` | apply inbox suggestions; mark refine triggers | S (M for merges) | `work list inbox`, `work add evidence`, `work set`, `work tend` | none | inbox truncated, `inbox_consolidated` events |
| `wikify-answer-from-wiki` | answer query from wiki + corpus fallback; emit query_feedback | S (M for syntheses) | `wiki find/show`, `corpus find`, `work add feedback query` | query string | answer text, query_feedback.jsonl |
| `wikify-tend` | deterministic dashboard regen + claim expiry + inbox dedup | none | `work tend`, `wiki build indexes`, `wiki check` | none | dashboards refreshed, `stage_changed` events |

Workflow skill specs:

| skill | loop shape | atomics | parallelism | budget |
|---|---|---|---|---|
| `wikify-baseline` | extract → for each concept (claim → gather → draft → write → check → commit → release) → tend | extract×1, gather×N, write×N, tend×1 | N concurrent gather + write per concept | exit 3 on budget exceeded |
| `wikify-guided` | repeat (read work/index.md → identify gap → extract OR gather → tend) until budget | same atoms; model picks next action | 1–4 concurrent gather forks | same |
| `wikify-free` | guided with broader exploration, no concept budget cap | same atoms | model-decided | same |
| `wikify-query` | answer-from-wiki → consolidate-inbox → optional refine | answer-from-wiki, consolidate-inbox, refine-page | sequential | same |
| `wikify-ingest` | corpus build/refresh wrapper; disable-model-invocation: true | (deterministic CLI only) | 1 | n/a |
| `wikify-maintain` | tend → consolidate-inbox → refine needs_refine candidates | tend, consolidate-inbox, refine-page | sequential | same |

**Composability proof.** Every workflow dispatches only the seven atomic skills. Guided/free/query differ from baseline in (a) ordering and re-entry of `extract-concepts` vs `gather-evidence`, (b) parallelism cap, (c) stopping criterion. No new atom is introduced; both can be added as new workflow `SKILL.md` files referencing the same atomic set.

`person-pages.md` is the one new reference. It absorbs person-page rules currently scattered across `write-constraints.md` and code (banned phrasing, `author_context` integration, graceful degradation). All other references move verbatim.

## 6. Workstreams and dependency DAG

Twelve workstreams, disjoint file ownership.

```text
W0 package-skeleton ──> W1 paths-and-api ──┬──> W2 run ──────────┬──> W4 concepts
                                           │                     │
                                           ├──> W3 corpus ───────┤
                                           │                     │
                                           │                     V
                                           │                     W5 draft ──> W6 wiki ──┬──> W7 render
                                           │                                            │
                                           │                                            └──> W8 eval
                                           │
                                           └──> W9 skills (after W2..W7 stabilise)
                                                          │
                                                          V
                                              W10 legacy-retire (gated by W8 telemetry parity)
                                                          │
                                                          V
                                              W11 collapse-adapters
```

Workstream ownership:

| ws | branch | owns (files) | tests added | acceptance |
|---|---|---|---|---|
| W0 | `redesign/a-package-skeleton` | mechanical `git mv` of preservation-tagged files; legacy-CLI imports rewired to new homes | none added; existing tests must pass | ruff+pytest clean; no behaviour change |
| W1 | `redesign/a-paths-and-api` | `api.py` (Bundle+Corpus dataclasses replacing `paths.py`), `cli/migrate.py`, `tests/test_bundle_resolution.py`, `test_migrate_inspect.py` | bundle resolution from CWD; migrate inspect on legacy fixture | new path attrs correct; legacy `paths.py` is a thin shim |
| W2 | `redesign/a-run` | `bundle/run/{state,events,lock,cost,lifecycle}.py`, `cli/run.py`, `cli/_io.py`, `tests/test_events_schema.py`, `test_run_store.py`, `test_cli_run.py`, `test_cli_io_emits_events.py` | events envelope; RunStateV1; lock; cost from events | `run init` → state+events; lock lifecycle |
| W3 | `redesign/a-corpus` | `corpus/queries.py`, `cli/corpus.py`, `tests/test_corpus_queries.py`, `test_cli_corpus.py` | wraps `corpus/graph.py` + `ingest/`; CLI list/find/show/check/build/refresh | `corpus find` columns `score id doc preview`; build/refresh round-trip |
| W4 | `redesign/a-work` | `bundle/work/{schema,canonicalize,card,evidence,inbox,claim,tend}.py`, `cli/work.py`, `tests/test_work_*.py`, `test_work_claim_atomic.py`, `test_work_inbox_append.py` | concept lifecycle on disk; claim/release; deterministic tend; W4 splits canonicalize out of dossier.py | claim TTL enforced; release exit 2 for non-owner; inbox-per-writer survives parallel writes |
| W5 | `redesign/a-draft` | `bundle/draft/{schema,artifact,builder,validator}.py`, `cli/draft.py`, `tests/test_draft_builder.py`, `test_validator.py`, `test_cli_draft.py` | DraftBuilder + Validator; draft.json from work+evidence | `draft check` exit 0 grounded; exit 1 with `QuoteNotInChunkError` |
| W6 | `redesign/a-wiki` | `bundle/wiki/{queries,commit,derived}.py`, `cli/wiki.py`, `tests/test_wiki_commit.py`, `test_cli_wiki.py` | commit gate; build indexes/graph/vectors from existing `bundle/wiki/graph.py` helpers | `wiki commit` blocks ungrounded; emits `page_committed` |
| W7 | `redesign/a-render` | `cli/render.py`, render path rewiring in `render/html/render.py`, `tests/test_render_paths.py` | render reads `wiki/`+`derived/` | renders fixture; output matches snapshot |
| W8 | `redesign/a-eval` | `cli/eval.py`, `eval/trace_replay.py` rewire, `tests/test_eval_from_events.py`, `test_telemetry_parity.py` | eval reads `events.jsonl` | M1/M3/M5/M6 within tolerance vs legacy |
| W9 | `redesign/b-skills-canonical-layout` | `.claude/skills/wikify/SKILL.md`+`references/*`, `wikify-baseline/SKILL.md`, 7 atomic SKILL.md, 5 stub workflow SKILL.md, `tests/test_skill_layout.py` | hybrid layout; references one level deep | every SKILL.md ≤500 lines; layout test passes |
| W10 | `redesign/c-*` (5 PRs in §7) | deletes `cli/legacy/*`, `session.py`/`meter.py` shells, `paths.py` shell, `baselines/`, `citations/__main__.py`, legacy tests | legacy tests deleted | ruff+pytest clean; legacy nouns deregistered |
| W11 | `redesign/d-collapse-adapters` | residual shim removal, `migrate inspect` doc, doc rewrites for `architecture.md`, `AGENTS.md`, `filesystem-state-design.md`, `skill-centric-execution-plan.md` | none | adapters gone; helpers documented |

## 7. Phased legacy-removal plan

**Phase A — additive (W0 then W1–W8 in DAG order).**

- `redesign/a-package-skeleton` — mechanical rename only; no new functionality. Imports across legacy CLI are updated to point at new package homes. Existing tests must pass unchanged.
- `redesign/a-paths-and-api` through `redesign/a-eval` — each workstream lands new domain modules in their final package home. Skills still call legacy CLI (legacy handlers continue to work because W0 moved them to `cli/legacy/`).

**Phase B — skills + parity gate.** PRs `redesign/b-skills-canonical-layout` and `redesign/b-telemetry-parity-gate`. Skill layout migrates; baseline workflow rewritten against new CLI. M1/M3/M5/M6 plus cost aggregates from `events.jsonl` must match legacy `_calls.jsonl` + `_run.json` on a fixture baseline.

**Phase C — legacy retire (five surgical deletion PRs).**

1. `redesign/c-cli-retire-session-kg-meter` — delete `cli/legacy/{session,kg,meter}.py`, `tests/wikify/{test_session,test_cli_kg,test_cli_meter}.py`, deregister sub-apps in `cli/__init__.py`. Cost emission now from `RunStore.append_call()`.
2. `redesign/c-cli-retire-extract-draft-validate-bundle` — delete `cli/legacy/{extract,draft,validate,bundle}.py` and the corresponding test files (rewrites already landed in W4/W5/W6). Deregister sub-apps.
3. `redesign/c-store-retire-session-meter` — delete `src/wikify/session.py` and `src/wikify/meter.py` shells (cost math and lock-held already moved to `run/cost.py`/`run/lock.py` by W0/W2).
4. `redesign/c-baselines-debug-retire` — delete `src/wikify/baselines/` and `src/wikify/citations/__main__.py`. `BaselineConfig` strategy moved to skill frontmatter; the evidence helper was relocated to `corpus/queries.py` in W3. `distill/` is already gone post-W0; `bundle/draft/preload.py` and `bundle/wiki/post_commit.py` are absorbed by `bundle/draft/builder.py` and `bundle/wiki/commit.py` in W5/W6.
5. `redesign/c-cli-prune-toplevel-and-paths` — delete `cli/legacy/` entirely (empty after PRs 1–2); delete legacy top-level commands (`trace`, `sample-claims`, `html`, `field-detect`); delete `paths.py` shell now that all callers use `api.Bundle`.

**Phase D — collapse adapters.** PR `redesign/d-collapse-adapters` removes residual shims; documents `migrate inspect`; ships doc rewrites for `architecture.md`, `AGENTS.md`, `filesystem-state-design.md`, `skill-centric-execution-plan.md`.

## 8. End-to-end MVP paths

| MVP | path |
|---|---|
| ingest | `wikify-ingest` skill → `wikify corpus build <source> --out <corpus>` → `corpus refresh` → `corpus check` |
| baseline | `wikify run init --bundle <b> --corpus <c> --strategy baseline` → `wikify-baseline` loop: `corpus find --seed` → forked `wikify-extract-concepts` → for each concept fork `wikify-gather-evidence` (`work claim`/`work add evidence`/`work release`) and `wikify-write-page` (`draft build`/writer/`draft check`/`wiki commit`) → `wikify-tend` → `wikify run close --status completed` |
| query | `wikify-query` skill: `wiki find` / `wiki show` / `corpus find` (fallback) → answer → `work add feedback query` → `wikify-consolidate-inbox` → optional `wikify-refine-page` → `wiki commit` |
| render+eval | `wikify render --bundle <b> --format html --out <dir>` and `wikify eval --bundle <b> --report <path>` |

## 9. First three PRs

PR 1 — `redesign/a-package-skeleton` (W0). Mechanical rename only.

| concern | detail |
|---|---|
| moves | `citestore/graph.py → corpus/graph.py`; `citestore/{db,resolver,bibtex,parse,models,__main__}.py → citations/`; `store/{wiki_bundle→bundle/wiki/page, wiki_index→bundle/wiki/index, wiki_files→bundle/wiki/files, wiki_graph→bundle/wiki/graph, bundle_embeddings→bundle/wiki/embeddings, page_naming→bundle/wiki/page_naming}`; `store/{corpus→corpus/chunks, vectors, vectors_meta, doc_markdown, images_index, equations_index, bibliography} → corpus/`; `distill/{dossier→bundle/work/dossier, author_context→bundle/draft/author_context, seed→corpus/seed, field_detect→corpus/field_detect, preload→bundle/draft/preload, write_runner→bundle/wiki/post_commit}`; `distill/` deleted entirely; `cli.py → cli/__init__.py`; `cli_io.py → cli/_io.py`; `cli_cmds/* → cli/legacy/*`. W0 does NOT split `canonicalize()` out of dossier.py — that happens in W4. Leaves in place: `paths.py`, `schema.py`, `session.py`, `meter.py`, `baselines/`, `ingest/`, `prompts/`, `render/`, `eval/`. |
| import sweeps | `wikify.citestore.*`, `wikify.store.*`, `wikify.distill.*`, `wikify.cli_io`, `wikify.cli_cmds.*` rewritten to new homes. |
| scope | no logic changes; no new modules. |
| verification | ruff + pytest clean; legacy CLI smoke: `wikify session init`, `wikify kg seeds`, `wikify ingest` unchanged. |

PR 2 — `redesign/a-paths-and-api` (W1). Depends on PR 1.

| concern | detail |
|---|---|
| files added | `api.py` (Bundle + Corpus dataclasses); `cli/migrate.py`; `tests/test_bundle_resolution.py`, `test_migrate_inspect.py`, `fixtures/legacy_bundle/`. |
| files modified | `paths.py` becomes a shim deferring to `api.Bundle`/`api.Corpus`; `cli/__init__.py` registers `migrate`; `cli/_helpers.py` surfaces exit codes 3 and 4. |
| verification | ruff + pytest clean; `uv run wikify migrate inspect tests/wikify/fixtures/legacy_bundle`. |

PR 3 — `redesign/a-run` (W2). Depends on PR 2.

| concern | detail |
|---|---|
| files added | `run/{state,events,lock,cost,lifecycle}.py` (lock imports `SessionLockHeldError` from `session.py`; cost imports `_Aggregates`/`_update_agg`/`TierPrice` from `session.py`+`meter.py` — both until Phase C); `cli/run.py`; `tests/test_events_schema.py`, `test_run_store.py`, `test_cli_run.py`, `test_cli_io_emits_events.py`. |
| files modified | `cli/__init__.py` registers `run`; `cli/_io.py` emits `cli_invoked` events and writes large IO to `run/io/<event_id>.{stdout,stderr}.txt`. |
| scope | does not touch `session.py` / `meter.py` shells. |
| verification | ruff + pytest clean; `wikify run init --bundle /tmp/r1 --corpus tests/wikify/fixtures/mini_corpus --strategy baseline`; `run show`; `run list events --tail 5`; `run close --status completed`. |

## 10. Doc rewrites in scope

| doc | phase | scope |
|---|---|---|
| `docs/architecture.md` | D | rewrite for noun-verb surface, package-per-noun layout, events.jsonl telemetry, hybrid skill layout. Drop legacy sections. |
| `AGENTS.md` | D | rewrite Read First; remove legacy CLI table. |
| `docs/filesystem-state-design.md` | D | promote from "design target" to "implementation reference"; remove disclaimer. Decide whether to rename on-disk `work/` → `concepts/` for full alignment with the package name. |
| `docs/skill-centric-execution-plan.md` | D | archive to `docs/history/`. |
| `.claude/skills/wikify/references/cli-tool-surface.md` | A (each PR) | extended incrementally as each noun ships. |
| `.claude/skills/wikify/references/schemas.md` | A (each PR) | extended incrementally — events envelope (W2), work.md/evidence.jsonl/inbox (W4), draft/response/validation (W5), wiki + derived (W6/W7). |
| `tasks/lessons.md` | continuous | append after every correction per CLAUDE.md format. |
