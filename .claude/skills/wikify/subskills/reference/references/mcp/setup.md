# MCP setup

Wikify ships a stdio MCP server that exposes the corpus surface.
Configure it once per project via `.mcp.json` (copy `.mcp.json.example`
at the repo root and edit the corpus path).

## .mcp.json

```json
{
  "mcpServers": {
    "wikify": {
      "command": "uv",
      "args": ["run", "wikify", "mcp", "serve"],
      "env": {
        "WIKIFY_CORPUS": "data/corpora/<my-corpus>",
        "WIKIFY_BUNDLE": "data/wikis/<my-bundle>"
      }
    }
  }
}
```

`uv run` resolves the project from the working directory Claude Code sets
to the repo root, so no absolute path is needed. The bare `wikify`
console script is only on PATH after `uv tool install .`; `uv run` works
straight from a fresh `uv sync`.

One server entry per `(corpus, bundle)` pair. Multi-corpus comparison
uses multiple entries (e.g. `wikify-ald`, `wikify-cvd`).

## Binding modes

- **Launch-time (preferred).** `WIKIFY_CORPUS` and `WIKIFY_BUNDLE` are
  read once at server boot. If only `WIKIFY_BUNDLE` is set, the server
  reads the bundle's `run/state.json` and binds the recorded corpus.
  This is the path Claude Code uses when it launches the server from
  `.mcp.json`.
- **Runtime.** Call `mcp__wikify__context_set(corpus_path=...,
  bundle_path=...)` mid-session to rebind. Useful when a workflow
  decides which corpus to explore after the session has started.
  `clear_bundle=True` drops the bundle binding without touching the
  corpus binding.

`mcp__wikify__context_show()` returns the current binding.

## Verifying the server

After editing `.mcp.json`, reload Claude Code and confirm the wikify
tools are visible:

- `mcp__wikify__context_show`
- `mcp__wikify__context_set`
- `mcp__wikify__corpus_find`
- `mcp__wikify__corpus_traverse`
- `mcp__wikify__corpus_show`
- `mcp__wikify__corpus_sample`
- `mcp__wikify__corpus_schema`
- `mcp__wikify__corpus_image`

If they are missing, see `fallback.md` and switch to the CLI surface
documented in `search-corpus`.

## Troubleshooting

- **Server fails to start.** Run `uv run wikify mcp serve` from a shell
  with the same env vars. Stderr from the Python process appears in
  Claude Code's MCP logs.
- **`no_corpus_bound` on every call.** Neither `WIKIFY_CORPUS` nor a
  bundle with a valid recorded corpus was configured. Call
  `context_set(corpus_path=...)` to rebind.
- **`bad_context: corpus path is not a directory`.** Path mistyped or
  the corpus has not been ingested. Verify with
  `wikify corpus check <path>`.
- **Tools work but resources fail.** Confirm the resource URI matches
  the templates in `resources.md` (e.g. figures use the two-segment
  `figures/{doc_short}/{stem}` form, not a single segment).
