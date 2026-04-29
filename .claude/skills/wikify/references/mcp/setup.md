# MCP setup

Wikify ships a stdio MCP server that exposes the corpus surface (and,
in later phases, wiki, bundle, and ingest tools). Configure it once
per project via `.mcp.json`.

## .mcp.json

```json
{
  "mcpServers": {
    "wikify": {
      "command": "wikify",
      "args": ["mcp", "serve"],
      "env": {
        "WIKIFY_CORPUS": "data/corpora/<my-corpus>",
        "WIKIFY_BUNDLE": "data/wikis/<my-bundle>"
      }
    }
  }
}
```

One server entry per `(corpus, bundle)` pair. Multi-corpus comparison
uses multiple entries (e.g. `wikify-ald`, `wikify-cvd`).

## Binding modes

- **Launch-time (preferred).** `WIKIFY_CORPUS` and `WIKIFY_BUNDLE` are
  read once at server boot. This is the path Claude Code uses when it
  launches the server from `.mcp.json`.
- **Runtime.** Call `mcp__wikify__context_set(corpus_path=...,
  bundle_path=...)` mid-session to rebind. Useful when a workflow
  decides which corpus to explore after the session has started.
  `clear_bundle=True` drops the bundle binding without touching the
  corpus binding.

`mcp__wikify__context_show()` returns the current binding.

## Verifying the server

After editing `.mcp.json`, reload Claude Code and confirm the seven
Phase 1 tools are visible:

- `mcp__wikify__context_show`
- `mcp__wikify__context_set`
- `mcp__wikify__corpus_find`
- `mcp__wikify__corpus_traverse`
- `mcp__wikify__corpus_show`
- `mcp__wikify__corpus_sample`
- `mcp__wikify__corpus_schema`

If they are missing, see `fallback.md` and switch to the CLI surface
documented in `wikify-search-corpus`.

## Troubleshooting

- **Server fails to start.** Run `wikify mcp serve` from a shell with
  the same env vars. Stderr from the Python process appears in
  Claude Code's MCP logs.
- **`no_corpus_bound` on every call.** Either `WIKIFY_CORPUS` was not
  set in `.mcp.json`, or the path does not exist. Call
  `context_set(corpus_path=...)` to rebind.
- **`bad_context: corpus path is not a directory`.** Path mistyped or
  not yet ingested. Verify with `wikify corpus check <path>`.
- **Tools work but resources fail.** Confirm the resource URI matches
  the templates in `resources.md` (e.g. figures use the two-segment
  `figures/{doc_short}/{stem}` form, not a single segment).
