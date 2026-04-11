---
name: wikify_simple/workflows/run-scripted
description: Run N iterations of deterministic distillation with fixed parameters, then render HTML.
---

# run-scripted

User-facing workflow: the user sets every knob up front, this skill runs the pipeline N times with create/refine semantics in a single Python process (corpus loaded once), then renders HTML and eval metrics.

## Inputs

| Parameter | Default | Description |
|---|---|---|
| `corpus` | `data/corpus` | Path to an already-ingested corpus. |
| `bundle` | `data/wikis/<run_id>` | Bundle output directory. |
| `strategy` | `M` | E (breadth) / M (mixed, headline) / X (depth). |
| `policy` | `rule_policy` | `rule_policy` (deterministic sampler) — this workflow does NOT use llm_policy. |
| `binding` | `heuristic` | `fake` / `heuristic` / `file_dispatch`. Only `file_dispatch` needs serve-dispatch running. |
| `iterations` | `1` | How many iterations to run. First is `create`, rest are `refine`. |
| `budget_per_iteration` | `50000` | Haiku-equivalent tokens per iteration. Accepts 50000, 50k, 1.5M, 1x, 3x. |
| `extract_tier` | `S` | S/M/L |
| `write_tier` | `M` | S/M/L |
| `edit_tier` | `M` | S/M/L |
| `compact_tier` | `S` | S/M/L |
| `exploit_fraction` | strategy default | 0..1 |
| `seed` | `0` | RNG seed for the first iteration (increments per iteration). |
| `field` | auto-detect | Writer field guide. |
| `artifact` | `wiki_article` | Writer artifact template (`wiki_article` or `wiki_person`). |
| `render_html` | `true` | Render HTML after the final iteration. |
| `run_eval` | `true` | Run eval after the final iteration. |

Orchestrator tier is locked at L (opus) — not exposed here because rule_policy does not use the orchestrator.

## Steps
1. Verify `corpus` exists (`ls <corpus>/docs` should be non-empty). If missing, tell the user to run `wikify-simple ingest` first.
2. Pick an explicit bundle path. If the user gave `bundle`, use it directly. Otherwise build one: `BUNDLE=data/wikis/scripted_<strategy>_<timestamp>`.
3. If `binding == file_dispatch`, verify `WIKIFY_SIMPLE_ALLOW_NETWORK=1` is set and ask the user to run `wikify_simple/runtime/serve-dispatch` in a parallel Claude session.
4. Run the campaign in one process:
   ```
   uv run python -m wikify_simple.cli campaign \
     --strategy {strategy} --policy {policy} --binding {binding} \
     --budget {budget_per_iteration} --iterations {iterations} --seed {seed} \
     --extract-tier {extract_tier} --write-tier {write_tier} \
     --edit-tier {edit_tier} --compact-tier {compact_tier} \
     [--exploit-fraction {exploit_fraction}] \
     --corpus {corpus} --bundle $BUNDLE \
     [--field {field}] --artifact {artifact}
   ```
   The `--bundle` flag is required for `campaign`; all iterations write to the same path.
5. Wait for the Python process to exit. If exit code != 0, stop and report.
6. If `render_html`, run `uv run python -m wikify_simple.cli html --bundle $BUNDLE`.
7. If `run_eval`, run `uv run python -m wikify_simple.cli eval --bundle $BUNDLE --corpus {corpus}`.
8. Report the final bundle path, HTML output path, one-line summary from `_metrics.json`, and any `write_rejections` from `_run.json`.

## Outputs
- Bundle at `$BUNDLE/` (markdown pages + frontmatter + `_index.json`, `_run.json`, `_calls.jsonl`)
- HTML site at `$BUNDLE/_html/` (if rendered)
- Metrics at `$BUNDLE/_metrics.json` and `_metrics.md` (if eval ran)

## Failure modes
- Corpus missing → abort with a message asking the user to ingest first.
- Process dies mid-campaign → bundle is in a partially-refined state (coverage memory is saved after each iteration). The user can re-invoke with `--iterations 1 --bundle $BUNDLE` to continue one more refine pass, or delete `$BUNDLE` to start over.
- `file_dispatch` binding with no serve-dispatch running → the Python harness will time out after 600s per dispatch. Tell the user to start serve-dispatch.

## Notes
- For fast iteration (10-30s per iteration), use `binding=heuristic`. Everything stays in-process.
- For quality, use `binding=file_dispatch`. The user must also start `/wikify_simple/runtime/serve-dispatch` in a second Claude session.
- The corpus (chunks, vectors, graph) is loaded exactly once regardless of `--iterations`. ExtractCache is also reused across iterations so in-process cache hits are free after iteration 1.
