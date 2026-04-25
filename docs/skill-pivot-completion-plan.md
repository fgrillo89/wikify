# Skill-pivot completion plan

## Where we are after PRs #29–#32

- Tier 1 ✅ closed: full schema-level + value-level field parity between
  legacy `run_baseline()` `_run.json`/`_calls.jsonl` output and the
  skill-driven path. Phase 5 deletion gate is unblocked.
- The skill workflow `run-baseline.md` is documented and every CLI it
  invokes (`session`, `kg`, `draft`, `validate`, `bundle`, `meter`) is
  individually green-tested.
- Outstanding plumbing gaps: extract-output canonicalization, person
  pages on the skill path, no live model-driven smoke.
- Outstanding cleanup: legacy `dispatch.py`, six legacy CLI commands,
  seven dispatch-era handler skills, the legacy pipelines.

## What "completing the skill pivot" means

1. The skill workflow is the only path that produces wikify bundles.
2. Legacy code that backed the dispatch-era flow is removed.
3. The CLI surface is the narrow set committed in
   `docs/skill-centric-pivot.md` — eight families, no model-calling
   subcommands at the top level.

## Phased execution

### Phase A — `skill-pivot/baseline-completion` (this PR)

Close the plumbing gaps so the skill is functionally complete on
baseline, BEFORE deleting any legacy code.

- `wikify extract canonicalize --session <p> --responses '[<paths>]'`:
  reads N extract-response JSON files, dedupes/aliases concepts,
  appends `session.pages` entries with `status=planned`. Wraps the
  existing `distill.dossier.canonicalize` helper.
- `wikify bundle commit-page` accepts `kind="person"` and routes to
  `bundle_paths.people_dir`.
- `run-baseline.md` updated to show the canonicalize step. Person
  pages flagged as a separate write loop.
- Tests cover both new paths.

### Phase B — `skill-pivot/legacy-removal`

Delete dispatch and the six legacy CLI commands. CLI shrinks to the
deterministic + skill-driven set.

- Delete `src/wikify/dispatch.py`.
- Delete `src/wikify/cli.py` commands: `distill`, `campaign`, `study`,
  `persona-generate`, `maintenance`, `query`. Keep: `ingest`, `refresh`,
  `field-detect`, `trace`, `sample-claims`, `html`, `eval` (none
  model-calling on the user-facing surface). Plus the new sub-apps
  (`session`, `kg`, `draft`, `validate`, `bundle`, `meter`).
- Delete `_BUDGET_TABLE`, `_parse_budget`, and any dead helpers in
  `cli.py` whose only callers were the deleted commands.
- Delete `.claude/skills/wikify/handlers/{compact,edit,extract,
  maintenance,orchestrate,query,write}.md` and
  `runtime/serve-dispatch.md`.
- Replace any tests that exercise the deleted code paths.
- The parity test in `test_baseline_skill_e2e.py` still runs
  `run_baseline()` for comparison — that goes in Phase C.

### Phase C — `skill-pivot/distill-pipeline-removal`

Delete the legacy pipelines once they are unreferenced.

- Delete `src/wikify/distill/pipeline.py` (legacy distill main).
- Delete `src/wikify/baselines/pipeline.py::run_baseline` (legacy
  baseline).
- Audit `src/wikify/distill/` for now-unreferenced modules:
  `explorer.py`, `iteration.py`, `orchestrator_*.py`, parts of
  `strategy.py`, `write_runner.py` (parts).
- The parity test in `test_baseline_skill_e2e.py` switches from
  comparing against a live `run_baseline()` to a frozen reference
  bundle (or is replaced by the synthetic-records value-equality
  probe, which already exists).
- Delete tests that target removed modules.

### Phase D — optional, separate from this thread

- Scripted-E/M/X workflows.
- Guided workflow.
- Live model-driven smoke (Haiku in CI or recorded transcripts).

## Order discipline

A → B → C, strict. Phase A makes the skill complete; Phase B removes
the legacy fallback; Phase C removes the modules nothing references.
Reversing the order would either delete a fallback before the
replacement is complete (B before A), or remove still-imported
modules (C before B).

## Out of scope for this thread

- Person pages with full author_context plumbing on the skill side.
  Phase A delivers the routing; populating `author_context` from the
  corpus is a follow-up if person pages prove worth the effort.
- Live agent smoke. Phase A's tests still simulate the model with
  canned `WriteResponse`. Real agent runs are a manual smoke item.
