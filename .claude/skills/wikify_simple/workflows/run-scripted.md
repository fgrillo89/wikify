---
name: wikify_simple/workflows/run-scripted
description: Run N iterations of deterministic distillation with fixed parameters, then render HTML.
---

# run-scripted

User-facing workflow: the user sets every knob up front, this skill loops the pipeline N times with create/refine semantics, then renders HTML and eval metrics.

## Inputs

| Parameter | Default | Description |
|---|---|---|
| `corpus` | `data/corpus` | Path to an already-ingested corpus. |
| `out` | `data/wikis/<run_id>` | Bundle output directory. |
| `strategy` | `M` | E (breadth) / M (mixed, headline) / X (depth). |
| `policy` | `rule_policy` | `rule_policy` (deterministic sampler) — this workflow does NOT use llm_policy. |
| `binding` | `heuristic` | `fake` / `heuristic` / `file_dispatch`. Only `file_dispatch` needs serve-dispatch running. |
| `iterations` | `1` | How many distill calls to make. First is `create`, rest are `refine`. |
| `budget_per_iteration` | `50000` | Haiku-equivalent tokens per iteration. Accepts 50000, 50k, 1.5M, 1x, 3x. |
| `extract_tier` | `S` | S/M/L |
| `write_tier` | `M` | S/M/L |
| `edit_tier` | `M` | S/M/L |
| `compact_tier` | `S` | S/M/L |
| `exploit_fraction` | strategy default | 0..1 |
| `seed` | `0` | RNG seed for the first iteration (increments per iteration). |
| `field` | auto-detect | Writer field guide. |
| `artifact` | `wiki_concept` | Writer artifact template. |
| `render_html` | `true` | Render HTML after the final iteration. |
| `run_eval` | `true` | Run eval after the final iteration. |

Orchestrator tier is locked at L (opus) — not exposed here because rule_policy does not use the orchestrator.

## Steps
1. Verify `corpus` exists (`ls <corpus>/docs` should be non-empty). If missing, tell the user to run `wikify-simple ingest` first.
2. Pick an explicit bundle path so every iteration writes to the same place. If the user gave `out`, use it directly. Otherwise build one: `BUNDLE=data/wikis/scripted_<strategy>_<timestamp>`.
3. If `binding == file_dispatch`, verify `WIKIFY_SIMPLE_ALLOW_NETWORK=1` is set and ask the user to run `wikify_simple/runtime/serve-dispatch` in a parallel Claude session.
4. For `i` in `1..iterations`:
   a. `iteration_op = "create" if i == 1 else "refine"`
   b. Run:
      ```
      uv run python -m wikify_simple.cli distill \
        --strategy {strategy} --policy {policy} --binding {binding} \
        --budget {budget_per_iteration} --seed {seed+i-1} \
        --extract-tier {extract_tier} --write-tier {write_tier} \
        --edit-tier {edit_tier} --compact-tier {compact_tier} \
        [--exploit-fraction {exploit_fraction}] \
        --iteration {iteration_op} \
        --corpus {corpus} --bundle $BUNDLE \
        [--field {field}] --artifact {artifact}
      ```
      The `--bundle` flag forces every iteration (create + refine) to write to the SAME path. Without it, `create` would stash into a timestamped subdirectory and subsequent refines would operate on an empty parent.
   c. Wait for the Python process to exit. If exit code != 0, stop and report.
5. If `render_html`, run `uv run python -m wikify_simple.cli html --bundle $BUNDLE`.
6. If `run_eval`, run `uv run python -m wikify_simple.cli eval --bundle $BUNDLE --corpus {corpus}`.
7. Report the final bundle path, HTML output path, one-line summary from `_metrics.json`, and any `write_rejections` from `_run.json`.

## Outputs
- Bundle at `$BUNDLE/` (markdown pages + frontmatter + `_index.json`, `_run.json`, `_calls.jsonl`)
- HTML site at `$BUNDLE/_html/` (if rendered)
- Metrics at `$BUNDLE/_metrics.json` and `_metrics.md` (if eval ran)

## Failure modes
- Corpus missing → abort with a message asking the user to ingest first.
- Iteration 1 succeeds but iteration 2 fails → bundle is in a partially-refined state; the user can re-invoke with `--iteration refine` to continue, or delete `{out}` to start over.
- `file_dispatch` binding with no serve-dispatch running → the Python harness will time out after 600s per dispatch. Tell the user to start serve-dispatch.

## Notes
- For fast iteration (10-30s per iteration), use `binding=heuristic`. Everything stays in-process.
- For quality, use `binding=file_dispatch`. The user must also start `/wikify_simple/runtime/serve-dispatch` in a second Claude session.
