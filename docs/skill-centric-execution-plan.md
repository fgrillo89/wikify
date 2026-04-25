# Skill-centric execution plan

This document is the planning brief for the full Wikify redesign. It is the
contract that drives production of the actual implementation plan. Any agent
producing that plan must follow it.

## Read first

- `CLAUDE.md`
- `AGENTS.md`
- `docs/architecture.md`
- `docs/filesystem-state-design.md`

`docs/architecture.md` and `AGENTS.md` describe the legacy CLI surface
(`session`, `kg`, `extract`, `draft`, `validate`, `bundle`, `meter`). The
redesign target is the noun-verb surface in `docs/filesystem-state-design.md`
plus the decisions below.

## Goal

Produce an implementation plan for the full skill-centric Wikify redesign with
PR-sized slices, disjoint ownership, and a phased legacy-removal target. The
brief itself is not the implementation plan; it is the contract for producing
one.

## Architectural decisions (load-bearing — do not relitigate)

1. **Skills own strategy. Python is deterministic only.** No
   `run_baseline`/`run_guided`/`query_improve` Python entry points. No model
   SDK calls in Python. The CLI exposes control surfaces; strategy is composed
   in skills from CLI atoms.

   Skills split into two kinds, both implemented as canonical Claude skills
   (each is its own directory under `.claude/skills/<skill-name>/` with a
   `SKILL.md`):

   - **Atomic skills**: single-purpose, reusable building blocks with declared
     inputs/outputs (e.g. `wikify-extract-concepts`, `wikify-gather-evidence`,
     `wikify-write-page`, `wikify-refine-page`, `wikify-consolidate-inbox`,
     `wikify-answer-from-wiki`, `wikify-tend`). Each atomic skill runs in a
     forked subagent (`context: fork` with an explicit `agent`), composes CLI
     atoms via Bash, and loads the prompt assets it needs from its own or
     shared reference material.
   - **Workflow skills**: compositions of atomic skills that encode a strategy
     (`wikify-baseline` now; `wikify-guided`, `wikify-free`, `wikify-query`,
     `wikify-ingest`, `wikify-maintain` later). Workflow skills hold loop
     shape, stopping criteria, and parallel-agent dispatch (invoking atomic
     skills via the Skill tool or spawning subagents that load atomic
     skills). They contain no model-call logic of their own.

   Baseline is the focus workflow. Guided and free are deferred. The
   atomic-skill inventory must be complete enough that guided and free can be
   added later as new workflow skills with no new Python and no new atoms.

2. **CLI is the agent's only normal interface to bundle state.** `list` = ls;
   `find --text` = rg; `find` (no flag) = semantic/graph retrieval; `show` =
   cat with parsed text/YAML views. Direct shell `ls/rg/cat` is for debugging
   only.

3. **Query is a skill, not a CLI noun.** Python exposes only deterministic
   feedback verbs (`work add feedback query`, `work list inbox query_feedback`).
   The answering loop lives in a workflow skill.

4. **Legacy removal is the explicit target, executed in named phases.** No
   permanent compatibility aliases. Adapters that span more than one PR are
   permitted only if their deletion PR is named in the plan. Each phase ends
   with concrete file deletions.

5. **Existing on-disk data is preserved.** Do not delete or rewrite
   `data/wikis/*` or `data/corpora/*` produced under the legacy layout. Ship a
   one-shot read-only migration helper; legacy data may remain legacy.

6. **Parallelism is explicit.** Multiple agents may operate on one bundle. The
   plan must specify the lock contract, per-concept claim semantics, claim
   TTL, and CLI verbs to expose them. Add verbs where needed (suggested:
   `work claim <concept>`, `work release <concept>`, `work list claims`).
   Specify exit codes for contention.

7. **Test focus is deterministic Python only**: CLI behavior (text and
   `--format json`), JSON/JSONL schema validation (Pydantic), fluent API
   (`Bundle`, corpus KG, wiki KG), store-layer mutations, lock/claim
   atomicity, path resolution. Skills and agent loops are NOT in the
   unit-test scope. Integration tests exercise the CLI surface end-to-end,
   not the model-calling skills.

8. **Reuse over rewrite.** The redesign is structural, not a from-scratch
   rewrite of working logic. Prompts, validators, fluent KGs, ingest
   pipeline, metric math, and renderer templates are preserved. The
   replacement bar is high: every REPLACE tag in the preservation inventory
   must show that the legacy logic cannot be moved or rewired.

## Target product surface

Top-level nouns: `corpus`, `run`, `work`, `draft`, `wiki`, `render`, `eval`.
Query is a skill, not a noun.

Use the command grammar from `docs/filesystem-state-design.md` with these
fixes:

- `wikify eval --bundle <bundle> [--report <path>]` — drop the `run` verb; it
  collides with the top-level noun.
- `wikify render --bundle <bundle> --format html [--out <dir>]` — html is a
  format, not a sub-noun.
- Drop `wikify query ask` and `wikify query feedback list|show|apply`.
  Feedback verbs live under `work`.
- `wikify corpus build <source>` keeps the bare positional. Resolve
  consistency with `wiki build indexes|graph|vectors` in the plan.
- Add concurrency verbs:
  ```text
  wikify work claim <concept> [--ttl <s>] [--owner <id>]
  wikify work release <concept>
  wikify work list claims
  ```
  Specify exit codes (recommended: 2 = lock/claim held, 3 = budget,
  4 = stale-claim-broken).
- `wikify run set --corpus`: either forbid mid-run corpus change or require
  re-validation of all `evidence.jsonl` quotes. Pick one in the plan.

## Skill layout

Follow canonical Claude skill conventions. Each skill is its own directory
under `.claude/skills/<skill-name>/` containing a `SKILL.md` (required) plus
optional supporting files in subdirectories (`references/`, `scripts/`,
`assets/`). The current `.claude/skills/wikify/{reference,workflows}/` tree
is **not** a Claude-discoverable skill — there is no `SKILL.md`, so the
files are loaded only because `AGENTS.md` and `CLAUDE.md` reference them by
path. Migration is required.

The redesign uses a hybrid layout: one shared-reference skill plus one
discoverable skill per atomic skill and per workflow skill.

```text
.claude/skills/
  wikify/                          shared reference + project context
    SKILL.md                       (user-invocable: false)
    references/
      schemas.md
      cli-tool-surface.md
      write-constraints.md
      citation-format.md
      tiers.md
      escalation.md
      knowledge-graph.md
      wiki-graph.md
      person-pages.md
  wikify-baseline/                 workflow skill (slash-invocable)
    SKILL.md
  wikify-guided/                   workflow skill (deferred; stub)
    SKILL.md
  wikify-free/                     workflow skill (deferred; stub)
    SKILL.md
  wikify-query/                    workflow skill (deferred; focus M2)
    SKILL.md
  wikify-ingest/                   workflow skill
    SKILL.md
  wikify-maintain/                 workflow skill
    SKILL.md
  wikify-extract-concepts/         atomic skill (context: fork)
    SKILL.md
  wikify-gather-evidence/          atomic skill (context: fork)
    SKILL.md
  wikify-write-page/               atomic skill (context: fork)
    SKILL.md
  wikify-refine-page/              atomic skill (context: fork)
    SKILL.md
  wikify-consolidate-inbox/        atomic skill (context: fork)
    SKILL.md
  wikify-answer-from-wiki/         atomic skill (context: fork)
    SKILL.md
  wikify-tend/                     atomic skill (deterministic CLI dispatch)
    SKILL.md
```

### Frontmatter conventions

Every `SKILL.md` declares its name, description, invocation policy, and
allowed tools. Examples:

Workflow skill (`wikify-baseline/SKILL.md`):
```yaml
---
name: wikify-baseline
description: Run the baseline Wikify workflow on the active bundle. Use when the user asks to build a wiki, run a baseline pass, extract concepts, or grow an existing bundle from a corpus. Dispatches atomic Wikify skills for extract, evidence, write, refine, and tend.
allowed-tools: Bash(wikify *) Skill(wikify-*) Task
---
```

Atomic skill (`wikify-write-page/SKILL.md`):
```yaml
---
name: wikify-write-page
description: Write one Wikipedia-style article or person page from a prepared draft. Use when wikify-baseline or another workflow has built a draft.json and a writer subagent must produce response.json. Returns a validated WriteResponse path.
context: fork
agent: general-purpose
model: claude-sonnet-4-6
allowed-tools: Bash(wikify draft *) Bash(wikify wiki *) Bash(wikify corpus show *) Read
---
```

Shared mega-skill (`wikify/SKILL.md`):
```yaml
---
name: wikify
description: Project-wide reference for the Wikify pipeline. Loads schemas, CLI grammar, citation format, write constraints, tier mapping, and escalation policy. Use when working on Wikify code, skills, prompts, or bundle artifacts.
user-invocable: false
---
```

### Naming, sizing, references

- Names are lowercase letters/numbers/hyphens, ≤ 64 chars, never contain
  `claude` or `anthropic`.
- Each `SKILL.md` body stays under 500 lines. Detailed material moves to
  `references/<topic>.md` files inside the same skill directory.
- References are at most one level deep from the `SKILL.md` that loads them.
  No reference file references another reference file.
- Descriptions are written in third person, front-load the use case, and
  include concrete trigger phrases. The combined `description` +
  `when_to_use` is capped at 1,536 characters in the skill listing.
- `disable-model-invocation: true` is set on workflow skills with side
  effects when manual control is required (e.g. `wikify-ingest` should not
  fire automatically).

### Atomic skill contract

Each atomic skill `SKILL.md` declares:

- single-line responsibility (the trigger sentence in `description`)
- the CLI atoms it invokes via Bash (e.g. `wikify corpus find`,
  `wikify draft build`, `wikify wiki commit`)
- the subagent tier (S/M/L → haiku/sonnet/opus) via `model:` and `effort:`
- the prompt assets it loads (filenames under its own `references/` or
  paths into the shared `wikify/` skill — referenced by relative path
  inside the project)
- inputs (positional `$ARGUMENTS` or `arguments:` list) and outputs
  (named files in the bundle)

### Workflow skill contract

Each workflow skill `SKILL.md` declares:

- the loop shape and stopping criteria
- which atomic skills it dispatches and in what order
- parallelism policy (how many concurrent forks, claim/release rhythm)
- budget and telemetry expectations

Workflow skills must reuse the atomic-skill inventory. Adding a new strategy
must not require new atoms.

### Shared-reference policy

Shared reference material lives only in `wikify/references/`. Atomic and
workflow skills reference it by relative path. Do not duplicate references
across skills. The shared `wikify/` skill is `user-invocable: false` so it
sits in context as background knowledge without polluting the slash menu.

## Risky seams to address explicitly

- **Lock + claim contract.** TTL, ownership, stale-claim breaking by
  `work tend`, contention exit codes.
- **Inbox concurrency.** Atomic-append semantics for `work/inbox/*.jsonl`
  under multiple writers. Specify the OS-level guarantee (one inbox file per
  writer + merge during tend is recommended; sidesteps Windows
  `O_APPEND`-atomicity portability).
- **Telemetry parity gate.** Before deleting `_calls.jsonl`/`_run.json`,
  M1/M3/M5/M6 and cost aggregates derived from `events.jsonl` must match the
  current values on a fixture baseline run.
- **CLI IO replay completeness.** `cli_invoked` events capture
  argv/cwd/exit/duration/preview/path so a run reconstructs without the
  original process tree.
- **Path migration.** `articles/` → `wiki/articles/`, `_session/` → `run/`,
  `_scratch/<slug>` → `work/concepts/<slug>/`, `_calls.jsonl` →
  `events.jsonl`. Render/eval rewrites land in the same phase that
  introduces the new layout.
- **Text output consistency.** `list` = one handle per line; `find` =
  `score id doc preview` columns; `show` renders text/YAML for JSON files.
  No raw JSON in default output.
- **Quote grounding.** `draft check` enforces verbatim-substring quotes; same
  gate runs in `wiki commit`.
- **Graph/vector loading.** Lazy-load on first
  `corpus find --near|--neighbors|...` call; caches in `derived/`. Document
  the hot-path.
- **Bundle resolution.** CLI resolves `./run/state.json` from CWD or `--run`;
  tests use `Bundle.open(path)` and never depend on CWD.
- **Skill compaction and re-invocability.** Skills load once into the
  conversation and stay until auto-compaction. Long-running workflows must
  be re-invokable (no hidden persistent state in skill body); per-iteration
  state lives in `work/` and `run/state.json`, not in the skill content.
- **Atomic skill isolation.** Atomic skills run in forked subagents
  (`context: fork`). The subagent does not inherit conversation history.
  Every input the atomic skill needs must arrive via `$ARGUMENTS`, the
  shared `wikify/` reference skill (preloaded), or files the skill reads
  via Bash from the bundle. Skills must be self-contained.
- **Description discoverability.** Auto-discovery depends entirely on the
  skill `description`. Each atomic and workflow skill must include concrete
  trigger phrases in third person. Skills with side effects use
  `disable-model-invocation: true` to prevent unintended auto-invocation.

## Planning requirements

1. **Inspect the repo.** Enumerate every legacy module, command, schema,
   skill file, and bundle path slated for removal. Tag each with the phase
   that deletes it.

2. **Preservation inventory.** Before any deletion list, enumerate everything
   that must be preserved verbatim or migrated with minimal change. Treat
   this as load-bearing IP. At minimum, audit and tag:

   - `src/wikify/prompts/` — every prompt template (writer, extract, refine,
     query, person-page, etc.). Tag each KEEP / REFACTOR / REPLACE with
     reasoning.
   - `src/wikify/distill/` — extract prompts, dossier assembly, write_runner
     contracts, seed selection. Identify deterministic parts that move into
     the package-per-noun homes (`bundle/work/`, `bundle/draft/`,
     `corpus/`) and prompt-side parts that stay in `prompts/`.
   - `src/wikify/schema.py` — `WriteRequest`/`WriteResponse` Pydantic
     contracts. KEEP unless structurally incompatible; never silently
     rewrite.
   - `src/wikify/baselines/` — `BaselineConfig` and per-page evidence
     helpers. Triage which knobs survive and which become skill parameters.
   - `src/wikify/citestore/` — corpus fluent KG. KEEP. Surface unchanged
     through `corpus find`.
   - `src/wikify/store/wiki_graph.py` — wiki fluent KG. KEEP. Surface
     unchanged through `wiki find`.
   - `src/wikify/eval/` — M1/M3/M5/M6 metric implementations. KEEP the math;
     rewire inputs to `events.jsonl` and the new bundle layout.
   - `src/wikify/render/` — site renderer. KEEP the templating; rewire to
     new paths.
   - `src/wikify/ingest/` — parse/chunk/embed/graph pipeline. KEEP. Surface
     through `corpus build/refresh`.
   - Validation: structural validation, `[^eN]` marker resolution, verbatim
     quote-grounding (`QuoteNotInChunkError`). KEEP in full and run from
     `draft check` and `wiki commit`.
   - Citation grammar (`citation-format.md`) and write constraints
     (`write-constraints.md`, Wikipedia-MoS rules). KEEP as prompt assets.
   - Person-page rules: `author_context` assembly, banned phrases ("appears
     in this corpus"), graceful degradation. KEEP.
   - Escalation policy and tier mapping. KEEP.
   - Lessons in `tasks/lessons.md` and corrections in `CLAUDE.md`. KEEP
     every entry.

   Output: a table with columns
   `path | role | KEEP/REFACTOR/REPLACE | new home | rationale`. Anything
   tagged REPLACE must justify why the existing logic cannot be reused.

3. **Final `src/wikify/` package layout.**

4. **Final CLI command tree** with grammar fixes applied.

5. **Final skill set** under `.claude/skills/` per the layout above:
   - One discoverable skill per atomic skill and per workflow skill, plus
     the shared `wikify/` reference skill.
   - For each skill, specify: directory name, full frontmatter
     (`name`, `description`, `context`, `agent`, `model`, `allowed-tools`,
     `disable-model-invocation`, `user-invocable` as applicable),
     `SKILL.md` body outline, supporting files under `references/`, and
     the prompt assets it loads from the shared `wikify/` skill.
   - Atomic skills: name, single-line responsibility, CLI atoms it composes,
     subagent tier mapped to `model:` (S=haiku, M=sonnet, L=opus), inputs
     and outputs.
   - Workflow skills (focus: `wikify-baseline`; stubs: `wikify-guided`,
     `wikify-free`, `wikify-query`, `wikify-ingest`, `wikify-maintain`).
     Each names the atomic skills it dispatches and its loop/parallelism
     shape.
   - Prove composability: show that `wikify-guided` and `wikify-free` can
     be expressed as different orderings/budgets over the same atomic-skill
     set, with no new atoms required.
   - Verify each `SKILL.md` body stays under 500 lines and references are
     one level deep.
   - Skills do not encode Python imports. Skills call the CLI; subagents
     read prompt assets and CLI text output.
   - Migration: the existing `.claude/skills/wikify/{reference,workflows}/`
     tree is split — `reference/*` moves into `wikify/references/` under
     the new shared mega-skill (with a `SKILL.md`), and
     `workflows/run-baseline.md` is rewritten as `wikify-baseline/SKILL.md`
     with body under 500 lines.

6. **Workstream decomposition.** Each workstream owns one domain package
   (`bundle/run/`, `bundle/work/`, `bundle/draft/`, `bundle/wiki/`,
   `corpus/`, `citations/`, etc.) plus its CLI handler in `cli/<noun>.py`
   and one or more skill files. `cli/__init__.py` registers nouns
   alphabetically. Workstreams may not edit other nouns' packages except
   inside a designated cross-cutting workstream (paths/api, telemetry
   envelope, shared CLI helpers). The package-per-noun layout — including
   the `bundle/` umbrella for the four bundle-internal packages — is
   load-bearing; any deviation must justify why the existing structure
   cannot be reused.

7. **Per-workstream spec:**
   - files/modules owned (full paths)
   - implementation responsibilities
   - legacy files deleted in this workstream vs. legacy files deleted in a
     later, named cleanup PR
   - tests to add/update/delete (deterministic only, per decision 7)
   - dependencies on other workstreams
   - acceptance criteria

8. **Phased legacy-removal plan.** Each phase names its deletion PR.

   - **Phase A**: introduce new layout + new CLI nouns alongside legacy.
     Skills still call legacy.
   - **Phase B**: migrate skills + render + eval to the new surface. The
     legacy `.claude/skills/wikify/{reference,workflows}/` doc tree is
     split into the canonical hybrid layout (shared `wikify/` mega-skill
     plus one discoverable directory per atomic and workflow skill).
     Legacy CLI nouns kept as thin adapters calling new stores. Telemetry
     parity gate must pass.
   - **Phase C**: delete legacy CLI nouns, legacy bundle paths in
     `paths.py`, legacy schemas, legacy skill files.
   - **Phase D**: collapse the migration helper into a documented utility;
     remove transitional adapters.

9. **End-to-end MVP paths:**
   - **ingest**: source files → corpus
   - **baseline**: corpus → concepts → evidence → draft → validation → wiki
     commit (parallel agents with claim/release verbs)
   - **query**: skill-driven; CLI provides feedback verbs only
   - **render + eval**: wiki bundle → static HTML; wiki bundle + corpus →
     metrics report

10. **First 3 PRs in order**, with exact scope, file lists, and verification
    commands (`uv run ruff check ...`, `uv run pytest tests/wikify -q`, plus
    targeted CLI smoke commands).

11. **Doc rewrites in scope.** `docs/architecture.md`, `AGENTS.md`,
    `docs/filesystem-state-design.md`, and this document are rewritten in
    the same phase as the code they describe.

## Output format

- Markdown plan, target 3000–4000 words.
- File-path tables, not prose, for module ownership and deletion lists.
- Dependency DAG (text or table) for workstream ordering.
- Use parallel exploration agents aggressively for repo inspection. Final
  output is a concrete plan, not discussion.
