# CLI Grammar

Seven nouns are the stable agent-facing grammar:

```text
wikify corpus
wikify run
wikify work
wikify draft
wikify wiki
wikify render
wikify eval
```

Common verbs:

```text
init, show, list, find, traverse, schema, repl, add, set, build, check, commit, tend, close
```

`traverse` walks one graph hop from a typed handle (corpus:
`doc:`/`chunk:`/`figure:`/`equation:`/`author:`; wiki: `page:`),
emitting handles for further commands.

`schema` self-describes the available node types, edge kinds, traverse
relations, and rank metrics for a given noun (`corpus schema`,
`wiki schema`). Run it once to learn the surface without grepping
source.

Most read commands also accept `--explain`, which prints the resolved
fluent-chain pseudocode (e.g.
`chunks().search('X', top_k=30).group_by_doc().top(3, by=citation_count)`)
without executing.

Corpus/wiki path resolution: `--corpus` / `--run` are optional. The
CLI checks the explicit flag, then `WIKIFY_CORPUS` / `WIKIFY_BUNDLE`
env vars, then walks up from cwd.

Use actual `--help` output as the source of truth for flags. Do not add
aspirational examples that the CLI does not support.
