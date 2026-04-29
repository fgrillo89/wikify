# Fallback when MCP is not configured

The wikify CLI is a first-class sibling adapter, not legacy baggage.
Every MCP tool has a CLI equivalent in `tool-map.md`; both call the
same domain APIs.

## Detecting MCP availability

If `mcp__wikify__corpus_schema` is in the tool list, the server is
configured and bound. If it is not — or if a call returns
`no_corpus_bound` and `context_set` cannot find the corpus — fall back
to the CLI surface.

## CLI fallback steps

1. Set the corpus once per shell (or pin via `WIKIFY_CORPUS` in
   `.env`):

   ```bash
   export WIKIFY_CORPUS=data/corpora/<my-corpus>
   export WIKIFY_CLI_FORMAT=compact
   ```

2. Use the verbs documented in `wikify-search-corpus`:

   ```bash
   wikify corpus schema
   wikify corpus find "<query>" --top-k 8
   wikify corpus traverse doc:<short> --to cited-by
   wikify corpus show chunk:<short> --full
   wikify corpus sample --max 12
   ```

3. Pipe handles between commands using `--format quiet`. The CLI
   already handles cross-platform line endings.

## Why both surfaces exist

- Humans need `--help`, readable errors, and shell-friendly output.
- CI / scripts / non-MCP runtimes need a stable subprocess interface.
- The CLI catches packaging, env resolution, and exit-code regressions
  the MCP layer cannot see.

Skill docs should present MCP as preferred for repeated agent reads
within a session and CLI as the portable equivalent. Do not describe
either as deprecated.
