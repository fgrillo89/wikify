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
- Skills assert `schema_version == N` before they read an artifact. If the version is ahead, fail fast with `SchemaVersionMismatch`; do not attempt forward-compatibility in prose.
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
- `status: "active" | "closed" | "failed"`
- `created_at, updated_at: ISO8601`
- `budget: {haiku_eq_target: int, haiku_eq_spent: int}`
- `stages: {seed_selection, extract, write}` — each `{status: pending|running|done|failed, started_at: str|null, finished_at: str|null}`
- `pages: [{page_id, status: planned|drafted|validated|committed|failed, draft_path: str|null, validation_path: str|null}]`
- `config: {baseline_write_fraction, abstract_fraction, top_k, default_tiers}`
- `telemetry_paths: {run_path, calls_path}`

Reserved (not required in v1, added in later strategies):

- `stopping_criteria`, `kpi_snapshot`, `acceptance_policy`, `iteration_index`,
  `recent_gains`, `active_page_id`, `active_chunk_ids`, `transcript_path`, `lock`.

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
Source of truth: `src/wikify/schema.py::WriteRequest`. Carries `schema_version: 1`
added alongside the existing fields.

Created by: `wikify draft write-request`. Read by: the write subagent.

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

### `<bundle>/pages/<id>.md`

Wikipedia-style page markdown with YAML frontmatter.

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
Current field-set is the merge gate for schema parity; see `src/wikify/meter.py`
and `src/wikify/baselines/pipeline.py` for the current keys. No `schema_version`
field yet — will be added when session closure takes over as the only writer.

Owning command: `wikify session close` (skill-driven path);
`baselines.pipeline.run_baseline` (legacy path).

### `<bundle>/_calls.jsonl`

Append-only model-call log. One JSON record per line with the
`src/wikify/meter.py::CallRecord` field-set:

```
role, tier, input_tokens, output_tokens, context_used, context_cap,
wall_seconds, cache_hit, prompt_hash, haiku_eq
```

No `schema_version` per-line; the whole file's stability is gated on the
`CallRecord` dataclass shape. Owning command: whichever command calls a model
(via subagent, the record is emitted by the skill after the subagent returns).

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
