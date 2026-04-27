---
name: wikify-eval
description: Compute evaluation metrics over a Wikify bundle's committed wiki. Use to score graph shape, figure references, page counts, and (with `--corpus`) coverage and grounding metrics. Writes `derived/eval.json` by default.
allowed-tools: Bash(wikify eval *)
---

# wikify-eval

Metrics over a committed wiki. Corpus-free metrics (graph shape,
figure references, page counts) always run. M1 coverage residual and
M6 grounding require `--corpus`. M5 trace replay reads
`run/events.jsonl`.

## Command

```
wikify eval --bundle <b> [--corpus <c>] [--report <path>] [--format text|json]
```

The flag is `--bundle`, not `--run`. `--report` defaults to
`<bundle>/derived/eval.json`.

## Inputs

- `--bundle <b>` — bundle to evaluate.
- `--corpus <c>` — optional. When supplied, M1 / M6 / GT-C run; when
  omitted, those metrics are reported as `unavailable` (not faked).
- `--report <path>` — alternate report path.

## Outputs

- `derived/eval.json` (or `--report` path) with
  `schema_version`, page counts, `g_evidence` and `g_links` modularity
  + spectral gap, figure-reference rates, and (when `--corpus` is
  set) coverage and grounding metrics.
- Terse text summary on stdout (or full JSON via `--format json`).

## When to use

- After a workflow finishes to score the resulting wiki.
- During strategy comparison runs: bundle-A vs bundle-B vs bundle-C
  on the same corpus.
- For telemetry parity checks against checked-in golden fixtures.

## Notes

- Eval never mutates the bundle. Re-running over the same bundle
  produces the same numbers.
- M5 trace metrics read `run/events.jsonl` directly.

## References

- [schemas.md](../wikify/references/schemas.md) — `derived/eval.json`
  schema.
- [cli-tool-surface.md](../wikify/references/cli-tool-surface.md) —
  full grammar.
