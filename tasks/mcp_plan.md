# MCP Server implementation plan for Wikify

Status: corpus tools are implemented on the MCP branch. This plan is
the current roadmap for extending that surface to wiki, bundle, and
mutation workflows.

## Position

MCP is the agent-native access layer for Wikify. It should make corpus
and wiki exploration faster, more structured, and easier to compose in
Claude Code without turning workflow strategy into Python.

The CLI remains first-class. The durable split is:

- Domain APIs are canonical for behavior: `corpus.queries`,
  `bundle.wiki.queries`, bundle/work/draft/run modules, ingest, render,
  and eval.
- CLI is the human, script, CI, debug, and fallback surface.
- MCP is the preferred agent transport when a server is configured.
- Skills remain the strategy layer. They decide exploration order,
  stopping criteria, budgets, retries, model tiers, and parallelism.

No new behavior should live only in the CLI adapter or only in the MCP
adapter. If a primitive matters, move it into the domain API and expose
it through both surfaces when relevant.

## Design goals

1. Expose the most expressive corpus query surface to the agent.
   Recursive graph traversal, sampling, ranking, filtering, evidence
   discovery, author/source/media lookup, and schema discovery are the
   core of Wikify.
2. Keep workflow strategy out of tools. MCP tools are deterministic
   operations; workflow skills compose them.
3. Use resources for addressable knowledge objects. Search and traverse
   tools should return handles, summaries, scores, and resource URIs;
   full content is fetched through resources when needed.
4. Keep response schemas lightweight. Return predictable envelopes and
   common fields, but do not overfit strict Pydantic-style contracts for
   every row shape.
5. Keep CLI and MCP readable together. Skills should document MCP calls
   next to CLI equivalents, not maintain separate strategy logic.

## Why MCP, not an HTTP daemon

The HTTP/curl daemon spike solved server-side latency but kept the
shell round trip: every agent call still spawned a Python interpreter
or required curl command construction. MCP removes that layer. Claude
Code can call `mcp__wikify__corpus_find(...)` as a native tool with
typed arguments and a long-lived warm process.

Additional wins:

- `.mcp.json` configures the server per project.
- Claude Code owns process lifecycle.
- Tool arguments avoid shell quoting.
- Responses can include resource links.
- One warm process can share corpus indexes, wiki graph, and bundle
  context.

Use stdio transport for Claude Code first. HTTP/SSE is out of scope
until a non-Claude consumer needs it.

## Server context

The natural server unit is one working context:

- one corpus, and optionally
- one bundle that records or implies that corpus.

This matches agent work: a session usually explores one corpus and one
wiki bundle at a time. Multi-corpus or multi-bundle comparisons should
use multiple MCP server entries in `.mcp.json`.

```json
{
  "mcpServers": {
    "wikify": {
      "command": "wikify",
      "args": ["mcp", "serve"],
      "env": {
        "WIKIFY_CORPUS": "data/corpora/ald_all_marker",
        "WIKIFY_BUNDLE": "data/wikis/ald-attempt-1"
      }
    }
  }
}
```

Binding modes:

- Launch-time binding: `WIKIFY_CORPUS` and `WIKIFY_BUNDLE` are read at
  server boot.
- Runtime binding: `context_set(corpus_path=..., bundle_path=...)`
  binds or rebinds explicitly.

Launch-time binding is the default. Runtime binding is a fallback for
sessions that choose a corpus or bundle mid-run.

## Tool surface principle

Do not mirror every CLI verb as a first pass. A large tool inventory
bloats the agent context and makes the surface harder to choose from.
Start with the high-frequency, high-leverage primitives and grow only
when a workflow repeatedly needs a missing operation.

The first MCP surface should be read-heavy:

- corpus query and graph traversal,
- corpus object retrieval,
- corpus sampling,
- corpus schema/discovery,
- wiki search, index, show, and traverse,
- bundle context/status inspection.

Bundle mutations, ingest, render, and eval should come later because
they need stronger locking, idempotency, dry-run behavior, progress
reporting, and structured failure handling.

## Core corpus tools

The corpus tools must cover all tasks currently taught by
`.claude/skills/wikify-search-corpus/references/corpus-cli-patterns.md`
and `corpus-graph-traversals.md`. This is the most important part of
the MCP plan.

### `corpus_find`

Expressive search over chunks, papers, and authors.

Inputs:

- `query`: optional text query.
- `by`: `chunk`, `paper`, or `author`.
- `rank`: `semantic`, `citation_count`, `pagerank`, `h_index`,
  `n_papers`, or other metrics advertised by `corpus_schema`.
- `text`: exact/text-search mode.
- `top_k`.
- optional filters as they become domain-backed: doc ids, author ids,
  year range, tags, section, source file, field, venue.
- optional output controls: include preview text, include best chunk,
  include matched chunk count, include media refs.

Rules:

- With no query plus a graph metric, rank the whole selected
  population.
- Reject invalid metric/population combinations with a clear error.
- Return compact rows with handles and resource URIs, not full blobs by
  default.

### `corpus_traverse`

Graph traversal from a handle to related objects.

Inputs:

- `source`: `doc:...`, `chunk:...`, `author:...`, `figure:...`, or
  `equation:...` as supported by the graph.
- `to`: relation name advertised by `corpus_schema`.
- `top_k`: `0` may mean unlimited only where the domain API supports
  it.
- `rank`: relation-compatible ranking metric.
- optional traversal controls: direction, depth, relation path, visited
  set, include paths, include edge metadata.

This tool is the agent workhorse for recursive exploration. It should
support patterns such as:

- paper -> cited-by -> papers -> chunks,
- paper -> references -> papers -> authors,
- chunk -> figures/equations -> source paper,
- author -> sources -> cited-by -> coauthors,
- sampled papers -> chunks -> evidence candidates.

The tool should return enough metadata to continue traversal without
requiring full object reads.

### `corpus_show`

Fetch one addressable corpus object by handle.

Inputs:

- `handle`.
- `full`: false by default.
- `include`: optional list such as `text`, `abstract`, `metadata`,
  `figures`, `equations`, `chunks`, `bibliography`.

The default should be preview-sized. Full text is explicit.

### `corpus_sample`

Query-free entry point selection.

Inputs:

- `population`: `docs`, `chunks`, or `authors` if supported.
- `strategy`: `diverse`, `top`, `random`, `stratified`, or other
  names advertised by `corpus_schema`.
- strategy parameters such as `max`, `pagerank_weight`, seed, year
  ranges, or strata.

Sampling is a primitive, not a workflow. A workflow decides whether the
sample is enough, which sampled items to read, and whether to iterate.

### `corpus_schema`

Self-describe the corpus query surface:

- handle kinds,
- node types,
- edge/relation names,
- allowed `find` populations,
- rank metrics and compatible populations,
- traversal relations and compatible source handle kinds,
- sampling strategies and their arguments,
- available filters.

The schema is an agent-facing discovery aid. Keep it readable and
compact.

### `context_show` and `context_set`

Show and update the current corpus/bundle binding:

- corpus path,
- bundle path,
- corpus manifest summary,
- wiki index availability,
- warm/cache status.

## Wiki tools

The wiki MCP surface should mirror the read-side needs of wiki skills,
with the wiki index as a first-class resource.

### `wiki_index`

Return committed wiki index information:

- article pages,
- person pages,
- redirects/aliases if present,
- link graph summary,
- stale/thin/missing projections if available,
- resource URIs for individual pages.

This gives the agent a cheap map of the current wiki before deciding
whether to search, show, traverse, or refine.

### `wiki_find`

Search committed wiki pages by title, body text, links, or page kind.
Return page handles/slugs, titles, kinds, score, summary, and resource
URI.

### `wiki_show`

Fetch one page by slug or id. Default to metadata and preview; full
Markdown is explicit.

### `wiki_traverse`

Traverse wiki graph relations such as links, backlinks, people,
evidence, and source chunks. This tool should not assume a mature wiki
exists. If the wiki is empty, return an empty result with a clear
`empty_wiki` note, not a failure.

### `wiki_schema`

Self-describe page kinds, traversal relations, and index fields.

## Resources

Resources are how agents should read full objects after a search or
traversal chooses a handle. Tools should return `resource_uri` fields
wherever possible.

Initial resources:

- `wikify://corpus/docs/{doc_handle}`
- `wikify://corpus/chunks/{chunk_handle}`
- `wikify://corpus/figures/{figure_handle}`
- `wikify://corpus/equations/{equation_handle}`
- `wikify://corpus/authors/{author_handle}`
- `wikify://wiki/index`
- `wikify://wiki/pages/{slug}`
- `wikify://wiki/people/{slug}`
- `wikify://bundle/status`
- `wikify://bundle/work/{concept_slug}`
- `wikify://schemas/corpus`
- `wikify://schemas/wiki`

Resource reads should be read-only and preview-oriented unless the URI
explicitly names a full object. Large resources can expose sections or
pagination later if needed.

## Lightweight response contract

Use a small common envelope instead of rigid per-tool schemas:

```json
{
  "ok": true,
  "kind": "corpus_find_result",
  "items": [
    {
      "handle": "doc:514791d621fa",
      "type": "doc",
      "title": "Atomic layer deposition ...",
      "score": 0.83,
      "rank": {"citation_count": 12, "pagerank": 0.0042},
      "resource_uri": "wikify://corpus/docs/doc:514791d621fa",
      "preview": "..."
    }
  ],
  "next": null,
  "notes": []
}
```

Guidelines:

- Keep `ok`, `kind`, `items`, `notes`, and `next` stable.
- Use common item fields when possible: `handle`, `type`, `title`,
  `score`, `rank`, `resource_uri`, `preview`, `meta`.
- Put shape-specific data under `meta` rather than forcing every row
  into one strict schema.
- Errors use the same envelope with `ok: false`, `code`, `message`,
  and optional `details`.

This gives the agent enough structure to compose calls without making
the API brittle.

## Skills and MCP

Skills remain the prompt/workflow layer. MCP is referenced in skills as
an access mode, not as a replacement for the skill.

Add shared references under `.claude/skills/wikify/references/mcp/`:

- `setup.md`: `.mcp.json`, launch-time binding, runtime binding,
  troubleshooting.
- `tool-map.md`: MCP tools and their CLI equivalents.
- `resources.md`: URI patterns and when to read resources.
- `fallback.md`: how to detect MCP availability and use CLI instead.

Capability skills should include a short "MCP mode" section:

- `wikify-search-corpus`: prefer `corpus_find`, `corpus_traverse`,
  `corpus_show`, `corpus_sample`, and `corpus_schema` for repeated
  reads; list CLI equivalents for fallback.
- `wikify-search-wiki`: prefer `wiki_index`, `wiki_find`,
  `wiki_show`, `wiki_traverse`, and `wiki_schema`.
- `wikify-bundle`: start with read-only context/status resources;
  mutating tools remain CLI-first until controlled MCP mutation tools
  exist.

Workflow skills should not duplicate MCP tool documentation. They
should say which capability skill to use and what decisions the
workflow owns. Example:

```text
Use wikify-search-corpus for evidence discovery. If MCP is configured,
use corpus_sample -> corpus_show/corpus_traverse -> corpus_find and
read selected resource URIs. Otherwise use the CLI equivalents from
the same capability skill.
```

Do not create MCP prompt templates for baseline/guided/refine in the
server. Those remain Claude skills because they are strategy and
orchestration, not deterministic backend operations.

## CLI relationship

The CLI remains first-class for four reasons:

1. Humans need an inspectable interface with `--help`, `schema`,
   readable errors, and shell-friendly output.
2. Tests and CI need a process boundary that catches packaging,
   argument parsing, env resolution, exit code, and format regressions.
3. Scripts, notebooks, and non-MCP agent runtimes still need a stable
   automation surface.
4. CLI commands are the fallback path when Claude Code has no MCP
   server configured.

The CLI should get thinner over time without being replaced. Re-ranking,
traversal, sampling, object lookup, and validation logic should live in
domain APIs. The CLI formats and exits; MCP returns envelopes and
resources. Both wrap the same implementation.

Skill docs should therefore show MCP as preferred for repeated agent
queries and CLI as the portable equivalent.

## Implementation sketch

Use the official Python MCP SDK and add one CLI verb:

```bash
wikify mcp serve
```

Skeleton:

```python
from pathlib import Path
import os

from mcp.server import Server
from mcp.server.stdio import stdio_server

from wikify.api import Bundle, Corpus
from wikify.corpus import queries

srv = Server("wikify")
_corpus: Corpus | None = None
_bundle: Bundle | None = None


@srv.tool()
async def corpus_find(
    query: str = "",
    by: str = "chunk",
    rank: str = "semantic",
    top_k: int = 8,
    text: bool = False,
) -> dict:
    corpus = require_corpus()
    rows = queries.find(corpus, query=query, by=by, rank=rank, top_k=top_k, text=text)
    return envelope("corpus_find_result", rows)


async def main() -> None:
    global _corpus, _bundle
    if "WIKIFY_CORPUS" in os.environ:
        _corpus = Corpus(root=Path(os.environ["WIKIFY_CORPUS"]))
    if "WIKIFY_BUNDLE" in os.environ:
        _bundle = Bundle.open(Path(os.environ["WIKIFY_BUNDLE"]))
    async with stdio_server() as (read, write):
        await srv.run(read, write, srv.create_initialization_options())
```

The actual implementation should not call CLI functions. It should call
the same domain helpers the CLI calls.

## Roadmap

### Completed foundation

- `corpus sample` is the query-free entry point primitive.
- Active docs and skills use the current sampling primitive.
- `corpus schema` describes find populations, rank compatibility,
  traversal relations, and sampling strategies.
- CLI query logic is centralized in `corpus.queries`.

### Current branch: corpus MCP and resources

Implemented surface:

- `wikify mcp serve`.
- `context_show`, `context_set`.
- `corpus_find`, `corpus_traverse`, `corpus_show`,
  `corpus_sample`, `corpus_schema`.
- corpus resources for docs, chunks, figures, equations, authors.
- tests proving parity with `corpus.queries`, not shell output.
- skill reference docs for MCP setup, tool map, resources, and fallback.

This surface should make recursive graph exploration pleasant enough for
real wiki-writing workflows.

### Next: wiki MCP and wiki resources

Ship:

- `wiki_index`, `wiki_find`, `wiki_show`, `wiki_traverse`,
  `wiki_schema`.
- `wikify://wiki/index`, page, and people resources.
- empty-wiki behavior that returns an empty result with a useful note.
- updates to `wikify-search-wiki`.

### Then: bundle read-side MCP

Ship read-only bundle context first:

- `bundle_status` or `work_show`.
- resources for bundle status and concept work cards.
- event summary/read helpers if needed by workflows.

Do not introduce mutations until lock, claim, dry-run, idempotency, and
error semantics are designed.

### Later: controlled mutations

Add mutating work/draft/wiki/run tools only where they improve agent
workflow materially:

- work add concept/evidence/feedback,
- claim/release/tend,
- draft build/check,
- wiki commit/build/check,
- run init/close/set where useful.

Requirements:

- existing lock/claim layer is enforced,
- mutations are idempotent where possible,
- errors use stable codes,
- high-risk operations support dry-run when meaningful.

### Last: ingest, render, eval

Add long-running and low-frequency operations last:

- ingest corpus/refresh/check/status,
- render,
- eval.

Ingest likely needs progress reporting or event polling. Keep it out of
the first MCP release.

## Testing strategy

Three layers:

1. Domain API tests remain the source of data-correctness.
2. CLI tests remain for argument parsing, env routing, output formats,
   exit codes, and human/script compatibility.
3. MCP tests verify tool registration, argument validation, envelope
   shape, resource reads, and parity with domain API calls.

Add workflow-level smoke tests after corpus and wiki MCP are stable:
one corpus exploration loop and one empty-wiki search/traverse loop.

## Out of scope

- HTTP/SSE transport.
- One multi-tenant server holding many corpora.
- Background warming of many bundles per server.
- Persistent cache files across MCP restarts.
- MCP-hosted workflow prompts. Workflows remain skills.

## Decision log

- Chose MCP SDK over FastAPI for the Claude Code path.
- Chose context-bound servers over one multi-tenant process.
- Chose expressive corpus tools over one-tool-per-CLI-verb mirroring.
- Chose resources for full object reads.
- Chose lightweight envelopes over rigid per-row schemas.
- Chose skills, not MCP prompts, for workflows.
- Chose CLI as first-class sibling adapter.
