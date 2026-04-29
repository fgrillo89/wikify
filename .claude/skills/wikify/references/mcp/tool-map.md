# MCP tool map

MCP tools and their CLI equivalents. Both adapters call into the same
domain APIs (`wikify.corpus.queries` today); behaviour and validation
rules match exactly.

For argument enumeration, call `mcp__wikify__corpus_schema` instead of
listing flags here. The schema is the single source of truth and is
kept in step with the underlying primitives.

## Corpus tools

| MCP tool                          | CLI equivalent                              |
|-----------------------------------|---------------------------------------------|
| `mcp__wikify__context_show`       | `wikify corpus check` (folded in: corpus health is part of the snapshot when bound) |
| `mcp__wikify__context_set`        | (CLI re-runs `wikify mcp serve` with new env) |
| `mcp__wikify__corpus_find`        | `wikify corpus find`                        |
| `mcp__wikify__corpus_traverse`    | `wikify corpus traverse`                    |
| `mcp__wikify__corpus_show`        | `wikify corpus show`                        |
| `mcp__wikify__corpus_sample`      | `wikify corpus sample`                      |
| `mcp__wikify__corpus_schema`      | `wikify corpus schema`                      |
| `mcp__wikify__corpus_image`       | `wikify corpus show figure:...` then Read on the printed `path` (CLI fallback) |

Listing maps onto search/traverse: "all docs ranked by citation_count"
is `corpus_find(by="paper", rank="citation_count")`; "chunks of one
doc" is `corpus_traverse(handle="doc:<short>", to="chunks")`. The
chunk traverse output is in document order and carries
``section_path`` + ``ord`` on every row, so the agent can pick the
introduction (or any other section) without N+1 round-trips.

Validation rules mirror the CLI exactly: `by="chunk"` +
`rank="citation_count"` is rejected on both surfaces, ambiguous
handles return `ambiguous_handle` with a match list, and so on.

## High-leverage parameters

- `corpus_find(field="title")` — title-only literal search, valid with
  `by="paper"`. Use for "papers whose title mentions X" rather than
  "papers whose body discusses X".
- `corpus_show(handle="doc:<short>", include_text=True, sections=["intro"])`
  — return the paper body grouped by section in document order in one
  call. Body excludes figure-caption stubs (``__image__``),
  references, acknowledgments, appendices, and boilerplate so it
  reads as prose. Section filter is forgiving: ``sections=["summary"]``
  matches ``"V. SUMMARY"``; numbering and case are stripped before
  comparison. When no section matches, the response ``notes`` echo
  every available section path.
- Figures live in ``corpus_traverse doc -> figures`` and
  ``corpus_show figure:<handle>`` (metadata) — call
  ``corpus_image(handle="figure:...")`` to also pull the binary into
  the model's context as an MCP ImageContent block. The figure item's
  ``meta.image_tool`` field advertises the call.
- ``corpus_show(handle="doc:...", include_text=True, mode="full")``
  returns the body as one ordered string (with ``## <section>``
  headers inlined) under ``meta.body``, instead of segmented under
  ``meta.text``. Best for "summarise this paper"; use the default
  ``mode="sections"`` when you may only want a subset.
- **References from a paragraph**:
  ``corpus_traverse(handle="chunk:<short>", to="cited-in-corpus")``
  parses the citation markers in that chunk's text and returns the
  in-corpus sources they resolve to. Pair with
  ``corpus_show chunk:<short> --full`` to read the surrounding prose,
  then traverse its citations to follow an argument.
- `corpus_find` paper rows now carry `meta.best_chunk_section` so the
  agent can tell whether a hit came from the abstract vs. references
  without an extra `corpus_show chunk:`.

## Response shape

## Response shape (envelope)

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
