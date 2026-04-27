---
name: wikify-wiki
description: Committed wiki layer for a Wikify bundle. Use to list/find/show committed pages, rebuild derived projections (`wiki build`), check the wiki for sanity, or promote a validated draft to `wiki/articles/` or `wiki/people/` (`wiki commit`).
allowed-tools: Bash(wikify wiki *)
---

# wikify-wiki

The wiki layer is the canonical, human-facing output. Pages live
under `<bundle>/wiki/articles/<slug>.md` and
`<bundle>/wiki/people/<slug>.md`. Derived projections (index, graph,
vectors) live under `<bundle>/derived/`. `wiki commit` is the gate
that promotes a validated `response.json` into the wiki tree.

## Commands

```
wikify wiki list  [articles|people|files] [--run <b>] [--format text|json]
wikify wiki find  "<query>" [--run <b>] [--top-k N] [--text] [--format text|json]
wikify wiki show  <handle>   [--run <b>] [--full] [--format text|json]
wikify wiki build indexes|graph|vectors  [--run <b>] [--format text|json]
wikify wiki check                        [--run <b>] [--format text|json]
wikify wiki commit <concept>             [--run <b>] [--ensure-projections] [--format text|json]
```

## Inputs

- `--run <b>` — bundle override; default is CWD.
- `<concept>` — concept slug to commit (must have a valid
  `validation.json`).
- `<handle>` — page handle for `show`.

## Outputs

- `wiki/articles/<slug>.md` or `wiki/people/<slug>.md` — committed
  page with YAML frontmatter + Wikipedia-style body.
- `derived/index.json` — page list (`wiki build indexes`).
- `derived/graph.json` — cite-edge wiki graph (`wiki build graph`).
- `derived/vectors.npz` — per-page embeddings (`wiki build vectors`).
- `page_committed` events. Per-attempt artifacts
  (`draft.json`/`response.json`/`validation.json`) are gc'd post-commit.

## When to use

- After a successful `draft check`: commit the page.
- Before serving search/answer queries: `wiki build indexes/graph/vectors`
  if any pages have been committed since the last projection.
- For ad-hoc inspection: `wiki list` (one page per line),
  `wiki find "<q>" --text` (substring grep over committed pages),
  `wiki show <handle> --full` (full page text).
- For sanity: `wiki check` reports counts and projection freshness.

## Commit gate

`wiki commit <concept>` refuses to promote unless `draft.json`,
`response.json`, and `validation.json` are all present and
`validation.ok` is true. The gate re-runs the verbatim quote check
under the run lock to guarantee no mid-flight evidence drift can
sneak a fabricated quote into `wiki/`.

## Exit codes

- 1 if commit gate fails (validation absent or `ok == false`).
- 2 if the run lock is held by another owner.

## References

- [atoms.md](../wikify/references/atoms.md) — `wiki commit` pre/post-conditions.
- [schemas.md](../wikify/references/schemas.md) — wiki page frontmatter
  + derived projections.
- [wiki-graph.md](../wikify/references/wiki-graph.md) — fluent wiki KG
  surfaced through `wiki find`.
- [write-constraints.md](../wikify/references/write-constraints.md) —
  what a committed page looks like.
- [citation-format.md](../wikify/references/citation-format.md) —
  marker / definition grammar re-checked at commit.
