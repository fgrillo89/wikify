# Wikify databases

Wikify keeps its state in plain SQLite files. There is no database
server to run: each store is one file on disk that any SQLite client can
open. This document covers the two stores that hold the bulk of a
project's data — the **corpus store** (the parsed input) and the
**wiki store** (the committed pages) — and explains the smaller files
that are *derived* from them.

If you have not read [overview.md](overview.md) yet, read it first. The
terms **corpus**, **chunk**, **bundle**, **wiki**, **page**, and
**evidence** are defined there and reused here with the same meaning.

## Two stores, two lifetimes

A Wikify project has two long-lived SQLite files, and they live in
different places because they have different lifetimes.

- **The corpus store** is `wikify.db`, in the corpus directory
  (`<corpus_root>/wikify.db`). It holds your source documents after
  parsing: the cleaned text, the chunks, search embeddings, and the
  citation/author graph. It is written once when you run
  `corpus build` and is treated as **read-only** while a wiki is being
  built. One corpus store can feed many bundles.

- **The wiki store** is `wiki.db`, in the bundle directory
  (`<bundle_root>/wiki.db`). It holds the committed pages of one wiki:
  their text, the evidence behind each citation, the links between them,
  and the navigation categories. It grows as the agent commits pages.

A third, smaller store — the **claim store** (`<bundle_root>/claims.db`)
— backs the separate data-artifact layer and is described briefly at the
end.

Every store is opened through the same connection helper
(`corpus/store/connection.py`), which applies one fixed block of SQLite
settings on every connection: write-ahead logging (`WAL`) so a reader
and a writer do not block each other, foreign keys on so deletes cascade
correctly, and a busy timeout so brief lock contention retries instead
of failing. Multi-row updates run inside a `transaction(...)` block that
commits as a unit or rolls back on error, so a reader never sees a
half-written record.

## The corpus store (`wikify.db`)

The corpus store is one file holding canonical entity rows, full-text
indexes, embeddings, a graph-edge table, and pre-computed metrics. The
`Store` class (`corpus/store/__init__.py`) is the single entry point; it
composes the per-area helpers (documents, authors, bibliography, assets,
full-text search, vectors, graph) into one surface so callers do not
need to know how the package is split. The schema is created and kept
up to date idempotently by `apply_schema` (`corpus/store/schema.py`,
current `SCHEMA_VERSION = 2`): every table uses `CREATE TABLE IF NOT
EXISTS`, so opening an old file simply adds anything missing.

### Canonical entity tables

These tables are the source of truth for what is in the corpus.

- **`documents`** — one row per source file. The key is `doc_id`.
  Columns carry the title, abstract, a one-line `tldr`, the author list
  (as JSON), year, venue, publisher, DOI, URL, and counts
  (`n_chunks`, `n_tokens`). A free-form `metadata_json` holds anything
  that does not have its own column.

- **`chunks`** — one row per chunk (a short, citable passage). The key
  is `chunk_id`; `doc_id` points back to its document, and `ord` records
  its position in that document. The verbatim passage is in `text`.
  `section_type` and `section_path_json` say where in the document it
  came from, and `is_boilerplate` flags non-content passages
  (references, captions, page furniture) so they can be excluded from
  coverage. This is the table evidence quotes are checked against.

- **`authors`** and **`document_authors`** — the people who wrote the
  documents, and the many-to-many link between a document and its
  authors (with position and role).

- **`bib_entries`** — the reference list parsed out of each document.
  Each entry can resolve to another document in the same corpus through
  `target_doc_id`, which is how citations between your own papers become
  graph edges. `chunk_citations` records *where* in the text a reference
  marker like `[12]` appeared, linking a chunk to the bib entry it
  cites.

- **`assets`** and **`chunk_assets`** — figures and equations extracted
  from documents, and the link from a chunk to the assets near it.

### Search: embeddings and full-text

The corpus store carries both kinds of search side by side.

- **Semantic search.** `embedding_spaces` names each embedding model
  used (its backend, model name, and vector dimension), and `embeddings`
  stores one vector per node as a binary `BLOB`, keyed by space, node
  type (for example `chunk` or `author`), and node id. Storing the space
  metadata next to the vectors means a corpus can be re-embedded with a
  new model without losing the old vectors.

- **Full-text search.** `chunks_fts` and `documents_fts` are SQLite
  FTS5 indexes. They are *external-content* indexes: they hold no copy
  of the text themselves, only the search index over the `chunks` and
  `documents` rows, so they stay in sync with the canonical tables and
  cost little disk. `Store.search_chunks_bm25` and the hybrid search that
  fuses BM25 with vector hits read these.

### The graph

Rather than a separate table per relationship, all edges live in one
generic table, **`graph_edges`**: a row is
`(src_type, src_id, kind, dst_type, dst_id)` with an optional weight and
metadata. The `kind` column names the relationship. Edges built during
ingest include `has_chunk` (document to its chunks), `authored_by`
(document to author), `coauthor` (author to author), and `references`
(document to a document it cites). Indexes on the outgoing and incoming
ends make "what does X point to" and "what points to X" both fast. This
is the table the graph-traversal read surface walks.

### Derived projections inside the corpus store

Some columns are not raw facts but **projections**: values recomputed
from the edge table so reads are cheap. They are rebuilt at the end of
`corpus build` and stored back in the same file.

- **`graph_views`** catalogs named views over the edges — for example
  `corpus_citation` (document nodes joined by `references` edges) and
  `author_coauthor` (authors joined by `coauthor` edges) — recording for
  each whether it is directed and weighted.

- **`node_metrics`** holds one value per `(view, node, metric)`. Cheap
  metrics that recompute in time proportional to the number of edges —
  `citation_count`, `coauthor_count`, and in/out degree — are refreshed
  by `refresh_cheap_metrics` (`corpus/store/metrics.py`). More expensive
  global metrics such as PageRank and h-index are computed separately
  (`corpus/store/metrics_global.py`). `edge_metrics` is the same idea for
  per-edge values.

- **`projection_status`** tracks whether each projection is fresh,
  stale, or errored, so a rebuild can be selective.

Because every projection is a function of the canonical rows and the
edge table, the corpus store can always be rebuilt from the parsed
documents; the metrics never become a second source of truth.

## Handles: short names for long ids

A real corpus `doc_id` is unwieldy — it is the document's natural title
followed by a 12-character hex suffix, for example:

```
[2011 Yang] Dopant Control by Atomic Layer Deposition...Switches_5f92b0389ccd
```

Typing or printing that everywhere would be miserable, so the CLI and
the skills use **handles**: short, stable names for corpus rows
(`corpus/handles.py`). The hex suffix alone (`5f92b0389ccd`) is globally
unique, so a handle is usually just that suffix. `resolve` expands a
short handle back to the full id by trying, in order: an exact match,
then a unique short-suffix match, then a unique delimited
`_<suffix>` ending — raising a clear error if a handle is ambiguous or
unknown. For output, `format_handle` prints `kind:short`, for example
`chunk:5f92b0389ccd` or `doc:...`; author handles replace spaces with
underscores so the handle is safe to pass through a pipe. Bulk callers
build a `HandleIndex` once so each lookup is constant-time.

Handles are purely a naming convenience over the canonical ids; nothing
in the stores depends on them.

## The wiki store (`wiki.db`)

The wiki store holds the committed output of one bundle. Its shape
deliberately mirrors the corpus store — a canonical table, an FTS5
external-content index, a generic edge table, embeddings, and the same
view/metric/projection tables — so the same read patterns work on both
sides. The schema and helpers live in `bundle/wiki/store.py`;
`open_wiki_store` creates the tables on first use.

### Page rows and their backing tables

- **`wiki_pages`** — one row per committed page. The key is `page_id`;
  `slug` is the unique URL-safe name; `kind` is `article`, `person`, or
  `data`; `body` is the page text and `frontmatter_json` its metadata
  (aliases and so on). `wiki_pages_fts` is the FTS5 index over the title
  and body, used by `search_wiki_bm25`.

- **`wiki_evidence`** — the citations behind a page. Each row ties a
  page and a citation marker (`[^e1]`, `[^e2]`, …) to the `chunk_id` and
  `doc_id` it came from, plus the verbatim `quote`. These `chunk_id` /
  `doc_id` values point straight back into the corpus store, which is how
  a claim on a page is traced to its origin.

- **`wiki_edges`** — the page graph, in the same generic
  `(src, kind, dst)` form as the corpus edge table. Three kinds are
  written: `links_to` (page to page), `cites_evidence` (page to the
  chunk it quotes), and `grounded_in` (page to the document a quote came
  from).

- **`wiki_categories`** and **`wiki_category_pages`** — the navigation
  hierarchy: a tree of categories and the pages that belong to each.
  These are populated from a validated navigation payload by
  `apply_navigation_categories` and exported back into the render-ready
  shape by `export_navigation_json`.

- **`wiki_embedding_spaces`** / **`wiki_embeddings`** — per-page vectors
  for semantic search over the wiki, mirroring the corpus embedding
  tables but keyed by `page_id`.

One write, `upsert_wiki_page`, refreshes a page's row, its FTS entry,
its evidence rows, and its outgoing edges inside a single transaction, so
a reader never sees a page whose body has been updated but whose evidence
is still stale.

### Markdown is canonical; `wiki.db` is the query store

A subtle but important point: the **markdown files** under
`wiki/articles/` and `wiki/people/` are the canonical pages. `wiki.db`
is a queryable mirror of them. When the agent commits a validated page
(`bundle/wiki/commit.py`), it writes the markdown file and upserts the
matching `wiki.db` row. If `wiki.db` is ever deleted or falls behind,
`rebuild_graph` (`bundle/wiki/derived.py`) re-reads every markdown page,
parses its evidence footnotes, and rewrites the rows — the database is
reconstructible from the files.

The one exception is **data pages** (`kind=data`). Their rendered
markdown only carries document-level references, so re-deriving them from
markdown would lose the precise chunk ids. Instead their `wiki.db` rows
are authored directly from the claim store, and a rebuild restores them
from there rather than from their markdown.

## Derived projections in the bundle

Alongside `wiki.db`, a bundle keeps a `derived/` directory of files that
are all rebuildable from the canonical pages. They exist so downstream
tools (the renderer, the evaluators, older readers) have stable,
ready-made inputs.

- **`derived/index.json`** — a flat list of every committed page (kind,
  slug, and path), produced by walking the page directories.

- **`derived/navigation.json`** — the navigation tree in the shape the
  renderer expects, exported from the `wiki_categories` tables.

- **`derived/vectors.npz`** — the per-page embedding matrix, kept as a
  compatibility projection for readers that load vectors from a file.
  The canonical copy of these vectors lives in `wiki_embeddings`; the
  `.npz` is written alongside it.

These are refreshed by `wikify wiki build graph` / `wikify wiki build
vectors`, which the agent runs once at the end of a workflow rather than
on every commit, so a hot commit loop does not pay the embedding cost.

## The claim store (`claims.db`)

The data-artifact layer has its own store because it is not part of the
wiki page graph. `claims.db` (`data/store.py`) holds individual
**data points** — one verifiable number each, with its subject,
property, value, unit, the source `doc_id` / `chunk_id`, and a grounding
quote — in the `data_points` table. A `property_registry` catalogs the
property space and its canonical units. `data_artifacts` stores each
table as a durable *specification* plus the claim ids that back it
(`data_artifact_claims`); the rendered table is always re-derived from
those rows, never hand-edited. Because data points carry the same
`chunk_id` / `doc_id` references as wiki evidence, a data table traces
back to the corpus the same way a page does.

This split is deliberate, and it matches the two-layer model in the
overview: looking a data table up through the wiki query surface
correctly returns "not found," and the right move is to fall back to the
data surface.

## How the stores connect

The two main stores are joined by ids, not by foreign keys across files.
A `wiki_evidence` row carries the `chunk_id` and `doc_id` of the corpus
chunk it quotes; the claim store does the same. When a cross-database
query is needed, the corpus `wikify.db` can be **attached** to a wiki
`wiki.db` connection so both sets of tables are visible at once. The
direction of dependence is one-way: the wiki and claim stores reference
the corpus, the corpus never references them — which is what lets one
read-only corpus feed many independent bundles.
