# Wiki CLI Patterns

The wiki is the committed, human-facing output. Use the wiki CLI to
inspect pages and projections without mutating work state.

## Common Commands

```bash
wikify wiki list [articles|people|files] [--run <bundle>] [--format auto|quiet|json]
wikify wiki find "<query>" [--run <bundle>] [--top-k N] [--text] \
    [--format auto|quiet|compact|json]
wikify wiki show <handle> [--run <bundle>] [--full] [--format text|json]
wikify wiki traverse <handle> --to <relation> [--run <bundle>] \
    [--rank n_links|n_evidence] [--top-k N] \
    [--format auto|quiet|compact|json]
wikify wiki repl [--run <bundle>]
wikify wiki check [--run <bundle>] [--format text|json]
```

Projection and commit commands belong to `bundle`, not this
read-only search skill.

`kind=data` artifacts are not wiki-graph nodes. A `wiki find` /
`wiki show` / `wiki traverse` on a data table returns
`error="page_not_found"` by design — inspect it through the `data`
noun (`wikify data list`, `wikify data show <claim_id>`,
`wikify data query`, `wikify data list-artifacts`), not the wiki tools.

## Handles

- A wiki handle is `page:<slug>` or just `<slug>`. Slugs are natural
  Wikipedia-style titles (e.g., `Atomic Layer Deposition`).
- Exact slug match wins. If no exact match, the CLI accepts a
  **case-insensitive unique prefix** (e.g., `atomic layer dep`
  resolves to `Atomic Layer Deposition`).
- Ambiguous prefixes return an error listing the candidates.
- A relative path like `wiki/articles/Photocatalysis.md` also works.

## Output Formats

- `quiet`    one handle per line; nothing else. Pipe-safe.
- `compact`  tab-separated columns (default when stdout is a TTY).
- `json`     existing JSON shape, for tooling.
- `auto`     compact when stdout is a TTY, quiet when piped.

### Compact Column Meanings

| Command / mode | Columns |
|---|---|
| `find`                          | `kind` `page-handle` `snippet` |
| `traverse` page result          | `links=N` `ev=N` `page-handle` `title` |
| `traverse` evidence result      | `chunk-handle` `doc-handle` `quote` |

Where:

- `links=N` — number of outgoing wiki links from that page.
- `ev=N` — number of evidence entries attached to that page.
- `chunk-handle` / `doc-handle` from the `evidence` relation are the
  corpus-side handles, ready to pipe into `wikify corpus show`.

## Traverse Relations

For a page handle:

- `links`        pages this page links to (LINKS_TO outgoing)
- `linked-by`    pages that link to this page
- `co-evidence`  pages that share at least one evidence source doc
- `evidence`     evidence entries — emits `chunk:` handles, suitable
                  for piping into `wikify corpus show` or
                  `wikify corpus traverse`

Add `--rank n_links|n_evidence` to sort page-typed results by attribute.

## Query Shapes

- Exact title or alias lookup.
- Unique-prefix slug lookup for partial titles.
- Body text search with `--text`.
- Coverage inspection through `wiki check`.
- One-hop relationship inspection through `wiki traverse`.

## Interactive Session

Use `wikify wiki repl --run <bundle>` for iterative committed-page
inspection. The process keeps the page index warm and avoids repeating
`--run` on every command.

```text
list articles
find atomic layer deposition top=10
show "Atomic Layer Deposition" full
exit
```

## Examples

### List Pages Linked From One Page

```bash
wikify wiki traverse "Atomic Layer Deposition" --to links \
    --rank n_links --top-k 10 --run <bundle>
```

### Find Pages That Cite The Same Source Docs

```bash
wikify wiki traverse "Atomic Layer Deposition" --to co-evidence \
    --top-k 5 --run <bundle>
```

### Bridge A Wiki Page To Its Corpus Evidence

```bash
wikify wiki traverse "Atomic Layer Deposition" --to evidence \
    --format quiet --run <bundle> \
  | xargs -I {} wikify corpus show {} --corpus <c>
```

## Full Page Discipline

Prefer `wiki find` and compact `wiki show` first. Use `--full` only for
the one page the workflow needs to read closely.
