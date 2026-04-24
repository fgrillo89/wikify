# Skill-centric pivot: Phase 0 + baseline slice — execution plan

## Context

`docs/skill-centric-pivot.md` commits Wikify to a skill-driven architecture: skills own the per-iteration loop, model calls happen only in Claude Code subagents, deterministic tools are `wikify` CLI subcommands, and a durable `session.json` coordinates across subagent/process boundaries. This PR lands the doc, builds the skill-pack shell (Phase 0 references + smoke tests), and proves the architecture with one vertical slice — `run-baseline.md` producing schema-identical bundles to the current `run_baseline()` against `tests/fixtures/tiny/`. Old Python baseline stays intact; schema parity on `_run.json` / `_calls.jsonl` is the merge gate.

## Commits

Single branch `skill-pivot/phase-0-baseline`, three commits, one PR.

1. **`docs: land refined skill-centric-pivot plan`** — this file + the refined architecture doc.
2. **`skills: reference consolidation + smoke tests`** — Phase 0.
3. **`wikify: session + baseline CLI + run-baseline skill + parity test`** — baseline slice.

## Phase 0 — Reference consolidation + smoke tests

### New reference files under `.claude/skills/wikify/reference/`

Each prose-only, ~150–300 lines, `name` + `description` frontmatter. No dispatch language.

- **`schemas.md`** — enumerate every durable artifact (`session.json`, `_run.json`, `_calls.jsonl`, `draft-<page_id>.json`, `validation-<page_id>.json`, `pages/*.md`, `_wiki_graph.json`, `_index.json`). Required keys, owning CLI command, `schema_version` policy (monotonic integer, bumped on breaking changes). Reference `src/wikify/schema.py` as executable truth.
- **`cli-tool-surface.md`** — 8-family `wikify` grammar (`session | kg | draft | validate | bundle | render | eval | ingest`). Conventions: stdout = JSON unless `--full`, non-zero exit = hard fail, `--session <path>` explicit on every mutation, lockfile semantics.
- **`write-constraints.md`** — MoS rules currently duplicated in `handlers/write.md`. Reference `src/wikify/schema.py::WriteResponse` validators by name; prose describes intent.
- **`citation-format.md`** — `[[cite:key]]` syntax, reference-list shape, "quote must be substring of cited chunk" rule.
- **`tiers.md`** — S/M/L → haiku/sonnet/opus, per-role defaults (extract=S, write=M, escalate=L), `--tier` override semantics.
- **`escalation.md`** — "spawn Task subagent at tier L when validation fails twice" pattern. Prose only.
- **`atoms.md`** — `seed-select`, `extract`, `retrieve-evidence`, `draft`, `validate`, `commit-page`, `checkpoint`. Cross-link to CLI commands.

### Existing skills

- `handlers/*.md` + `runtime/serve-dispatch.md` — add one-line DEPRECATED banner. **Do not delete bodies** (still referenced by live `dispatch.py` and `distill/pipeline.py`).
- `reference/{knowledge-graph,orchestrator,parameters,wiki-graph}.md`, `workflows/{ask,run-campaign,run-scripted}.md` — leave alone.

### Smoke test — `tests/wikify/test_skill_smoke.py`

~80 lines, no fixtures, <1s. Three cases:

1. Frontmatter parses as YAML with `name` + `description`.
2. Every `reference/<name>.md` path mentioned in skill bodies resolves.
3. Every `wikify.<dotted.path>` mentioned in skill bodies `importlib.import_module`s.

Not a prose linter.

## Baseline slice

### CLI subcommands — new under `src/wikify/cli_cmds/`

| Command | Output | Backing |
|---|---|---|
| `wikify session init --bundle <p> --corpus <p> --strategy baseline` | writes `_session/session.json` v1 | new |
| `wikify session show --session <p> [--full]` | token-light JSON | new |
| `wikify session update --session <p> --patch <json>` | JSON Merge Patch stdin | new |
| `wikify session checkpoint --session <p> --label <s>` | copy to `_session/checkpoints/` | new |
| `wikify session close --session <p>` | `status=closed`, final `_run.json` flush | wraps `CostMeter.snapshot()` |
| `wikify kg seeds --corpus <p> --session <p>` | seed chunk-id JSON | extract `_select_seeds` from `run_baseline` |
| `wikify kg abstracts --corpus <p> --doc-ids <json>` | chunk payloads | wraps `KnowledgeGraph.chunks()` |
| `wikify kg evidence --corpus <p> --page-id <id> --top-k N` | ranked chunks | extract from `run_baseline` |
| `wikify draft write-request --session <p> --page-id <id>` | `draft-<page_id>.json`, path echoed | wraps `distill.write_prep` |
| `wikify validate write --draft <p> --response <p>` | `validation-<page_id>.json`, exit 0/1 | wraps `WriteResponse` validators |
| `wikify bundle commit-page --session <p> --response <p>` | writes page + index + graph updates | wraps existing store + graph writers |

Keep `wikify html` / `wikify eval` / `wikify ingest` as-is. No `wikify draft extract` this PR — baseline extract is a skill-driven Task call over seed chunks.

### Session JSON schema v1 (baseline subset)

```
schema_version: 1
session_id: str
strategy: "baseline"
bundle_root, corpus_root: str
status: "active"|"closed"|"failed"
created_at, updated_at: ISO8601
budget: {haiku_eq_target, haiku_eq_spent}
stages: {seed_selection, extract, write} each {status, started_at, finished_at}
pages: [{page_id, status: planned|drafted|validated|committed, draft_path, validation_path}]
config: {baseline_write_fraction, abstract_fraction, top_k, default_tiers}
telemetry_paths: {run_path, calls_path}
```

Defer `stopping_criteria`, `kpi_snapshot`, `acceptance_policy`, queue state — baseline has a finite deterministic page set.

### File layout additions

- `<bundle>/_session/session.json`, `_session/checkpoints/`, `_session/session.lock`
- `<bundle>/_scratch/draft-<page_id>.json`, `<bundle>/_scratch/validation-<page_id>.json`

Add `session_dir`, `scratch_dir` properties to `src/wikify/paths.py::BundlePaths`.

### `.claude/skills/wikify/workflows/run-baseline.md`

Sections per plan-doc template: Purpose, Inputs, Required session state, Commands, Model steps, Artifacts, Validation, Completion, Failure/Resume.

Per-iteration loop: pick next `status=planned` page from session → `wikify draft write-request` → Task subagent (tier M) writes `WriteResponse` JSON to scratch → `wikify validate write` → on success `wikify bundle commit-page` + session patch to `committed` → `wikify session checkpoint` every N pages → continue until no planned pages remain or budget exceeded.

### Parity test — `tests/wikify/test_baseline_skill_parity.py`

Single test against `tests/fixtures/tiny/`:

1. **Reference run**: `run_baseline(..., writer=_ValidFakeWriter(), extractor=FakeExtractor())` → bundle A.
2. **Skill-path run**: import Typer command functions directly (no subprocess); inject same fakes via `WIKIFY_TEST_FAKES` env gate.
3. **Assert**:
   - `set(_run.json.keys())` equal between A and B
   - Each `_calls.jsonl` line: same `CallRecord` field names (not values — seed order may differ)
   - `pages/*.md` filenames match
   - `_wiki_graph.json` top-level keys match

Extend `test_baseline_pipeline.py` by one assertion: `BundlePaths(root).session_dir` does not exist after pure-Python run.

Centralise `_ValidFakeWriter` / `FakeExtractor` in `tests/wikify/fakes.py` if not already there.

### `run_baseline()` stays intact

Skill path reuses `_select_seeds` and `_retrieve_evidence` via module-level refactor, not rewrite. Deletion is a follow-up PR gated on two weeks of green parity-test CI.

## Out of scope

Scripted E/M/X, guided workflow, `dispatch.py` deletion, `handlers/*.md` deletion, `schema_version` on `ExtractRequest`/`WriteRequest`/`WriteResponse`, handler-skill reintroduction, `run-campaign.md` / `run-scripted.md` / `ask.md` changes, MCP server, new corpora, changes to `wikify html`/`eval`/`ingest` internals.

## Verification

```
uv run ruff check src/wikify tests/wikify
uv run pytest tests/wikify/test_skill_smoke.py -v
uv run pytest tests/wikify/test_baseline_pipeline.py -v
uv run pytest tests/wikify/test_baseline_skill_parity.py -v
uv run pytest tests/wikify -q
uv run wikify session init --bundle scratch/parity-bundle --corpus tests/fixtures/tiny --strategy baseline
uv run wikify session show --session scratch/parity-bundle/_session/session.json
```

Manual: `_run.json` keys identical between Python-path and skill-path bundles; `jq 'keys' _session/session.json` returns exactly v1 fields; every skill markdown parses YAML frontmatter.

## Push-backs baked in

1. No `schema_version` on `WriteResponse`/`WriteRequest` this PR — 658-LOC test file would churn; session-only is the right cut.
2. Parity test calls Typer functions directly, not subprocess — 50× faster, real tracebacks.
3. Keep handler skills with DEPRECATED banner, do not delete — still live-referenced.
4. This plan is a repo doc, not PR description paste — PR descriptions get lost.
5. Extract stage stays skill-dispatched, not a new CLI — no `wikify draft extract` until scripted/guided need it.
