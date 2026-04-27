---
name: wikify-corpus
description: Build, refresh, and query a Wikify corpus. Use when ingesting a directory of source documents, refreshing derived corpus artifacts, or running list/find/show queries against an existing corpus. Read-only during a wiki run.
allowed-tools: Bash(wikify corpus *)
---

# wikify-corpus

The corpus is the authoritative evidence layer. It carries chunks,
vectors, the citation graph, doc markdown, and figure/equation
indexes. All commands that mutate the corpus are explicit
(`build` and `refresh`); everything else is read-only.

## Commands

```
wikify corpus build <source> --out <corpus>
                              [--mode additive|sync]
                              [--parser default|lite|marker|docling]
                              [--workers N] [--no-refresh]
wikify corpus refresh <corpus>
wikify corpus check    <corpus> [--format text|json]
wikify corpus list     docs|chunks|files [--corpus <c>] [--doc <d>]
wikify corpus find     "<query>" [--corpus <c>] [--top-k N] [--text]
wikify corpus find     --seed   [--corpus <c>] [--max N] [--pagerank-weight W]
wikify corpus show     <handle> [--corpus <c>] [--full]
```

Handles: `doc:<id>` and `chunk:<id>`. Default text output is one
handle per line for `list`; `score id doc preview` rows for `find`.

## Inputs

- A source directory of documents for `build`.
- A corpus directory (`--corpus`) for everything else.

## Outputs

- `<corpus>/` populated with chunks, vectors, the corpus knowledge
  graph, doc markdown, and image/equation indexes.
- Text or JSON to stdout for query commands.

## When to use

- Bootstrap a corpus from a directory of papers.
- Pick the seed-doc set for a baseline pass (`find --seed`).
- Pull semantic neighbors for a concept during evidence gathering
  (`find "<title>" --top-k <k>`).
- Pull the full text of a chunk for a writer subagent (`show chunk:<id>
  --full`).

## References

- [atoms.md](../wikify/references/atoms.md) — `corpus find --seed`,
  `corpus find "<query>"`, `work add evidence` pre/post-conditions.
- [cli-tool-surface.md](../wikify/references/cli-tool-surface.md) —
  full grammar.
- [knowledge-graph.md](../wikify/references/knowledge-graph.md) —
  fluent corpus KG used by the find verbs internally.
