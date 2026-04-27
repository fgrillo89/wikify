# Wiki CLI Patterns

The wiki is the committed, human-facing output. Use the wiki CLI to
inspect pages and projections without mutating work state.

## Common Commands

```bash
wikify wiki list [articles|people|files] [--run <bundle>] [--format text|json]
wikify wiki find "<query>" [--run <bundle>] [--top-k N] [--text]
wikify wiki show <handle> [--run <bundle>] [--full] [--format text|json]
wikify wiki repl [--run <bundle>]
wikify wiki check [--run <bundle>] [--format text|json]
```

Projection and commit commands belong to `wikify-bundle`, not this
read-only search skill.

## Query Shapes

- Exact title or alias lookup.
- Body text search with `--text`.
- Committed page text search.
- Coverage inspection through `wiki check`.
- Relationship inspection when exposed by CLI flags.

## Interactive Session

Use `wikify wiki repl --run <bundle>` for iterative committed-page
inspection. The process keeps the page index warm and avoids repeating
`--run` on every command.

```text
list articles
find atomic layer deposition top=10
show atomic-layer-deposition full
exit
```

## Full Page Discipline

Prefer `wiki find` and compact `wiki show` first. Use `--full` only for
the one page the workflow needs to read closely.
