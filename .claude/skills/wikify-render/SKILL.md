---
name: wikify-render
description: Render a Wikify bundle's committed wiki to a static HTML site. Use after one or more `wiki commit` operations to produce a browsable site under `<bundle>/derived/site/` (or a custom `--out` directory).
allowed-tools: Bash(wikify render *)
---

# wikify-render

Static HTML site generator over a bundle's committed wiki. The
render layer is deterministic and read-only: it consumes the same
`wiki/articles/` and `wiki/people/` files that `wiki commit` writes,
plus the `derived/*` projections, and emits a complete HTML tree.

## Command

```
wikify render --bundle <b> --format html [--out <dir>] [--corpus <c>]
              [--output-format text|json]
```

The flag is `--bundle`, not `--run`. `--corpus` lets the renderer
stage figures from the ingest tree; when omitted, the corpus path
recorded in `run/state.json` is used. `--out` defaults to
`<bundle>/derived/site/`.

## Inputs

- `--bundle <b>` — bundle to render.
- `--format html` — only `html` is implemented.
- `--out <dir>` — output directory; created if missing.
- `--corpus <c>` — optional override; falls back to `state.json`.

## Outputs

- A complete HTML site under `--out` (default
  `<bundle>/derived/site/`): per-page HTML, an index, and any staged
  figures referenced by committed pages.

## When to use

- After committing one or more pages to inspect the wiki visually.
- As a post-step in `wikify-baseline` or `wikify-render-eval` to
  ship a snapshot.
- For QA on figure placement and citation rendering.

## Notes

- Render does not mutate `wiki/` or `run/state.json`. It is safe to
  run any number of times.
- If you have committed pages but `derived/index.json` is stale,
  run `wikify wiki build indexes` first; render reads the index.

## References

- [schemas.md](../wikify/references/schemas.md) — `derived/site/`
  output and the wiki page schema the renderer consumes.
- [cli-tool-surface.md](../wikify/references/cli-tool-surface.md) —
  full grammar.
