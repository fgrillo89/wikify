# SQLite Query Store Implementation Plan

Status: proposal. This plan captures the current target for replacing the
runtime JSONL/NPZ/NetworkX query path with one SQLite-backed corpus/wiki query
store while preserving the CLI, MCP, and skill-facing behavior.

## Goal

Use SQLite as the local query store for corpus and wiki exploration:

- fast chunk/page lookup,
- BM25 lexical retrieval,
- current semantic embedding search,
- hybrid BM25 + vector retrieval,
- low-memory graph traversal,
- document/author/bibliography/asset/wiki-page joins,
- metric lookup for PageRank, citation counts, coauthor counts, and related
  derived graph metrics.

The public behavior should remain available through the same domain API, CLI,
and MCP surfaces. Strategy stays in skills.

## Non-Goals

- Do not introduce a separate graph database as the primary store.
- Do not make concepts part of the DB ontology.
- Do not store adjacency matrices as canonical data.
- Do not move external DOI/Crossref/OpenAlex cache records into corpus-local
  state.
- Do not make sqlite-vec, sqlite-muninn, or another extension a hard
  dependency until benchmarks justify it.

## Storage Boundaries

`wikify.db` is corpus/wiki-local query state:

- documents,
- chunks,
- authors,
- bibliography entries as they appear in ingested documents,
- assets,
- committed wiki pages,
- graph edges,
- FTS indexes,
- embeddings,
- metric projections,
- projection freshness/status.

The existing DOI/citation resolver SQLite cache remains separate if it stores
external lookup results. Ingest reads and writes that cache, then writes the
resolved local facts into `wikify.db`.

## Core Ontology

Node types:

```text
document
chunk
author
bib_entry
asset
wiki_page
```

No `concept` node type. Concept extraction, planning, and page candidates remain
bundle/workflow artifacts.

## Canonical Tables

### `documents`

One row per ingested source document.

```sql
documents(
  doc_id TEXT PRIMARY KEY,
  source_path TEXT,
  source_kind TEXT,       -- pdf | docx | html | md | pptx | unknown
  doc_type TEXT,          -- article | review | thesis | report | webpage | slides | note | unknown
  title TEXT,
  abstract TEXT,
  authors_json TEXT,
  year INTEGER,
  container_title TEXT,   -- journal | conference | book | website | collection | deck title
  publisher TEXT,
  doi TEXT,
  url TEXT,
  n_chunks INTEGER,
  n_tokens INTEGER,
  metadata_json TEXT
);
```

Promote only fields needed for lookup, filtering, sorting, and graph joins.
Keep the row generic: `abstract` can be null for notes, slides, and webpages;
`container_title` is the broad field for journal, conference, book, website, or
similar source container; `doi` is optional but useful for joining to local
bibliography entries. Store messy source metadata and domain-specific citation
payloads in JSON.

### `chunks`

One row per retrieval/citation unit.

```sql
chunks(
  chunk_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id),
  ord INTEGER NOT NULL,
  text TEXT NOT NULL,
  section_path_json TEXT,
  section_type TEXT,      -- abstract | intro | methods | results | discussion | caption | equation | references | other
  char_start INTEGER,
  char_end INTEGER,
  token_count INTEGER,
  is_boilerplate INTEGER DEFAULT 0,
  metadata_json TEXT
);
```

Indexes:

```sql
CREATE INDEX chunks_doc_ord ON chunks(doc_id, ord);
CREATE INDEX chunks_section_type ON chunks(section_type);
```

### `authors`

Lightweight identity for traversals. This is not a full person ontology.

```sql
authors(
  author_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  metadata_json TEXT
);
```

### `document_authors`

Derived from `documents.authors_json`, persisted for fast traversal.

```sql
document_authors(
  doc_id TEXT NOT NULL REFERENCES documents(doc_id),
  author_id TEXT NOT NULL REFERENCES authors(author_id),
  position INTEGER,
  role TEXT,
  PRIMARY KEY (doc_id, author_id, position)
);
```

Indexes:

```sql
CREATE INDEX document_authors_author ON document_authors(author_id, doc_id);
```

### `bib_entries`

One row per bibliography/reference entry inside an ingested document.

```sql
bib_entries(
  bib_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id),
  ord INTEGER,
  local_key TEXT,
  raw_text TEXT,
  title TEXT,
  authors_json TEXT,
  year INTEGER,
  container_title TEXT,   -- journal | conference | book | website | collection
  publisher TEXT,
  doi TEXT,
  url TEXT,
  target_doc_id TEXT,     -- nullable; set only when resolved to an ingested document
  confidence REAL,
  resolution TEXT,        -- exact_doi | title_year | manual | unresolved
  bib_json TEXT
);
```

Indexes:

```sql
CREATE INDEX bib_entries_doc_ord ON bib_entries(doc_id, ord);
CREATE INDEX bib_entries_doi ON bib_entries(doi);
CREATE INDEX bib_entries_target ON bib_entries(target_doc_id);
```

The full BibTeX/citation payload is stored in JSON. DOI/title/year/target are
promoted because they drive lookup and traversal.

### `chunk_citations`

In-text citation mentions linked to bibliography entries.

```sql
chunk_citations(
  chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id),
  doc_id TEXT NOT NULL REFERENCES documents(doc_id),
  bib_id TEXT NOT NULL REFERENCES bib_entries(bib_id),
  marker_text TEXT,
  char_start INTEGER,
  char_end INTEGER,
  context TEXT,
  PRIMARY KEY (chunk_id, bib_id, marker_text, char_start)
);
```

Indexes:

```sql
CREATE INDEX chunk_citations_bib ON chunk_citations(bib_id, chunk_id);
```

### `assets`

Generic non-body-text artifacts.

```sql
assets(
  asset_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id),
  asset_type TEXT,        -- figure | table | equation | image | code | slide | other
  ord INTEGER,
  page INTEGER,
  path TEXT,
  caption TEXT,
  content TEXT,
  metadata_json TEXT
);
```

### `chunk_assets`

Derived proximity/mention relation between chunks and assets.

```sql
chunk_assets(
  chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id),
  asset_id TEXT NOT NULL REFERENCES assets(asset_id),
  relation TEXT,          -- contains | near | mentions
  confidence REAL,
  PRIMARY KEY (chunk_id, asset_id, relation)
);
```

### `wiki_pages`

One row per committed wiki page.

```sql
wiki_pages(
  page_id TEXT PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,     -- article | person | index
  body TEXT NOT NULL,
  frontmatter_json TEXT,
  created_at TEXT,
  updated_at TEXT
);
```

## Derived Search Tables

### BM25 / FTS5

Use FTS5 in the same database.

```sql
chunks_fts(
  chunk_id UNINDEXED,
  doc_id UNINDEXED,
  title,
  section_path,
  text
);

wiki_pages_fts(
  page_id UNINDEXED,
  title,
  body
);
```

Initial retrieval modes:

```text
rank=semantic  current vector behavior
rank=bm25      FTS5 bm25()
rank=hybrid    BM25 + semantic fused with reciprocal rank fusion
text=true      literal/grep-like exact search
```

BM25 and vector search both return handles. Hybrid fuses by `chunk_id` or
`page_id`, then joins canonical rows for metadata and previews.

### Embeddings

Keep the current embedder contract and move storage behind it.

```sql
embedding_spaces(
  space_id TEXT PRIMARY KEY,
  backend TEXT NOT NULL,
  model TEXT,
  dim INTEGER NOT NULL,
  created_at TEXT
);

embeddings(
  space_id TEXT NOT NULL REFERENCES embedding_spaces(space_id),
  node_type TEXT NOT NULL,      -- chunk | wiki_page | document
  node_id TEXT NOT NULL,
  vector BLOB NOT NULL,         -- float32 bytes, unit-normalized
  PRIMARY KEY (space_id, node_type, node_id)
);
```

Supported current embedders:

```text
hash        dim 128
MiniLM/BGE  dim 384
Jina        dim 512
Nomic       dim 768
```

Changing backend/model/dim invalidates reuse for that embedding space. Preserve
the existing fingerprint behavior from `vectors.meta.json`.

Exact vector search is the first implementation. `sqlite-vec`, sqlite-muninn,
or another ANN/HNSW extension can become an optional derived accelerator after
benchmarking.

## Graph Storage

Store graph edges as a derived, persisted projection.

```sql
graph_edges(
  scope_type TEXT NOT NULL,     -- document | wiki_page | global
  scope_id TEXT NOT NULL,
  src_type TEXT NOT NULL,
  src_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  dst_type TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  weight REAL DEFAULT 1.0,
  ord INTEGER,
  meta_json TEXT,
  PRIMARY KEY (src_type, src_id, kind, dst_type, dst_id)
);
```

Indexes:

```sql
CREATE INDEX graph_scope ON graph_edges(scope_type, scope_id);
CREATE INDEX graph_out ON graph_edges(src_type, src_id, kind);
CREATE INDEX graph_in ON graph_edges(dst_type, dst_id, kind);
CREATE INDEX graph_kind ON graph_edges(kind);
```

Edge families:

```text
document -> chunk       has_chunk
chunk -> document       source_doc

document -> author      authored_by
author -> document      authored
author -> author        coauthor

chunk -> bib_entry      cites
bib_entry -> chunk      cited_in
bib_entry -> document   resolved_to
document -> document    references
document -> document    cited_by

document -> asset       has_asset
asset -> document       source_doc
chunk -> asset          mentions_asset | near_asset
asset -> chunk          context_chunk

wiki_page -> chunk      cites_evidence
chunk -> wiki_page      cited_by_wiki
wiki_page -> document   grounded_in
document -> wiki_page   supports_wiki
wiki_page -> wiki_page  links_to

chunk -> chunk          similar_to
document -> document    similar_to
wiki_page -> wiki_page  similar_to
```

Bidirectional traversal edges are emitted both ways when built. Runtime code
does not infer reverse edges.

## Graph Traversal API

Implement one small graph-native layer over SQLite:

```python
class GraphStore:
    def neighbors(node, direction="out", kinds=None, limit=100): ...
    def traverse(seeds, direction="out", kinds=None, max_depth=2, limit=500): ...
    def path(start, target, kinds=None, max_depth=4): ...
    def subgraph(seeds, kinds=None, depth=1, limit=500): ...
```

Use indexed one-hop SQL for `neighbors`.

Use recursive CTEs for simple bounded traversals:

```sql
WITH RECURSIVE walk(depth, node_type, node_id, path) AS (
  SELECT
    0,
    :start_type,
    :start_id,
    '|' || :start_type || ':' || :start_id || '|'

  UNION ALL

  SELECT
    walk.depth + 1,
    e.dst_type,
    e.dst_id,
    walk.path || e.dst_type || ':' || e.dst_id || '|'
  FROM walk
  JOIN graph_edges e
    ON e.src_type = walk.node_type
   AND e.src_id = walk.node_id
  WHERE walk.depth < :max_depth
    AND (:kind IS NULL OR e.kind = :kind)
    AND instr(walk.path, '|' || e.dst_type || ':' || e.dst_id || '|') = 0
)
SELECT *
FROM walk
WHERE depth > 0
LIMIT :limit;
```

Use Python BFS/DFS over indexed `neighbors` when traversal policy is more
complex:

- mixed directions,
- different edge kinds per hop,
- frontier ranking,
- weighted path search,
- early stopping,
- inspectable debug traces.

Complexity targets:

- one-hop lookup: `O(log E + degree(node))`,
- local graph-edge refresh: `O((old_edges + new_edges) log E)`,
- bounded BFS/DFS: `O(edges visited)`,
- full metric rebuild: `O(E)` or worse, depending on metric.

## Metrics

Metrics are derived projections over named graph views. Do not store PageRank or
other metrics directly on canonical entity rows.

```sql
graph_views(
  graph_name TEXT PRIMARY KEY,
  description TEXT,
  node_types_json TEXT,
  edge_kinds_json TEXT,
  directed INTEGER,
  weighted INTEGER,
  params_json TEXT,
  updated_at TEXT
);

node_metrics(
  graph_name TEXT NOT NULL,
  node_type TEXT NOT NULL,
  node_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  value REAL NOT NULL,
  computed_at TEXT NOT NULL,
  params_json TEXT,
  PRIMARY KEY (graph_name, node_type, node_id, metric)
);

edge_metrics(
  graph_name TEXT NOT NULL,
  src_type TEXT NOT NULL,
  src_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  dst_type TEXT NOT NULL,
  dst_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  value REAL NOT NULL,
  computed_at TEXT NOT NULL,
  params_json TEXT,
  PRIMARY KEY (graph_name, src_type, src_id, kind, dst_type, dst_id, metric)
);
```

Initial graph views:

```text
corpus_citation   document nodes, references edges, directed
chunk_citation    chunk/bib_entry/document nodes, cites/resolved_to edges, directed
author_coauthor   author nodes, coauthor edges, undirected
wiki_links        wiki_page nodes, links_to edges, directed
wiki_grounding    wiki_page/chunk/document nodes, cites_evidence/source_doc edges, directed
```

Adjacency matrices are computation artifacts. Build sparse matrices from
`graph_edges` during metric jobs and persist only `node_metrics` /
`edge_metrics`. If matrix construction becomes a bottleneck, cache matrices
under a derived cache directory, not as canonical DB state.

## Projection Refresh

Canonical rows change first. Derived projections refresh by scope.

Document add/update:

```text
1. Upsert document/chunks/authors/bib_entries/assets.
2. Refresh FTS rows for changed chunks.
3. Refresh embeddings for new/changed chunks.
4. Delete graph_edges where scope_type='document' and scope_id=:doc_id.
5. Insert derived graph edges for that document.
6. Mark affected graph views/metrics stale.
```

Wiki page add/update:

```text
1. Upsert wiki_pages.
2. Refresh wiki FTS row.
3. Refresh wiki page embedding.
4. Delete graph_edges where scope_type='wiki_page' and scope_id=:page_id.
5. Insert wiki links, evidence, and grounding edges.
6. Mark affected wiki graph views/metrics stale.
```

Projection status:

```sql
projection_status(
  projection TEXT NOT NULL,     -- fts | vectors | graph_edges | metrics
  scope_type TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  status TEXT NOT NULL,         -- fresh | stale | failed
  updated_at TEXT,
  error_json TEXT,
  PRIMARY KEY (projection, scope_type, scope_id)
);
```

Global metric jobs are explicit maintenance operations. They should not block
normal document/page addition.

## CLI and MCP Compatibility

Preserve the current agent-facing primitives:

```text
corpus find
corpus show
corpus list
corpus traverse
corpus sample
corpus schema

wiki find
wiki show
wiki list
wiki traverse
wiki schema
```

Add retrieval modes without changing the noun/verb model:

```text
wikify corpus find "growth per cycle" --rank bm25
wikify corpus find "growth per cycle" --rank hybrid
wikify corpus find "GPC" --text
```

MCP wraps the same domain implementation as CLI. Search/traverse responses
return handles, scores, relation metadata, and resource URIs.

## Notebooks

Add notebooks for exploration and explanation before locking down the full
implementation. These notebooks are part of the implementation plan because
the graph behavior needs to be visible and inspectable.

Target notebooks:

```text
notebooks/sqlite_store_schema_walkthrough.ipynb
notebooks/graph_traversal_bfs_dfs.ipynb
notebooks/recursive_cte_traversal.ipynb
notebooks/hybrid_retrieval_bm25_vector_rrf.ipynb
notebooks/metric_projection_pagerank.ipynb
```

Notebook goals:

- build a tiny fixture corpus in SQLite,
- show how `graph_edges` represents document/chunk/author/bib/wiki relations,
- step through one-hop lookup,
- step through Python BFS and DFS with a visible frontier/visited set,
- compare Python BFS with recursive CTE traversal,
- show cycle handling,
- show path search,
- show BM25, vector, and hybrid RRF retrieval over the same chunks,
- compute a small PageRank over a named graph view and write `node_metrics`,
- explain what gets refreshed when a document or wiki page is added.

The notebooks should use small synthetic data first, then optionally load a real
test corpus once the DB builder exists.

## Implementation Phases

### 1. Prototype Store and Notebooks

- Add a tiny SQLite schema builder in a prototype module or script.
- Create synthetic fixture data covering all node types except optional wiki
  pages.
- Write the BFS/DFS and recursive CTE notebooks.
- Validate the ontology and traversal semantics before touching production
  ingest.

Acceptance:

- The notebooks demonstrate one-hop traversal, multi-hop traversal, cycle
  control, path search, and subgraph extraction.
- The schema supports the current corpus handles and relation names.

### 2. Domain Store Layer

- Add a `CorpusQueryStore` or equivalent domain class that opens `wikify.db`.
- Implement canonical upsert/read methods.
- Implement FTS5 setup and BM25 search.
- Implement exact vector storage/search using current `embedder_for`.
- Implement `GraphStore`.

Acceptance:

- Unit tests cover document/chunk lookup, BM25 search, exact vector search,
  hybrid RRF, one-hop traversal, recursive traversal, and scoped graph refresh.

### 3. Ingest Projection Writer

- Update ingest refresh to write `wikify.db` alongside existing artifacts.
- Preserve existing JSONL/NPZ outputs during the migration period.
- Reuse unchanged chunk embeddings exactly as today.
- Keep DOI resolver cache separate and copy resolved local facts into
  `bib_entries`.

Acceptance:

- Existing tests still pass.
- New tests prove adding one document refreshes only that document's FTS,
  embeddings, and graph edge scope.

### 4. Query Path Migration

- Route `corpus.queries` through the SQLite store where available.
- Keep fallback to current JSONL/NPZ/NetworkX artifacts until migration is
  complete.
- Add `rank=bm25` and `rank=hybrid`.
- Preserve current CLI and MCP result shapes.

Acceptance:

- Existing CLI/MCP tests pass.
- The GPC-style exact-term query succeeds through BM25/hybrid without needing
  the literal `text=true` fallback.

### 5. Wiki Store Projection

- Persist committed wiki pages into `wiki_pages`.
- Add wiki FTS, wiki embeddings, and wiki graph edges.
- Route wiki search/traverse through the same store.
- Preserve existing bundle files as canonical human-facing wiki output.

Acceptance:

- Empty-wiki behavior remains graceful.
- Wiki page addition refreshes only that page's derived rows.

### 6. Metrics

- Add `graph_views`, `node_metrics`, `edge_metrics`, and
  `projection_status`.
- Implement explicit metric refresh commands for named graph views.
- Compute initial metrics: citation count, in/out degree, PageRank, coauthor
  count.

Acceptance:

- Queries can rank documents/authors/wiki pages by stored metrics without
  recomputing graphs.
- Metrics can be marked stale after local updates and refreshed explicitly.

### 7. Optional Accelerators

- Benchmark `sqlite-vec` for vector search.
- Study sqlite-muninn's graph traversal and HNSW patterns.
- Add accelerators only behind the domain store interface.

Acceptance:

- Public CLI/MCP behavior is unchanged with or without accelerators.
- Windows/Linux installability and fallback behavior are documented.

## Open Questions

- Exact filename/location for `wikify.db` inside corpus and bundle roots.
- Whether wiki rows live in the corpus DB, bundle DB, or both with attached
  SQLite databases.
- How long to maintain JSONL/NPZ/NetworkX fallback artifacts during migration.
- Whether document-level embeddings should be stored or computed from chunks.
- Which graph metrics are needed first by real workflows.
