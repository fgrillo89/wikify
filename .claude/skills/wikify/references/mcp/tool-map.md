# MCP tool map

MCP tools and their CLI equivalents. Both adapters call into the same
domain APIs (`wikify.corpus.queries` for Phase 1); behaviour and
validation rules match exactly.

For argument enumeration, call `mcp__wikify__corpus_schema` instead of
listing flags here. The schema is the single source of truth and is
kept in step with the underlying primitives.

## Phase 1 — corpus

| MCP tool                          | CLI equivalent                              |
|-----------------------------------|---------------------------------------------|
| `mcp__wikify__context_show`       | `wikify mcp serve --corpus ... --bundle ...` (launch-time) |
| `mcp__wikify__context_set`        | (CLI re-runs `wikify mcp serve` with new env) |
| `mcp__wikify__corpus_find`        | `wikify corpus find`                        |
| `mcp__wikify__corpus_traverse`    | `wikify corpus traverse`                    |
| `mcp__wikify__corpus_show`        | `wikify corpus show`                        |
| `mcp__wikify__corpus_sample`      | `wikify corpus sample`                      |
| `mcp__wikify__corpus_schema`      | `wikify corpus schema`                      |

`corpus_find`, `corpus_traverse`, and `corpus_show` validation rules
mirror the CLI exactly: `--by chunk --rank citation_count` is rejected
on both surfaces, ambiguous handles return `ambiguous_handle` with a
match list, and so on.

## Response shape

Tools return a lightweight envelope::

    {
      "ok": true,
      "kind": "<tool-specific kind>",
      "items": [...],
      "notes": [...],
      "next": null
    }

Errors share the envelope::

    {
      "ok": false,
      "code": "<stable code>",
      "message": "<human text>",
      "details": {...} | null
    }

Common item fields: `handle`, `type`, `title`, `score`, `rank`
(metric dict), `resource_uri`, `preview`, `meta`. Shape-specific data
lives under `meta` so the surface stays loose.

`resource_uri` is the canonical handle for follow-up reads — see
`resources.md` for the URI patterns and when to fetch a resource
versus calling another tool.
