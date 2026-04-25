---
name: wikify/reference/schemas
description: Durable artifact catalog and schema_version policy for the wikify agent-backend file contract.
---

# Schemas

Every durable file the agent or CLI produces has a documented schema and a
monotonically increasing `schema_version` integer. This file is the authoritative
catalog; Pydantic models in `src/wikify/schema.py` and `src/wikify/session.py` are
the executable source of truth for shape validation.

## Schema_version policy

- Every durable artifact carries `schema_version: int`.
- Bump on a breaking field change: removed key, renamed key, changed type, changed semantics.
- Non-breaking additions (new optional field) do not bump.
- Skills assert `schema_version == N` before they read an artifact. If the version is ahead, fail fast with `SchemaVersionMismatchError`; do not attempt forward-compatibility in prose.
- Parity-test transcripts are invalidated on any bump; re-record and diff.

## Session artifacts

### `<bundle>/_session/session.json`

Source of truth: `src/wikify/session.py::SessionV1`.
Current `schema_version`: 1.

Required fields for baseline strategy:

- `schema_version: int`
- `session_id: str`
- `strategy: "baseline" | "scripted-E" | "scripted-M" | "scripted-X" | "guided"`
- `bundle_root: str` — absolute path
- `corpus_root: str` — absolute path
- `status: "active" | "completed" | "failed" | "abandoned"`
- `created_at, updated_at: ISO8601`
- `budget: {haiku_eq_target: int, haiku_eq_spent: int}`
- `stages: {seed_selection, extract, write}` — each `{status: pending|running|done|failed, started_at: str|null, finished_at: str|null}`
- `pages: [{page_id, status: planned|drafted|validated|committed|failed, draft_path: str|null, validation_path: str|null, kind: article|person, aliases: list[str]}]`
- `config: {baseline_write_fraction, abstract_fraction, top_k, default_tiers}`
- `telemetry_paths: {run_path, calls_path}`

Reserved (not required in v1, added in later strategies):

- `stopping_criteria`, `kpi_snapshot`, `acceptance_policy`, `iteration_index`,
  `recent_gains`, `active_page_id`, `active_chunk_ids`, `transcript_path`, `lock`.

Optional baseline-strategy fields (default to empty/"create"):

- `seed_doc_ids: list[str]` — PageRank+submodular-selected document IDs.
  Populated by `wikify kg seeds --persist`. Carried into `_run.json` on close.
- `seed_chunk_ids: list[str]` — abstract-equivalent chunk IDs per seed doc.
  Populated by `wikify kg seeds --persist`.
- `iteration: str` — per-iteration identifier for campaign-style reruns.
  Defaults to `"create"`; scripted/guided strategies bump it.

Owning CLI: `wikify session init/show/update/checkpoint/close`. The agent may
read the file directly; it may not hand-edit canonical fields — mutations go
through a CLI subcommand.

### `<bundle>/_session/checkpoints/<label>.json`

Whole-file snapshot of `session.json` taken by `wikify session checkpoint`.
Same schema as `session.json`. `schema_version` equals the session's at
checkpoint time.

### `<bundle>/_session/session.lock`

Opaque lockfile. Contents: `{owner, acquired_at, expires_at}`. Owned by
`wikify session lock/unlock`. No `schema_version`.

## Scratch artifacts

### `<bundle>/_scratch/draft-<page_id>.json`

The model-facing `WriteRequest` payload — prompt-layer-resolved, evidence-packed.
Canonical fields: `src/wikify/schema.py::WriteRequest` (frozen, `extra="forbid"`).

The scratch file also carries a top-level `schema_version: 1` envelope
field. The envelope is **not** part of the canonical `WriteRequest`
model — it is stripped by `wikify validate write` before Pydantic
validation. Skills and downstream tools may rely on the envelope to
version the on-disk format independently of the Pydantic schema.

Created by: `wikify draft write-request`. Read by: the write subagent.

### `<bundle>/_scratch/response-<page_id>.json`

The subagent's raw `WriteResponse` output. Canonical fields:
`src/wikify/schema.py::WriteResponse` (frozen, `extra="forbid"`). The
same `schema_version: 1` envelope convention applies — scratch writers
may emit it; `wikify validate write` strips it before Pydantic checks.

Created by: the write subagent (skill-driven). Read by: `wikify
validate write` and `wikify bundle commit-page`.

### `<bundle>/_scratch/validation-<page_id>.json`

Validation verdict for a `WriteResponse` scratch payload.

```
{
  "schema_version": 1,
  "ok": bool,
  "page_id": str,
  "response_path": str,
  "errors": [{"path": str, "code": str, "message": str}],
  "structural_checks": {...},   // per-check pass/fail map
  "checked_at": ISO8601
}
```

Created by: `wikify validate write`. Read by: the workflow skill.

## Bundle artifacts

### `<bundle>/articles/<id>.md` and `<bundle>/people/<id>.md`

Wikipedia-style page markdown with YAML frontmatter. The subdirectory
is determined by the page `kind`: `article` → `articles/`, `person` →
`people/` (enforced by `src/wikify/store/wiki_files.py::write_page`).

Frontmatter required fields: `id, kind (article|person), title, aliases, created_at`.
Body rules: see `write-constraints.md`. Citation format: see `citation-format.md`.
No `schema_version` field — the format IS the schema; breaking changes come
through the render pipeline, not the page file.

Owning CLI: `wikify bundle commit-page`.

### `<bundle>/_index.json`

Generated index over `pages/`. Shape defined by `src/wikify/store/wiki_index.py`.
Owning command: `wikify bundle commit-page` (rebuilds on each commit).
`schema_version` to be added when the index format is first mutated.

### `<bundle>/_wiki_graph.json`

Wiki graph of citation edges between pages. Shape defined by
`src/wikify/distill/write_runner.py::rebuild_wiki_graph`. Owning command:
`wikify bundle commit-page`. `schema_version` to be added when the format is
first mutated.

## Telemetry artifacts

### `<bundle>/_run.json`

Run snapshot flushed on `wikify session close` (and legacy `run_baseline()`).

**Skill-path writer** (`src/wikify/session.py::write_run_snapshot`): emits
`schema_version: 1` plus full legacy field-set parity. Session-derived
core fields: `session_id`, `strategy`, `mode`, `iteration`, `status`,
`bundle_root`, `corpus_root`, `created_at`, `closed_at`, `timestamp_utc`,
`budget_target_haiku_eq`, `stages`, `config`, `pages`, `n_pages_committed`,
`n_pages_failed`, `page_counts`, `telemetry_paths`. Baseline overlay
fields: `seed_doc_ids`, `seed_chunks_read`, `evidence_chunks_read`,
`split_initial`, `seed_extract_budget`, `baseline_write_fraction`,
`min_evidence_chunks`, `skipped_thin_pages`, `n_pages_written`,
`write_rejections`. Meter-derived fields (read from `_calls.jsonl`,
shape matches legacy `CostMeter.snapshot()`): `run_id`,
`budget_used_haiku_eq`, `wall_seconds`, `by_role`, `by_tier`, `context`
(`used_max`, `used_mean`, `headroom_min`, `headroom_mean`), `calls`
(integer count), `cache_hit_rate`.

**Legacy writer**: previously `run_baseline` in the legacy baselines
pipeline, retired in the skill-pivot. The skill-path writer is now the
only producer of `_run.json`. Field-set parity with the legacy writer
was achieved before deletion (overlay + meter sides), so existing
downstream consumers (`wikify html`, `wikify eval`) read the
skill-path bundle without changes.

Owning command: `wikify session close`.

### `<bundle>/_calls.jsonl`

Append-only model-call log. One JSON record per line with the
`src/wikify/meter.py::CallRecord` field-set:

```
role, tier, input_tokens, output_tokens, context_used, context_cap,
wall_seconds, cache_hit, prompt_hash, haiku_eq
```

No `schema_version` per-line; the whole file's stability is gated on the
`CallRecord` dataclass shape.

**Skill-path writers**:

- `wikify meter record` — explicit emission; used by workflows to log
  extract/query/orchestrate calls the subagent performs. Callers MUST
  NOT use this command for the write call — `wikify bundle commit-page`
  records that automatically. Double-recording is not deduplicated.
- `wikify bundle commit-page` — auto-records the write call using the
  `WriteResponse.tokens_in` / `tokens_out` fields on successful commit.
  Refuses to record negative tokens, tokens beyond the declared
  `context_cap`, or projected spend over 1.05× the session budget
  target.

Both writers bump `session.budget.haiku_eq_spent` (stored as `float`)
under the session lock and honor the legacy `1.05 × budget_target`
hard-abort gate — a projected overshoot exits the CLI with a
structured `budget_exceeded` error on stderr and a non-zero exit code.
`wikify session close` reads this file and folds it into the
`_run.json` snapshot (see above) in the exact shape legacy
`CostMeter.snapshot()` emits (`budget_used_haiku_eq`, `wall_seconds`,
`by_role`, `by_tier`, `context`, `calls` count, `cache_hit_rate`).
Unknown `role` strings in `_calls.jsonl` are rejected at aggregation
(`UnknownRoleError`); the legitimate role set is the `Role` enum in
`src/wikify/types.py`.

## Corpus artifacts (read-only)

- Raw papers — arbitrary source material.
- Parsed chunks — produced by `wikify ingest`.
- Knowledge graph export — produced by `wikify ingest`.

The agent does not write these. Inspection-only.

## Invariants

- The agent does not invent new file types mid-workflow.
- The agent does not hand-edit canonical session fields with raw shell writes.
- Promotion from scratch to bundle happens only through a CLI command.
- Token-light outputs are the default; `--full` flags opt into heavy payloads.
