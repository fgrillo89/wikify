# Finish Skill-Centric Redesign In One Session

This is a paste-ready implementation prompt for an agent runtime that can
delegate to parallel workers. It assumes the repository is already past W0-W6,
Phase C deletion, and the post-cleanup fixes on top of #51. The goal is to
finish W7-W11 in one integrated branch, avoiding temporary shims and partial
refactor states.

## Prompt

You are finishing the Wikify skill-centric redesign in a single implementation
session. Work in one dedicated branch and produce one PR. Do not split this
into temporary compatibility PRs. Do not preserve backward compatibility unless
explicitly named below.

Read these first:

- `AGENTS.md`
- `docs/skill-centric-execution-plan.md`
- `tasks/skill-centric-redesign-plan.md`
- `docs/filesystem-state-design.md`
- `docs/architecture.md`

Current status:

- W0-W6 are merged.
- Phase C deletion mostly landed: `session.py`, `meter.py`, `paths.py`,
  `baselines/`, `distill/`, and legacy CLI command files are gone.
- Current branch may already contain uncommitted W7/W8 fixes. Inspect them and
  integrate or correct them. Do not discard existing user/agent work.
- #51 removed the main strategy-in-Python smells and rewrote some v2 skill
  references, but the canonical skill layout is still incomplete.

Binding product shape:

- Strategy and workflow loop shape live in skills, not Python.
- Python exposes deterministic, composable primitives only.
- Final top-level CLI nouns are:
  `corpus`, `run`, `work`, `draft`, `wiki`, `render`, `eval`, `migrate`.
- `render` and `eval` are top-level nouns and use `--bundle`, not `--run`:
  `wikify render --bundle <bundle> --format html [--out <dir>]`
  `wikify eval --bundle <bundle> [--corpus <corpus>] [--report <path>]`
- No `wikify session`, `wikify kg`, `wikify meter`, `wikify html`,
  `wikify extract`, `wikify validate`, or `wikify bundle` commands remain.
- No `BaselineConfig`, `run_baseline`, Python baseline strategy package, or
  Python strategy controller may be introduced.
- Existing v1 bundles may be inspected read-only by `wikify migrate inspect`.
  They are not a compatibility execution surface.

Hard constraints:

- No temporary shims.
- No dead compatibility modules.
- No duplicated ownership paths. If a function is moved to its final package,
  delete or rewrite the old path in the same change. Do not leave both old and
  new implementations alive "until later".
- No parallel implementations of the same concept under different names. Before
  adding a module, search for the existing owner and either extend it or delete
  the displaced one.
- No Python defaults that silently choose strategy, model tier, model id, loop
  shape, evidence top-k, or budget splits.
- If a command mutates a bundle, it must use the v2 lock/claim contract and
  structured CLI errors.
- Default CLI output is token-light text; `--format json` is the automation
  contract.
- CLI invocations continue to be logged through the existing `cli_invoked`
  event and `run/io/*` artifacts where a bundle context exists.

Use parallel workers with non-overlapping ownership:

Important coordination rule: parallelism is for speed, not for creating three
versions of the same abstraction. Each worker owns only the paths listed for
that worker. If a worker discovers that their task requires touching another
worker's owned path, they must stop and report the needed change to the
coordinator instead of editing across the boundary. The coordinator decides
which worker owns the dependency and prevents duplicate implementations.

Add periodic cleanup checkpoints:

- After each worker finishes a coherent slice, run `git diff --stat` and a
  stale-surface `rg` before continuing.
- Reevaluate whether any adapter, helper, or renamed module is now redundant.
- Prefer deleting displaced code immediately over documenting it as temporary.
- If two files now answer the same question, collapse to one canonical owner.
- If a test was deleted because behavior was retired, add or update the v2 test
  that proves the replacement behavior. Do not leave coverage gaps disguised as
  cleanup.

## Worker A: W7/W8 Render, Eval, Telemetry, MVP Smoke

Ownership:

- `src/wikify/cli/render.py`
- `src/wikify/cli/eval.py`
- `src/wikify/cli/__init__.py` only for registering `render` and `eval`
- `src/wikify/render/**` if path rewiring is needed
- `src/wikify/eval/**`, especially `trace_replay.py`
- `tests/wikify/test_cli_render.py`
- `tests/wikify/test_cli_eval.py`
- new telemetry parity and end-to-end smoke tests

Tasks:

- Finalize `wikify render --bundle <bundle> --format html [--out <dir>]`.
- Rewire the static renderer to read v2 `wiki/articles`, `wiki/people`, and
  `derived/*`. Do not read legacy top-level `articles/` or `people/`.
- Finalize `wikify eval --bundle <bundle> [--corpus <corpus>] [--report <path>]`.
- Preserve existing metric math. Rewire inputs to v2 layout and
  `run/events.jsonl`.
- Implement corpus-dependent metrics behind explicit `--corpus`: M1 coverage
  residual and M6 grounding. If `--corpus` is absent, emit the corpus-free
  subset and mark corpus-dependent metrics as unavailable in the report rather
  than fabricating values.
- Rewire M5/trace replay to consume `run/events.jsonl`; do not use
  `_calls.jsonl` or `_run.json`.
- Write the eval report to `derived/eval.json` by default when `--report` is
  omitted.
- Backfill a telemetry parity regression test using fixture/golden data. Since
  legacy code has been deleted, compare event-derived aggregates to a checked-in
  golden fixture, not to resurrected legacy code.
- Add an end-to-end MVP smoke test that exercises the deterministic baseline
  skeleton without an LLM: corpus build or fixture corpus, `run init`,
  seed/find evidence, `work add concept`, `work add evidence`, `draft build`,
  synthetic valid response, `draft check`, `wiki commit`, `render`, `eval`.

Acceptance:

- `uv run python -m wikify.cli render --help` works.
- `uv run python -m wikify.cli eval --help` works.
- Render and eval tests pass.
- The MVP smoke proves the v2 CLI can complete a baseline-shaped wiki lifecycle.

## Worker B: W9 Canonical Skills

Ownership:

- `.claude/skills/**`
- `tests/wikify/test_skill_layout.py`

Tasks:

- Create the canonical hybrid skill layout from
  `tasks/skill-centric-redesign-plan.md`.
- Add `.claude/skills/wikify/SKILL.md` as the shared mega-skill. It should
  explain the v2 bundle nouns, CLI defaults, logging, state files, and how to
  choose atomic/workflow skills.
- Move shared references under `.claude/skills/wikify/references/` and update
  links. Use `references`, not the old singular `reference`, unless you update
  every reference consistently.
- Add a dedicated baseline workflow skill, e.g.
  `.claude/skills/wikify-baseline/SKILL.md`, that implements the baseline
  process entirely through v2 CLI primitives:
  concept extraction or seed discovery, evidence retrieval, writer-agent
  fanout, draft validation, wiki commit, render, eval.
- Add atomic skills for the primitive actions. Use clear names and keep each
  `SKILL.md` small:
  `wikify-corpus`, `wikify-run`, `wikify-work`, `wikify-draft`,
  `wikify-wiki`, `wikify-render`, `wikify-eval`.
- Add workflow stubs for non-baseline modes without implementing Python logic:
  `wikify-query`, `wikify-guided-explore`, `wikify-refine`,
  `wikify-render-eval`, and one additional workflow if the plan already names
  it. Stubs must describe composition over primitives, not introduce new CLI.
- Remove all skill references to retired commands and artifacts:
  `wikify session`, `wikify kg`, `wikify meter`, `wikify html`,
  `wikify extract`, `wikify validate`, `wikify bundle`, `_session`,
  `_scratch`, `_calls.jsonl`, `_run.json`, `BaselineConfig`.
- Add `tests/wikify/test_skill_layout.py` that checks every skill directory has
  a `SKILL.md`, shared references resolve, no retired commands appear, and each
  `SKILL.md` stays reasonably small.

Acceptance:

- An agent reading only the skills can run the v2 baseline lifecycle without
  touching raw JSON files except through documented CLI outputs.
- The skills explicitly say strategy decisions live in skill markdown and the
  agent prompt, not Python.

## Worker C: W10/W11 Cleanup, Adapter Collapse, Docs

Ownership:

- `src/wikify/api.py`
- `src/wikify/cli/migrate.py`
- `src/wikify/citations/__main__.py`
- `src/wikify/cli/legacy/**`
- `docs/architecture.md`
- `docs/filesystem-state-design.md`
- `docs/skill-centric-execution-plan.md`
- `AGENTS.md`
- `tasks/skill-centric-redesign-plan.md`

Tasks:

- Delete `src/wikify/citations/__main__.py`.
- Remove `src/wikify/cli/legacy/` entirely if only empty/package litter
  remains. Do not leave empty compatibility packages.
- Delete `src/wikify/bundle/wiki/post_commit.py` if still present. The v2
  projection implementation is `bundle/wiki/derived.py`.
- Audit `api.py`: keep only the read-only layout detection needed by
  `migrate inspect`. If `LegacyBundle` remains, document it as a read-only
  inspector helper, not an execution adapter. If it is unused, remove it and
  update tests.
- Update `cli/migrate.py` help text so it says read-only inspector, not
  eventual migration executor.
- Rewrite architecture docs to the final v2 state. Remove W7/W8 pending notes
  once Worker A lands them.
- Rewrite `docs/filesystem-state-design.md` so examples use
  `wikify render --bundle` and `wikify eval --bundle`, not old positional or
  v1 commands.
- Update `docs/skill-centric-execution-plan.md` and
  `tasks/skill-centric-redesign-plan.md` to record the final state and any
  deliberate deviations. Historical notes are fine only if clearly marked as
  historical and not agent instructions.
- Ensure `AGENTS.md` matches the final CLI and skill layout.

Acceptance:

- `rg "wikify session|wikify kg|wikify meter|wikify html|BaselineConfig|run_baseline|post_commit|cli/legacy|citations/__main__" src tests docs AGENTS.md .claude/skills tasks -g "!.claude/worktrees/**"`
  returns only intentionally historical references, if any. Prefer zero in
  active agent-facing docs and skills.
- No empty legacy package remains.

## Coordinator Integration

Do the initial local work yourself:

- Inspect `git status`.
- Preserve existing uncommitted changes unless they conflict with final design.
- Quickly read the current uncommitted W7/W8 files before delegating so workers
  do not duplicate or revert them.
- Spawn Workers A, B, and C in parallel with the ownership boundaries above.

When workers return:

- Integrate their changes in one branch.
- Run an overlap review before running the final suite:
  identify files/modules with similar names or responsibilities, especially
  `render`, `eval`, `wiki derived/projections`, `api/migrate`, skill
  references, and CLI command wrappers.
- For every refactored path dependency, verify the old path is either deleted
  or explicitly reduced to a read-only inspector with tests proving that scope.
- Reject any result where both old and new paths can mutate or execute the same
  workflow.
- Resolve CLI consistency:
  `render`/`eval` must use `--bundle`.
- Ensure top-level help lists exactly:
  `corpus`, `migrate`, `run`, `work`, `draft`, `wiki`, `render`, `eval`.
- Ensure command docs and skills agree with actual `--help` output.
- Run the stale-surface audit:
  `rg "wikify session|wikify kg|wikify meter|wikify html|wikify extract|wikify validate|wikify bundle|BaselineConfig|run_baseline|src/wikify/baselines|post_commit|cli/legacy|citations/__main__" src tests docs AGENTS.md .claude/skills tasks -g "!.claude/worktrees/**"`
- Run the CLI smoke commands:
  `uv run python -m wikify.cli --help`
  `uv run python -m wikify.cli render --help`
  `uv run python -m wikify.cli eval --help`
  `uv run python -m wikify.cli corpus --help`
  `uv run python -m wikify.cli wiki --help`
- Run quality gates:
  `uv run ruff check src/wikify tests/wikify scripts`
  `uv run pytest tests/wikify -q`

Definition of done:

- One PR finishes W7-W11 plus the backfilled telemetry parity gate and MVP
  smoke.
- No Python strategy controller exists.
- Skills are the only place where baseline/query/guided/refine workflow logic
  is described.
- CLI, docs, and skills all describe the same v2 surface.
- Full test suite and ruff pass.
- The PR description lists what changed, the stale-surface audit result, and
  the exact verification commands.
