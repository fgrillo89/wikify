# Skill-centric Wikify redesign — implementation plan

This plan satisfies the binding contract in `docs/skill-centric-execution-plan.md`. It enumerates the legacy surface, fixes a preservation inventory for working logic, names the final package layout, the final CLI command tree, the canonical Claude skill set, eleven disjoint workstreams, a four-phase legacy-removal sequence, end-to-end MVP paths, and the first three PRs in execution order. Architectural decisions in the brief (skills own strategy, CLI is the agent's bundle interface, query is a skill, legacy removal is named, on-disk legacy data is preserved, parallelism is explicit, deterministic-only tests, reuse over rewrite) are not relitigated.

The plan favours the existing `src/wikify/store/` directory (singular). It does not introduce a sibling `stores/` package. Mid-run corpus change is forbidden — `wikify run set --corpus` is removed; a corpus swap requires a new bundle.

## 1. Preservation inventory

Logic that must survive the redesign. Tags: KEEP (move only), REFACTOR (signature/IO change), REPLACE (rewrite — must justify why reuse fails).

| path | role | tag | new home | rationale |
|---|---|---|---|---|
| `src/wikify/prompts/` (16 files) | writer / refine / extract / person / field guides / artifact templates | KEEP | `src/wikify/prompts/` (unchanged); CLI `wikify draft show <concept>` exposes assembled prompts | Templates are strategy-agnostic; loaded by `DraftBuilder` and skill assets. |
| `src/wikify/distill/dossier.py` | dossier compilation | KEEP | `store/draft.py::DraftBuilder._compile_dossier()` | Pure data transform; consumed only by draft. |
| `src/wikify/distill/author_context.py` | author-context for person pages | KEEP | `store/draft.py::DraftBuilder._author_context()` | Deterministic; lives next to draft assembly. |
| `src/wikify/distill/write_runner.rebuild_wiki_graph` | post-commit wiki graph + vectors rebuild | KEEP | `store/wiki.py::WikiStore.rebuild_projections()` | Called by `wiki commit` and `wiki build graph`. |
| `src/wikify/distill/preload.py` | evidence pre-loading | REFACTOR | folded into `DraftBuilder.build()` reading from `work/concepts/<slug>/evidence.jsonl` | Caller surface changes; logic preserved. |
| `src/wikify/distill/seed.py` | greedy seed selection | KEEP | `store/corpus_store.py::CorpusStore.seeds()` | Surfaced via `corpus find --seed`. |
| `src/wikify/distill/field_detect.py` | field classification | KEEP | unchanged | Called from `corpus check`. |
| `src/wikify/distill/canonicalize.py` | concept canonicalisation | KEEP | `store/work.py::WorkStore.add_concept()` + `consolidate_inbox()` | Surfaces via `work add concept`, `work tend`. |
| `src/wikify/schema.py` (`WriteRequest`, `WriteResponse`, `ExtractRequest`, `ExtractResponse`, `QuoteNotInChunkError`, `_check_wikipedia_structure`, `_check_figure_mentions`, `_body_has_prose_and_evidence`) | Pydantic write contracts + structural checks | KEEP | unchanged | Load-bearing contract; no rewrite. `Validator` wraps it. |
| `src/wikify/baselines/config.py::BaselineConfig` | baseline knobs | REPLACE | per-workflow-skill frontmatter (`wikify-baseline/SKILL.md`) | Strategy belongs in skills, not Python. Knobs that survive (e.g. evidence top-k) move to skill arguments; the rest disappear. |
| `src/wikify/baselines/_evidence.select_evidence_chunks_for_page` | per-page evidence helper | KEEP | `src/wikify/store/corpus_store.py::CorpusStore.select_evidence()` | Pure ranking; corpus-side, not strategy-side. |
| `src/wikify/citestore/` (9 files: `graph.py`, `graph_build.py`, `db.py`, `resolver.py`, `bibtex.py`, `parse.py`, `models.py`, `__init__.py`) | corpus fluent KG | KEEP | unchanged | Surfaced through `corpus find/show/list`. `__main__.py` deleted. |
| `src/wikify/store/wiki_graph.py` | wiki fluent KG | KEEP | unchanged; wrapped by `WikiStore` | Surfaced through `wiki find`. |
| `src/wikify/store/{wiki_bundle,wiki_index,wiki_files,bundle_embeddings}.py` | wiki page IO + index + embeddings | REFACTOR | unified under `store/wiki.py::WikiStore` reading `wiki/articles/`, `wiki/people/`, writing `derived/` projections | Path surfaces change; logic preserved. |
| `src/wikify/store/{page_naming,vectors,vectors_meta,doc_markdown,images_index,equations_index,corpus,bibliography}.py` | corpus chunk/vector/figure stores | KEEP | unchanged | Read by ingest, eval, `CorpusStore`. |
| `src/wikify/eval/{metrics,community,audit,stats,claim_sampler}.py` | M1/M3/M5/M6 metric math | KEEP | unchanged | `trace_replay.py` rewires input path in W8. |
| `src/wikify/eval/trace_replay.py` | event log replay | REFACTOR | reads `run/events.jsonl` instead of `_meta/kg_trace.jsonl` | Input path changes; replay logic invariant. |
| `src/wikify/render/html/` (3 files) | Jinja2 site renderer | REFACTOR | reads `wiki/` and `derived/` | Templates unchanged; path resolution changes. |
| `src/wikify/ingest/` (33 files) | parse / chunk / embed / graph / topics / boilerplate / abstract tagger | KEEP | unchanged | Surfaced through `corpus build`/`corpus refresh`. |
| `src/wikify/cli_cmds/_helpers.py` | shared error/lock helpers | KEEP | extended to handle exit codes 3 (budget) and 4 (stale-claim broken) | Stable shared contract. |
| `src/wikify/cli_io.py` | CLI IO capture wrapper around Typer | REFACTOR | emits `cli_invoked` events into `run/events.jsonl` and writes large IO to `run/io/<event_id>.{stdout,stderr}.txt` | Capture exists; eventing is new. |
| `.claude/skills/wikify/reference/{schemas,cli-tool-surface,write-constraints,citation-format,tiers,escalation,knowledge-graph,wiki-graph,atoms}.md` (9 files) | shared references | KEEP | `.claude/skills/wikify/references/<name>.md` (under new shared mega-skill `SKILL.md`) | `schemas.md` and `cli-tool-surface.md` get content updates in W9; the rest move verbatim. |
| `.claude/skills/wikify/workflows/run-baseline.md` | baseline workflow doc | REFACTOR | rewritten as `.claude/skills/wikify-baseline/SKILL.md` (≤ 500 lines) | Frontmatter introduced; body trimmed. |
| `tasks/lessons.md`, `CLAUDE.md` corrections | tribal knowledge | KEEP | unchanged | Project memory is cumulative. |

REPLACE is limited to `BaselineConfig` (strategy belongs in skills), `citestore/__main__.py` (CLI replaces debug entry points), and module aggregators (`distill/__init__.py`, `baselines/__init__.py`) that become unnecessary once functions move into specific store classes.

## 2. Legacy enumeration and phase tags

Phase A introduces new layout alongside legacy. Phase B migrates skills/render/eval and runs the telemetry parity gate. Phase C deletes legacy CLI nouns, paths, and tests. Phase D collapses adapters and migration helpers.

| path | role | phase deleted in PR | rationale |
|---|---|---|---|
| `src/wikify/cli_cmds/session.py` | legacy `session init/show/update/close/checkpoint/lock/unlock` | C — `redesign/c-cli-retire-session-kg-meter` | Replaced by `wikify run` + `wikify work claim/release`. |
| `src/wikify/cli_cmds/kg.py` | legacy `kg seeds/abstracts/evidence` | C — same PR | Replaced by `wikify corpus find`. |
| `src/wikify/cli_cmds/extract.py` | legacy `extract canonicalize` | C — `redesign/c-cli-retire-extract-draft-validate-bundle` | Replaced by `wikify work add concept`. |
| `src/wikify/cli_cmds/draft.py` | legacy `draft write-request` | C — same PR | Replaced by `wikify draft build` (new). |
| `src/wikify/cli_cmds/validate.py` | legacy `validate write` (358 lines) | C — same PR | Replaced by `wikify draft check` and `wiki commit` gates. |
| `src/wikify/cli_cmds/bundle.py` | legacy `bundle commit-page` (307 lines) | C — same PR | Replaced by `wikify wiki commit`. |
| `src/wikify/cli_cmds/meter.py` | legacy `meter record` | C — first deletion PR (with session/kg) | Replaced by `events.jsonl` envelope; cost is computed by filtering `type == "call"`. |
| `src/wikify/session.py` (`SessionV1`, `session_lock()`, `write_run_snapshot()`) | session schema + lock + final snapshot | C — `redesign/c-store-retire-session-meter` | Replaced by `RunStore` (run/state.json + run/lock + events.jsonl). |
| `src/wikify/meter.py` (`CallRecord`, `CostMeter.snapshot()`) | per-call telemetry + reference aggregator | C — same PR as `session.py` | Cost is computed on demand from `events.jsonl`. |
| `src/wikify/baselines/__init__.py`, `baselines/config.py`, `baselines/_evidence.py` (rest of dir) | strategy config | C — `redesign/c-baselines-distill-citestore-debug-retire` | `BaselineConfig` lives in skill frontmatter; helpers move to `CorpusStore`. |
| `src/wikify/distill/__init__.py`, `distill/preload.py`, `distill/write_runner.py` | aggregator + pre-load + post-commit graph rebuild | C — same PR | Logic moves into `store/draft.py`, `store/wiki.py`. Other distill files (`dossier.py`, `author_context.py`, `seed.py`, `field_detect.py`, `canonicalize.py`) are kept and folded into store modules. |
| `src/wikify/citestore/__main__.py` | ad-hoc debug entry point | C — same PR | Replaced by `wikify corpus show`. |
| `src/wikify/paths.py` legacy accessors (`session_dir`, `session_path`, `session_lock_path`, `scratch_dir`, `calls_path`, `run_history_path`, `articles_dir`, `people_dir`, `meta_dir`, `_index_*`, `_wiki_graph.json`, `_wiki_vectors.npz`) | legacy bundle paths | C — `redesign/c-cli-prune-toplevel-and-paths` | Replaced by `run_*`, `work_*`, `wiki_*`, `derived_*` accessors landed in W1. |
| `<bundle>/_session/`, `<bundle>/_scratch/`, `<bundle>/_calls.jsonl`, `<bundle>/_run.json`, `<bundle>/_run_history.jsonl`, `<bundle>/_index.{json,md}`, `<bundle>/_wiki_graph.json`, `<bundle>/_wiki_vectors.npz`, `<bundle>/_meta/`, top-level `articles/`, top-level `people/` | legacy bundle artifacts | not deleted on disk — preserved per decision 5; **no longer written** after Phase A | Existing bundles remain readable; `wikify migrate inspect` reports them. |
| `tests/wikify/test_session.py`, `test_cli_kg.py`, `test_cli_meter.py` | tests for retired nouns | C — same PR as the noun they cover | No preserved behaviour. |
| `tests/wikify/test_cli_extract.py`, `test_cli_draft_bundle.py`, `test_cli_validate.py`, `test_cli_io.py`, `test_baseline_skill_e2e.py`, `test_html_render.py`, `test_skill_smoke.py` | mixed legacy/preserved | B — rewritten in the workstream that owns the new noun | Logic preserved; invocations rewritten. |
| `.claude/skills/wikify/{reference,workflows}/` (current flat tree without `SKILL.md`) | skill docs without canonical layout | B — `redesign/b-skills-canonical-layout` | Migrated to the hybrid layout in W9. |
| `src/wikify/cli.py` legacy `app.add_typer(... session/kg/extract/draft/validate/bundle/meter)` registrations + flat `trace`/`sample-claims`/`html`/`field-detect` commands | legacy registrations | C — `redesign/c-cli-prune-toplevel-and-paths` | Top-level becomes the new noun-verb tree only. |
| compatibility adapters in `cli_cmds/{session,kg,extract,draft,validate,bundle,meter}.py` (Phase B thin wrappers if any) | thin shims | D — `redesign/d-collapse-adapters` | Removed once skills migrate fully. |
| `wikify migrate` helper | one-shot read-only inspector | D — same PR | Documented utility after legacy retire is complete. |

## 3. Final `src/wikify/` package layout

```text
src/wikify/
  __init__.py
  api.py                     # NEW: Bundle.open() fluent entry
  cli.py                     # noun-verb registrations only after Phase C
  cli_io.py                  # emits cli_invoked events (W2)
  config.py, context.py, embedding.py, models.py, schema.py, types.py  # KEEP
  paths.py                   # extended in W1; legacy accessors removed in Phase C
  events.py                  # NEW (W2): Pydantic envelope for run/events.jsonl
  cli_cmds/
    __init__.py, _helpers.py
    corpus.py    # NEW W3
    run.py       # NEW W2
    work.py      # NEW W4
    draft.py     # NEW W5 (replaces legacy draft.py)
    wiki.py      # NEW W6 (replaces bundle.py)
    render.py    # NEW W7
    eval.py      # NEW W8 (replaces top-level eval in cli.py)
    migrate.py   # NEW W1 (read-only inspector)
  store/
    __init__.py
    run.py            # NEW W2: RunStore over run/state.json + events.jsonl + lock
    corpus_store.py   # NEW W3: CorpusStore wrapping citestore + ingest
    work.py           # NEW W4: WorkStore over work/index.md + concepts + inbox
    draft.py          # NEW W5: DraftBuilder + Validator
    wiki.py           # NEW W6: WikiStore unifying wiki_bundle/wiki_index/wiki_graph/wiki_files
    derived.py        # NEW W7: DerivedStore (rebuildable projections)
    page_naming.py, wiki_graph.py, wiki_index.py, wiki_bundle.py, wiki_files.py,
    bundle_embeddings.py, vectors.py, vectors_meta.py, doc_markdown.py,
    images_index.py, equations_index.py, corpus.py, bibliography.py    # KEEP
  ingest/        # KEEP (33 files)
  citestore/     # KEEP (8 files; __main__.py deleted in Phase C)
  distill/       # KEEP dossier/author_context/seed/field_detect/canonicalize
                 # DELETED Phase C: __init__.py, preload.py, write_runner.py
  prompts/       # KEEP (16 files)
  eval/          # KEEP (7 files; trace_replay.py rewired in W8)
  render/        # KEEP (3 files; path resolution updated in W7)
  baselines/     # DELETED Phase C; helpers absorbed by CorpusStore
  session.py, meter.py   # DELETED Phase C
```

`api.py` exposes `Bundle.open(path)` returning facets that mirror the CLI nouns (`bundle.run`, `bundle.corpus`, `bundle.work`, `bundle.draft`, `bundle.wiki`). Tests call this fluent API rather than spawning subprocesses.

## 4. Final CLI command tree

Top-level nouns: `corpus`, `run`, `work`, `draft`, `wiki`, `render`, `eval`, `migrate`. Verbs: `init`, `show`, `list`, `find`, `add`, `set`, `build`, `check`, `commit`, `tend`, `close`, `claim`, `release`. Defaults to terse text; `--format json` is the automation contract.

Grammar fixes:

- `eval --bundle <b>` (no `run` verb); corpus resolved from bundle's `run/state.json`.
- `render --bundle <b> --format html` (format is a flag, not a sub-noun).
- `query` noun removed entirely — query is a workflow skill (`wikify-query`); feedback verbs live under `work`.
- `build` always takes a positional naming what to build: `corpus build <source>` (source dir), `wiki build <kind>` where kind ∈ {indexes, graph, vectors}.
- Concurrency: `work claim <concept> [--ttl <s>] [--owner <id>]`, `work release <concept>`, `work list claims`. Exit codes: 0 success, 1 validation/precondition, 2 lock/claim held, 3 budget exceeded, 4 stale claim broken by `work tend`.
- `run set --corpus` is forbidden — mid-run corpus change requires a new bundle. Other `run set` fields (strategy note, budget target) remain.

Full command list (verbs in canonical order):

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

Hybrid layout: one shared `wikify/` mega-skill (user-invocable: false) plus one discoverable directory per atomic and per workflow skill. Every directory has a `SKILL.md` with frontmatter and a body ≤ 500 lines. References sit one level deep under `wikify/references/`; no reference loads another reference.

```text
.claude/skills/
  wikify/                              SKILL.md (user-invocable: false)
    references/
      schemas.md, cli-tool-surface.md, write-constraints.md,
      citation-format.md, tiers.md, escalation.md,
      knowledge-graph.md, wiki-graph.md, person-pages.md (NEW), atoms.md
  wikify-baseline/                     SKILL.md (workflow)
  wikify-guided/                       SKILL.md (workflow stub)
  wikify-free/                         SKILL.md (workflow stub)
  wikify-query/                        SKILL.md (workflow stub; M2 focus)
  wikify-ingest/                       SKILL.md (workflow; disable-model-invocation: true)
  wikify-maintain/                     SKILL.md (workflow)
  wikify-extract-concepts/             SKILL.md (atomic, context: fork)
  wikify-gather-evidence/              SKILL.md (atomic, context: fork)
  wikify-write-page/                   SKILL.md (atomic, context: fork)
  wikify-refine-page/                  SKILL.md (atomic, context: fork)
  wikify-consolidate-inbox/            SKILL.md (atomic, context: fork)
  wikify-answer-from-wiki/             SKILL.md (atomic, context: fork)
  wikify-tend/                         SKILL.md (atomic, deterministic CLI dispatch)
```

Atomic skill specs (single-line responsibility | tier (model) | CLI atoms invoked | inputs | outputs):

| skill | responsibility | tier | CLI atoms | inputs | outputs |
|---|---|---|---|---|---|
| `wikify-extract-concepts` | scan corpus seeds and emit `ExtractResponse` records that become `work add concept` calls | M (sonnet) | `corpus find --seed`, `corpus show chunk:`, `work add concept` | `$ARGUMENTS` (seed budget, kind filter) | `events: concept_created`, `work/concepts/<slug>/work.md` |
| `wikify-gather-evidence` | for one claimed concept, retrieve and append evidence to its ledger | S (haiku) | `corpus find "<concept>"`, `corpus find --near`, `work add evidence`, `work claim`, `work release` | `$ARGUMENTS` (concept slug, top-k) | `work/concepts/<slug>/evidence.jsonl`, `events: evidence_added` |
| `wikify-write-page` | from a fresh `draft.json`, produce `response.json` for one create-task page | M (sonnet); escalates to L | `draft show`, `wiki commit` (only after writer returns) | `$ARGUMENTS` (concept slug) | `work/concepts/<slug>/response.json`, `events: call` |
| `wikify-refine-page` | from a refine-task draft, produce a refined `response.json` | M (sonnet); escalates to L | `wiki show <page> --full`, `draft show`, writer call | `$ARGUMENTS` (concept slug) | `work/concepts/<slug>/response.json`, `events: call` |
| `wikify-consolidate-inbox` | apply `work/inbox/*.jsonl` suggestions to concepts and mark refine triggers | S (haiku) for triage; M for merges | `work list inbox`, `work add evidence`, `work set`, `work tend` | none | inbox truncated, `events: inbox_consolidated` |
| `wikify-answer-from-wiki` | answer a user query from the wiki, fall back to corpus, emit `query_feedback` | S (haiku) for short answers; M for syntheses | `wiki find`, `wiki show`, `corpus find`, `work add feedback query` | `$ARGUMENTS` (query string) | answer text, `work/inbox/query_feedback.jsonl` |
| `wikify-tend` | deterministic dashboard regen + claim expiry + inbox dedup | none (no model call) | `work tend`, `wiki build indexes`, `wiki check` | none | dashboards refreshed, `events: stage_changed` |

Workflow skill specs:

| skill | loop shape | atomics dispatched | parallelism | budget signal |
|---|---|---|---|---|
| `wikify-baseline` | extract → for each concept (claim → gather → draft → write → check → commit → release) → tend | extract-concepts × 1, gather-evidence × N (parallel), write-page × N (parallel), tend × 1 | up to N concurrent forks of gather + write per concept; one extract; one tend | `target_haiku_eq` from `run/state.json`; budget exceeded raises exit 3 |
| `wikify-guided` | repeat (read work/index.md → identify gap → extract-concepts OR gather-evidence → tend) until budget exhausted | same atoms, model picks next action each turn | 1–4 concurrent gather forks | same |
| `wikify-free` | guided but with broader exploration and no concept budget cap | same atoms | model-decided | same |
| `wikify-query` | for each user question: answer-from-wiki → consolidate-inbox → optional refine | answer-from-wiki, consolidate-inbox, refine-page | sequential | same |
| `wikify-ingest` | corpus build/refresh wrapper; disable-model-invocation: true | (deterministic CLI only) | 1 | n/a |
| `wikify-maintain` | tend → consolidate-inbox → refine candidates with needs_refine | tend, consolidate-inbox, refine-page | sequential | same |

**Composability proof.** Every workflow above dispatches only the seven atomic skills. Differences between baseline, guided, and free reduce to (a) ordering and re-entry pattern of `extract-concepts` vs `gather-evidence`, (b) parallelism cap, and (c) stopping criterion. No new atomic skill is introduced for guided or free; both can be added as new workflow `SKILL.md` files referencing the same atomic set.

`person-pages.md` is the one new reference file. It absorbs the person-page rules currently scattered across `write-constraints.md` and code (banned phrasing "appears in this corpus", `author_context` integration, graceful degradation). All other references move verbatim.

## 6. Workstreams and dependency DAG

Eleven workstreams, disjoint file ownership. Cross-cutting concerns (paths, telemetry envelope, `_helpers.py`) are owned by W1 and extended only inside that workstream's PRs.

```text
W1 paths-and-layout ──┬──> W2 run-and-telemetry ─────────┬──> W4 work
                      │                                  │
                      ├──> W3 corpus ────────────────────┤
                      │                                  │
                      │                                  V
                      │                                  W5 draft ──> W6 wiki ──┬──> W7 render-and-derived
                      │                                                         │
                      │                                                         └──> W8 eval
                      │
                      └──> W9 skills (depends on W2..W7 surface stabilising)
                                                                  │
                                                                  V
                                                W10 legacy-retire (gated by W8 telemetry parity)
                                                                  │
                                                                  V
                                                              W11 collapse-adapters
```

Workstream ownership table:

| ws | branch | owns (files) | tests added | acceptance |
|---|---|---|---|---|
| W1 | `redesign/a-paths` | `paths.py` additive accessors, `cli_cmds/migrate.py`, `tests/test_paths_layout.py`, `tests/test_migrate_inspect.py` | layout-version detection; migrate inspect on legacy fixture | new accessors correct; legacy accessors unchanged; inspect tallies legacy artifacts |
| W2 | `redesign/a-run` | `events.py`, `store/run.py`, `cli_cmds/run.py`, `cli_io.py`, `tests/test_events_schema.py`, `test_run_store.py`, `test_cli_run.py` | events envelope; RunStore lifecycle; cli_invoked emission | `run init`→`state.json`+`events.jsonl`; lock lifecycle |
| W3 | `redesign/a-corpus` | `store/corpus_store.py`, `cli_cmds/corpus.py`, `tests/test_corpus_store.py`, `test_cli_corpus.py` | wraps citestore+ingest; list/find/show/check/build/refresh | `corpus find` columns `score id doc preview`; build/refresh round-trip |
| W4 | `redesign/a-work` | `store/work.py`, `cli_cmds/work.py`, `tests/test_work_store.py`, `test_cli_work.py`, `test_work_claim_atomic.py`, `test_work_inbox_append.py` | WorkStore + claim/release + tend | claim TTL enforced; release exit 2 for non-owner; inbox-per-writer survives parallel writes |
| W5 | `redesign/a-draft` | `store/draft.py`, `cli_cmds/draft.py`, `tests/test_draft_builder.py`, `test_validator.py`, `test_cli_draft.py` | DraftBuilder + Validator; draft.json from work+evidence | `draft check` exit 0 grounded; exit 1 with `QuoteNotInChunkError` |
| W6 | `redesign/a-wiki` | `store/wiki.py`, `cli_cmds/wiki.py`, `tests/test_wiki_store.py`, `test_cli_wiki.py` | unified WikiStore; commit gate; build indexes/graph/vectors | `wiki commit` blocks ungrounded; emits `page_committed` |
| W7 | `redesign/a-render` | `store/derived.py`, `cli_cmds/render.py`, `render/html/render.py` rewiring, `tests/test_render_paths.py` | render reads `wiki/`+`derived/` | renders fixture; output matches snapshot |
| W8 | `redesign/a-eval` | `cli_cmds/eval.py`, `eval/trace_replay.py`, `tests/test_eval_from_events.py`, `test_telemetry_parity.py` | eval reads `events.jsonl` | M1/M3/M5/M6 within tolerance vs legacy |
| W9 | `redesign/b-skills-canonical-layout` | `.claude/skills/wikify/SKILL.md`+`references/*`, `wikify-baseline/SKILL.md`, 7 atomic SKILL.md, 5 stub workflow SKILL.md, `tests/test_skill_layout.py` | hybrid layout; references one level deep | every SKILL.md ≤500 lines; layout test passes |
| W10 | `redesign/c-*` (5 PRs in §7) | deletes legacy `cli_cmds/*`, `session.py`, `meter.py`, `baselines/`, `distill/{__init__,preload,write_runner}.py`, `citestore/__main__.py`, legacy paths, legacy tests | legacy tests deleted | ruff+pytest clean; legacy nouns deregistered |
| W11 | `redesign/d-collapse-adapters` | residual shim removal, `migrate inspect` doc | none | adapters gone; helper documented |

## 7. Phased legacy-removal plan

Each phase is named with concrete deletion PRs.

**Phase A — additive only (W1–W8 in DAG order).** New layout and CLI nouns ship alongside legacy; no deletions. PRs: `redesign/a-paths`, `redesign/a-run`, `redesign/a-corpus`, `redesign/a-work`, `redesign/a-draft`, `redesign/a-wiki`, `redesign/a-render`, `redesign/a-eval`.

**Phase B — skills + parity gate (W9).** Hybrid skill layout lands; baseline workflow rewritten against new CLI. Telemetry parity gate: M1/M3/M5/M6 plus cost aggregates from `events.jsonl` must match legacy `_calls.jsonl` + `_run.json` on a fixture baseline. PRs: `redesign/b-skills-canonical-layout`, `redesign/b-telemetry-parity-gate`.

**Phase C — legacy retire (W10), five surgical deletion PRs:**

1. `redesign/c-cli-retire-session-kg-meter` — delete `cli_cmds/{session,kg,meter}.py`, `tests/wikify/{test_session,test_cli_kg,test_cli_meter}.py`, deregister sub-apps in `cli.py`. `meter` event emission moves to `RunStore.append_call()`.
2. `redesign/c-cli-retire-extract-draft-validate-bundle` — delete `cli_cmds/{extract,draft,validate,bundle}.py`, `tests/wikify/{test_cli_extract,test_cli_draft_bundle,test_cli_validate}.py` (rewrites already landed in W4/W5/W6), deregister sub-apps.
3. `redesign/c-store-retire-session-meter` — delete `src/wikify/{session,meter}.py`. Imports rewritten to `RunStore`.
4. `redesign/c-baselines-distill-citestore-debug-retire` — delete `src/wikify/baselines/`, `src/wikify/distill/{__init__,preload,write_runner}.py`, `src/wikify/citestore/__main__.py`. Helpers absorbed by stores.
5. `redesign/c-cli-prune-toplevel-and-paths` — drop legacy top-level commands (`trace`, `sample-claims`, `html`, `field-detect`) from `cli.py`; remove legacy accessors from `paths.py`; keep `migrate inspect`.

**Phase D — collapse adapters (W11).** PR `redesign/d-collapse-adapters` removes residual compatibility shims, documents `migrate inspect`, and ships the doc rewrites for `docs/architecture.md`, `AGENTS.md`, `docs/skill-centric-execution-plan.md`, `docs/filesystem-state-design.md` per requirement 11.

## 8. End-to-end MVP paths

| MVP | path |
|---|---|
| ingest | `wikify-ingest` skill → `wikify corpus build <source> --out <corpus>` → `wikify corpus refresh <corpus>` → `wikify corpus check <corpus>` |
| baseline | `wikify run init --bundle <b> --corpus <c> --strategy baseline` → `wikify-baseline` skill loop: `corpus find --seed` → forked `wikify-extract-concepts` → for each concept fork `wikify-gather-evidence` (`work claim`/`work add evidence`/`work release`) and `wikify-write-page` (`draft build`/writer/`draft check`/`wiki commit`) → `wikify-tend` → `wikify run close --status completed` |
| query | `wikify-query` skill: `wiki find` / `wiki show` / `corpus find` (fallback) → answer → `work add feedback query` → `wikify-consolidate-inbox` → optionally `wikify-refine-page` → `wiki commit` |
| render+eval | `wikify render --bundle <b> --format html --out <dir>` and `wikify eval --bundle <b> --report <path>` |

## 9. First three PRs

PR 1 — `redesign/a-paths` (W1).

| concern | detail |
|---|---|
| files added | `src/wikify/cli_cmds/migrate.py`, `tests/wikify/test_paths_layout.py`, `tests/wikify/test_migrate_inspect.py`, fixture bundle under `tests/wikify/fixtures/legacy_bundle/` |
| files modified | `src/wikify/paths.py` (additive accessors `run_state_path`, `events_path`, `lock_path`, `io_dir`, `work_dir`, `work_index_path`, `work_inbox_dir`, `work_concept_dir`, `wiki_dir`, `wiki_articles_dir`, `wiki_people_dir`, `derived_dir`, `derived_index_path`, `derived_graph_path`, `derived_vectors_path`, `bundle_layout_version`); `src/wikify/cli.py` (register migrate sub-app, no other changes) |
| scope rules | no legacy accessor removed; no other workstream's files touched |
| verification | `uv run ruff check src/wikify tests/wikify`; `uv run pytest tests/wikify/test_paths_layout.py tests/wikify/test_migrate_inspect.py -q`; `uv run wikify migrate inspect tests/wikify/fixtures/legacy_bundle` |

PR 2 — `redesign/a-run` (W2). Depends on PR 1.

| concern | detail |
|---|---|
| files added | `src/wikify/events.py` (Pydantic envelope, event-type vocabulary, schema_version 1), `src/wikify/store/run.py` (RunStore: init/open/append_event/append_call/lock/unlock/close + token+cost aggregation from events), `src/wikify/cli_cmds/run.py` (init/show/list events/set/lock/unlock/close), `tests/wikify/test_events_schema.py`, `tests/wikify/test_run_store.py`, `tests/wikify/test_cli_run.py`, `tests/wikify/test_cli_io_emits_events.py` |
| files modified | `src/wikify/cli.py` (register `run` noun), `src/wikify/cli_io.py` (emit `cli_invoked` events, write large IO to `run/io/<event_id>.{stdout,stderr}.txt`), `src/wikify/cli_cmds/_helpers.py` (exit codes 3 and 4 surfaced) |
| scope rules | no other workstream's files touched; legacy `session`/`meter` untouched |
| verification | `uv run ruff check src/wikify tests/wikify`; `uv run pytest tests/wikify -q`; `uv run wikify run init --bundle /tmp/r1 --corpus tests/wikify/fixtures/mini_corpus --strategy baseline`; `uv run wikify run show --run /tmp/r1`; `uv run wikify run list events --run /tmp/r1 --tail 5`; `uv run wikify run close --run /tmp/r1 --status completed` |

PR 3 — `redesign/a-corpus` (W3). Depends on PR 1; independent of PR 2 (read-only on `run/`; `cli_invoked` emission becomes a no-op when no run context resolves).

| concern | detail |
|---|---|
| files added | `src/wikify/store/corpus_store.py` (CorpusStore wrapping `citestore.KnowledgeGraph`, `ingest.pipeline`, seed selection from `distill/seed.py`), `src/wikify/cli_cmds/corpus.py` (build/refresh/check/list/find/show), `tests/wikify/test_corpus_store.py`, `tests/wikify/test_cli_corpus.py` |
| files modified | `src/wikify/cli.py` (register `corpus` noun) |
| scope rules | does not modify `cli.py`'s `ingest` / `refresh` / `field-detect` legacy commands (they ship side-by-side until Phase C); no shared helper changes outside `_helpers.py` lock-context plumbing already landed in PR 2 |
| verification | `uv run ruff check src/wikify tests/wikify`; `uv run pytest tests/wikify -q`; `uv run wikify corpus list docs --corpus tests/wikify/fixtures/mini_corpus`; `uv run wikify corpus find "atomic layer deposition" --corpus tests/wikify/fixtures/mini_corpus --top-k 3`; `uv run wikify corpus show chunk:<some-chunk> --corpus tests/wikify/fixtures/mini_corpus --full`; `uv run wikify corpus check tests/wikify/fixtures/mini_corpus` |

## 10. Doc rewrites in scope

Per brief requirement 11, doc rewrites land in the same phase as the code they describe.

| doc | phase | scope |
|---|---|---|
| `docs/architecture.md` | D | rewrite top-to-bottom for the new noun-verb surface, store layer, events.jsonl telemetry, hybrid skill layout. Drop legacy section. |
| `AGENTS.md` | D | rewrite Read First list and CLI section. Remove legacy CLI table. Reference new skill layout. |
| `docs/filesystem-state-design.md` | D | promote from "design target" to "implementation reference"; remove the "not the current implementation" disclaimer. |
| `docs/skill-centric-execution-plan.md` | D | archive into `docs/history/` once consumed; the plan in `tasks/skill-centric-redesign-plan.md` becomes the authoritative implementation reference. |
| `.claude/skills/wikify/references/cli-tool-surface.md` | A (each PR) | extended incrementally as each noun ships. |
| `.claude/skills/wikify/references/schemas.md` | A (each PR) | extended incrementally — events envelope (W2), work.md/evidence.jsonl/inbox (W4), draft/response/validation (W5), wiki + derived (W6/W7). |
| `tasks/lessons.md` | continuous | append after every correction per CLAUDE.md format. |

