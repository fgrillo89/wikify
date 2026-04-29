# MCP resources

Resources are the read path for full objects. Tools return handles +
previews + a `resource_uri` field; resources resolve a URI to the full
record. Use them when you've decided which handle to inspect — never
to enumerate.

## URI patterns (Phase 1)

| URI template                                       | Returns |
|----------------------------------------------------|---------|
| `wikify://corpus/docs/{ident}`                     | full doc record (id, title, kind, metadata, n_chunks, abstract) |
| `wikify://corpus/chunks/{ident}`                   | full chunk text + section path |
| `wikify://corpus/figures/{doc_short}/{stem}`       | figure record: caption, page, on-disk path, near-chunk handles |
| `wikify://corpus/equations/{ident}`                | equation record: latex, label, kind, chemical flag |
| `wikify://corpus/authors/{ident}`                  | author profile: name, h_index, citation_count, n_papers, top coauthors |
| `wikify://schemas/corpus`                          | same payload as `corpus_schema` tool |

Notes:

- `{ident}` is the bare short id (no `kind:` prefix). The path segment
  encodes the kind. e.g. `wikify://corpus/docs/514791d621fa`.
- Figures use a **two-segment** template because figure ids contain a
  slash (`<doc_short>/<stem>`) and FastMCP URI parameters are limited
  to one path segment each.
- Author idents replace spaces with `_` to keep URIs valid.

## When to use a resource vs another tool call

- **Tool first** for any list, search, traversal, or sampling
  operation. Tool responses include `resource_uri` on every item.
- **Resource fetch** when you already have a handle + URI and want the
  full record. Avoids re-running the search and avoids inlining
  hundreds of KB into a tool response.
- **Tool again** when you want a transformed view (rank, filter,
  group). Resources are read-only, parameter-free, and return the same
  shape every time.

## Empty resources

A resource read on a missing object surfaces a structured error
through the MCP client (e.g. ``doc_not_found``). Always pull the
handle from a fresh tool result before reading.
