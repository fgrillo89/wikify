# CLI Grammar

Eight workflow nouns are the stable file/state grammar:

```text
wikify corpus
wikify run
wikify work
wikify data
wikify draft
wikify wiki
wikify render
wikify eval
```

The MCP server control noun is separate from the workflow grammar:

```text
wikify mcp serve
```

The acquisition noun stages external documents for `corpus build`:

```text
wikify arxiv scout    "<query>" [--max 200]
wikify arxiv identify --category cs.LG [--category cs.AI] [--set <raw>] --out <dir>
wikify arxiv download --out <dir> [--concurrency 4] [--rate 4.0]
wikify arxiv status   --out <dir>
```

`arxiv` exhaustively harvests every paper in one or more arXiv categories
into a staging directory. Harvest is set-based (categories), not free-text;
`scout` bridges from a topic in words to the categories to harvest:

- `scout "<query>"` samples the top hits of a free-text Query-API search
  (`all:`, `ti:`, `abs:`, `cat:` field prefixes) and prints the
  distribution of their primary categories, with the matching setSpec and a
  ready-to-run `identify` suggestion. It is a discovery aid, not an
  exhaustive lister (the Query API caps at 30k). One request.

The exhaustive harvest then runs in two resumable phases:

- `identify` walks the complete OAI-PMH record set(s) via `resumptionToken`,
  serially (1 request / 3 s, honoring `503 Retry-After`). It writes one
  record per paper to `<dir>/manifest.jsonl` and the resume cursor to
  `<dir>/harvest_state.json`. `--category cs.LG` maps to `cs:cs:LG`;
  physics-group categories map correctly too (`cond-mat.mtrl-sci` ->
  `physics:cond-mat:mtrl-sci`). An unrecognized archive errors with
  `unknown_category` (pass the exact setSpec via `--set`). Re-running in a
  dir whose `harvest_state.json` was created for *different* categories
  errors with `state_mismatch` rather than silently harvesting the wrong
  set; use a fresh `--out` or the same categories to resume.
- `download` fetches each pending PDF from `export.arxiv.org/pdf/<id>` (the
  host arXiv sets aside for programmatic access) concurrently, capped by
  `--concurrency` and `--rate` (default ~4 req/s, arXiv's PDF-friendly
  rate). Transient 429/503 responses back off and retry. PDFs already on
  disk are skipped, so re-running resumes after an interruption. It exits
  non-zero with `download_incomplete` if any PDF fails (so automation does
  not ingest a partial corpus); pass `--allow-partial` to exit 0 anyway.
  The failed list is always in the output for resume.

The two phases use different rate regimes by design: `identify`/`scout` hit
the metadata APIs, capped at 1 request / 3 s on a single connection (arXiv
ToU); `download` hits the PDF host, which tolerates ~4 req/s. Set
`WIKIFY_CONTACT_EMAIL` to add a contact to the User-Agent (arXiv requests
this for programmatic use).
- `status` tallies the manifest (`done` / `pending` / `failed`) by on-disk
  presence; useful before/after resuming a long run.

`manifest.jsonl` / `harvest_state.json` are not ingested file types, so
`corpus build <dir> --out <corpus>` enumerates the staged PDFs and ignores
them. Each manifest record carries the rich arXiv metadata (id, title,
authors, abstract, categories, dates, doi) for future ingest wiring.

The `data` noun is the factual-data claim store and the `kind=data`
artifact tables:

```text
wikify data add <records.jsonl> --run <bundle> --corpus <corpus> [--keep-rejected]
wikify data list | show | query | coverage --run <bundle>
wikify data consolidate | commit | rebuild | list-artifacts --run <bundle>
```

`data add` is the verification gate: it reads a JSONL of staged points
(each a `subject` / `property` / `value_text` / `doc_id` /
`grounding_quote` / `chunk_id` record), resolves each point's `chunk_id`,
checks the value against the chunk text, and stores or rejects the point.
**Each staged point's `chunk_id` must be a resolvable id** — use the bare
short (`5f92b0...`) or the full canonical chunk id, NOT the
`chunk:`-prefixed handle that `corpus show` / `corpus find` and the MCP
corpus tools print. A `chunk:`-prefixed `chunk_id` does not resolve
through the `data add` resolver; the point is silently rejected and
dropped (pass `--keep-rejected` to retain rejects for inspection). Strip
the `chunk:` prefix before staging.

`kind=data` artifacts live in a store separate from the wiki page graph
(`<bundle>/claims.db`, not `wiki.db`). They render and appear in
navigation, but `wiki show` / `wiki traverse` / `wiki find` return
`page_not_found` for a data table — this is expected, not an error. The
round-trip surface for data artifacts is the `data` noun (`data show`,
`data query`, `data list-artifacts`); do not retry on the wiki side.

Common verbs:

```text
init, show, list, find, traverse, schema, repl, add, set, build, check, commit, tend, close
navigation-context, apply-navigation
```

`wiki navigation-context` writes the compact organizer projection for committed
pages. It includes page summaries, cluster hints from links/shared evidence/text
overlap, existing navigation when available, and freshness deltas for new or
changed page ids. `wiki apply-navigation` validates the hierarchy and persists
the render-compatible navigation projection.

`traverse` walks one graph hop from a typed handle (corpus:
`doc:`/`chunk:`/`figure:`/`equation:`/`author:`; wiki: `page:` or
`category:`), emitting handles for further commands.

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
