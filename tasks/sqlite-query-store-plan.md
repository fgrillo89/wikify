# SQLite Query Store

Implementation spec for replacing the runtime JSONL/NPZ/NetworkX query path with one SQLite-backed corpus/wiki query store. Strategy stays in skills. CLI and MCP shapes stay byte-stable.

## Context

Today the corpus query path reads `docs/*.json` + `chunks/*.jsonl` + `vectors.npz` + `knowledge_graph.json` (NetworkX `MultiDiGraph` pickle) + `citations.json`, plus a separate per-corpus `.citestore.db` for Crossref/OpenAlex DOI lookups. `Corpus()` materializes the graph and vectors into RAM at startup. BM25 is unavailable; literal-grep is the fallback. Per-document refresh is partial: only chunk embeddings are reused incrementally — graph, citation edges, topics, and bibliography are rebuilt corpus-wide on every `corpus refresh`.

This is fine at fixture scale and uncomfortable at thousands of papers. The new store is one SQLite file (`<corpus_root>/wikify.db`) with canonical entity tables, FTS5 for BM25, BLOB float32 vectors loaded once into a numpy matrix, a `graph_edges` table traversed by recursive CTE, and a metrics projection that refreshes on explicit maintenance. Strictly better than today at every scale: lazy schema-level load, indexed lookups, no NetworkX in the critical path.

## Goals

Use SQLite as the local query store for corpus and wiki exploration:

- fast chunk/document/page lookup,
- BM25 lexical retrieval,
- exact semantic vector search via in-RAM numpy matmul,
- hybrid BM25 + vector via reciprocal rank fusion,
- low-memory graph traversal,
- document/author/bibliography/asset/wiki-page joins,
- metric lookup for citation count, PageRank, h-index, coauthor count.

Public behavior remains available through the same domain API, CLI, and MCP surfaces.

## Non-Goals and Hard Constraints

- No separate graph database. No Apache AGE, Kùzu (archived Oct 2025), DuckPGQ (still WIP), Neo4j, FalkorDB.
- No SQLite loadable extensions. No `sqlite-vec`, no `sqlite-muninn`, no FTS5 custom-tokenizer C bindings.
- No new top-level pyproject dependencies in Phases 1–6. Stdlib `sqlite3`, numpy, FTS5, on-demand `networkx.pagerank` (already a dep) and `scipy.sparse` (already a dep).
- No `concept` node type. Concept extraction stays a bundle/workflow artifact under `<bundle_root>/work/inbox/`.
- No persisted `similar_to` edges. Vector search at query time is the similarity layer.
- No bidirectional duplicated edges. Edges are stored once; direction is handled by the `graph_in` / `graph_out` indexes.
- No `scope_type` / `scope_id` projection columns. Scoped refresh keys off the source-of-truth foreign key.
- DOI/Crossref/OpenAlex lookup cache (`<corpus_root>/.citestore.db`) stays separate. Resolved canonical metadata is copied into `bib_entries` so corpora are portable without the cache.

## Storage Boundaries

`wikify.db` is corpus-local query state at `<corpus_root>/wikify.db`:

- `documents`, `chunks`, `authors`, `document_authors`,
- `bib_entries`, `chunk_citations`,
- `assets`, `chunk_assets`,
- `embedding_spaces`, `embeddings`,
- `chunks_fts`, `documents_fts`,
- `graph_edges`,
- `graph_views`, `node_metrics`, `edge_metrics`,
- `projection_status`.

`wiki.db` is bundle-local query state at `<bundle_root>/wiki.db`. Wiki pages are bundle-scoped (one corpus → many bundles → many wikis). Cross-DB joins use `ATTACH DATABASE`. Markdown files in `<bundle_root>/wiki/articles/*.md` and `wiki/people/*.md` remain the source of truth on disk; SQLite is a derived index for query.

`.citestore.db` (existing) keeps DOI/Crossref/OpenAlex lookup results. Strictly external-cache role; never queried by skills or CLI directly.

## Ontology

Node types:

```
document
chunk
author
bib_entry
asset
wiki_page
```

## Connection Pragmas

Every connection runs:

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA temp_store = MEMORY;
PRAGMA mmap_size = 268435456;  -- 256 MB
```

Pragmas are per-connection in SQLite; a single helper module sets them on every `connect()`.

## Canonical Tables

### `documents`

```sql
CREATE TABLE documents (
  doc_id TEXT PRIMARY KEY,
  source_path TEXT,
  source_kind TEXT,           -- pdf | docx | html | md | pptx | unknown
  doc_type TEXT,              -- article | review | thesis | report | webpage | slides | note | unknown
  title TEXT,
  abstract TEXT,              -- nullable: notes / slides / webpages may not have one
  tldr TEXT,                  -- preserves Document.tldr from current model
  authors_json TEXT,
  year INTEGER,
  container_title TEXT,       -- journal | conference | book | website | collection
  publisher TEXT,
  doi TEXT,                   -- exact-match column; never queried via FTS
  url TEXT,
  n_chunks INTEGER,
  n_tokens INTEGER,
  metadata_json TEXT          -- everything else from Document.metadata
);

CREATE INDEX documents_doi ON documents(doi) WHERE doi IS NOT NULL;
CREATE INDEX documents_year ON documents(year);
```

`Document.sections` is reconstructable from `chunks.section_path_json` joined to `chunks.ord` — not promoted. `Document.similar_to` becomes optional `graph_edges(document → document, kind='similar_to')` rows, populated only by an explicit similarity-edge job (off by default; see Non-Goals). `Document.cites` and `Document.cites_same` are derived from `chunk_citations` + `bib_entries.target_doc_id` and not stored as columns.

### `chunks`

```sql
CREATE TABLE chunks (
  chunk_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  text TEXT NOT NULL,
  section_path_json TEXT,
  section_type TEXT,          -- abstract | intro | methods | results | discussion | caption | equation | references | other
  char_start INTEGER,
  char_end INTEGER,
  token_count INTEGER,
  is_boilerplate INTEGER DEFAULT 0,
  equation_ids_json TEXT,     -- preserves Chunk.equation_ids for chunk → equation joins
  metadata_json TEXT
);

CREATE INDEX chunks_doc_ord ON chunks(doc_id, ord);
CREATE INDEX chunks_section_type ON chunks(section_type);
```

### `authors`

```sql
CREATE TABLE authors (
  author_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  metadata_json TEXT
);
```

### `document_authors`

Derived from `documents.authors_json`, regenerated on document upsert.

```sql
CREATE TABLE document_authors (
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  author_id TEXT NOT NULL REFERENCES authors(author_id),
  position INTEGER,
  role TEXT,
  PRIMARY KEY (doc_id, author_id, position)
);

CREATE INDEX document_authors_author ON document_authors(author_id, doc_id);
```

### `bib_entries`

One row per bibliography/reference entry inside an ingested document.

```sql
CREATE TABLE bib_entries (
  bib_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  ord INTEGER,
  local_key TEXT,
  raw_text TEXT,
  title TEXT,
  authors_json TEXT,
  year INTEGER,
  container_title TEXT,
  publisher TEXT,
  doi TEXT,                   -- exact-match column
  url TEXT,
  target_doc_id TEXT REFERENCES documents(doc_id) ON DELETE SET NULL,  -- nullable; set when resolved to an in-corpus doc
  confidence REAL,
  resolution TEXT,            -- exact_doi | title_year | manual | unresolved
  bib_json TEXT
);

CREATE INDEX bib_entries_doc_ord ON bib_entries(doc_id, ord);
CREATE INDEX bib_entries_doi ON bib_entries(doi) WHERE doi IS NOT NULL;
CREATE INDEX bib_entries_target ON bib_entries(target_doc_id) WHERE target_doc_id IS NOT NULL;
CREATE INDEX bib_entries_unresolved ON bib_entries(target_doc_id, doi) WHERE target_doc_id IS NULL;
```

DOI canonicalization: lowercase, strip leading `https://doi.org/`. Title+year fallback uses lowercased first-50-character substring match — port verbatim from `src/wikify/corpus/graph_build.py` resolution.

### `chunk_citations`

In-text citation mentions linked to bibliography entries. Regenerated on document upsert.

```sql
CREATE TABLE chunk_citations (
  chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  bib_id TEXT NOT NULL REFERENCES bib_entries(bib_id) ON DELETE CASCADE,
  marker_text TEXT,
  char_start INTEGER,
  char_end INTEGER,
  context TEXT,
  PRIMARY KEY (chunk_id, bib_id, marker_text, char_start)
);

CREATE INDEX chunk_citations_bib ON chunk_citations(bib_id, chunk_id);
```

### `assets`

```sql
CREATE TABLE assets (
  asset_id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  asset_type TEXT,            -- figure | table | scheme | equation | image | code | slide | other
  ord INTEGER,
  page INTEGER,
  path TEXT,                  -- on-disk path for figures (PNGs and sidecars stay on disk)
  caption TEXT,
  content TEXT,               -- inline content for equations/code
  metadata_json TEXT
);

CREATE INDEX assets_doc ON assets(doc_id, asset_type, ord);
```

### `chunk_assets`

```sql
CREATE TABLE chunk_assets (
  chunk_id TEXT NOT NULL REFERENCES chunks(chunk_id) ON DELETE CASCADE,
  asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
  relation TEXT,              -- contains | near | mentions
  confidence REAL,
  PRIMARY KEY (chunk_id, asset_id, relation)
);
```

### `wiki_pages` (in `wiki.db`)

```sql
CREATE TABLE wiki_pages (
  page_id TEXT PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,         -- article | person | index
  body TEXT NOT NULL,
  frontmatter_json TEXT,
  created_at TEXT,
  updated_at TEXT
);
```

## Derived Search Tables

### FTS5 (external-content)

External-content FTS5 over canonical tables — no text duplication, `snippet()` and `highlight()` work, refresh by explicit `INSERT INTO fts(fts) VALUES('rebuild')` on bulk load.

```sql
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  text,
  content='chunks',
  content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2 tokenchars ''-'''
);

CREATE VIRTUAL TABLE documents_fts USING fts5(
  title,
  abstract,
  content='documents',
  content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2 tokenchars ''-'''
);
```

Tokenizer rationale: `unicode61` with diacritic stripping handles scientific text without collapsing semantically distinct stems (`oxide` vs `oxidation`). No porter. Greek letters normalized at ingest. Hyphenated terms (`atomic-layer-deposition`) stay tokenized via `tokenchars '-'`. DOIs and chemical formulas with periods/slashes use the exact-match `doi` column, not FTS.

Retrieval modes (no change to the noun/verb model):

```
--rank semantic     vector matmul (current default)
--rank bm25         FTS5 bm25() with ascending order
--rank hybrid       reciprocal rank fusion of bm25 + semantic, k=60
--text              literal substring fallback (preserved; rarely needed once BM25 lands)
```

BM25 column weights: `bm25(documents_fts, 4.0, 1.0)` for title-vs-abstract on the document side; `bm25(chunks_fts, 1.0)` on chunks. Hybrid fuses (a) chunk-level vector top-200, (b) chunk-level BM25 top-200, (c) document-level BM25 top-200 (rolled up to constituent chunks).

### Embeddings

```sql
CREATE TABLE embedding_spaces (
  space_id TEXT PRIMARY KEY,
  backend TEXT NOT NULL,
  model TEXT,
  dim INTEGER NOT NULL,
  created_at TEXT
);

CREATE TABLE embeddings (
  space_id TEXT NOT NULL REFERENCES embedding_spaces(space_id) ON DELETE CASCADE,
  node_type TEXT NOT NULL,    -- chunk | wiki_page | document
  node_id TEXT NOT NULL,
  vector BLOB NOT NULL,       -- float32 LE bytes, unit-normalized at write time
  PRIMARY KEY (space_id, node_type, node_id)
);

CREATE INDEX embeddings_space_type ON embeddings(space_id, node_type);
```

Currently shipped embedders: `hash` (dim 384, deterministic, no external model) and `fastembed` with `sentence-transformers/all-MiniLM-L6-v2` (dim 384). Additional fastembed models (Jina, Nomic, BGE) plug in via the same `embedding_spaces` row; no schema change required. Fingerprint compatibility (backend, dim, model) is preserved verbatim from `src/wikify/corpus/vectors_meta.py`.

Vector search is exact: on first query in a process, decode all `(node_id, vector)` rows for the active space into one contiguous `(n, d)` float32 numpy matrix; cache on the store object. Subsequent queries are `matrix @ query_vec`, then `np.argpartition` for top-k. The vector path lives behind `store.vector_index()` so a future `hnswlib` sidecar swap is a one-file change.

## Graph Storage

```sql
CREATE TABLE graph_edges (
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

CREATE INDEX graph_out ON graph_edges(src_type, src_id, kind);
CREATE INDEX graph_in ON graph_edges(dst_type, dst_id, kind);
CREATE INDEX graph_kind ON graph_edges(kind);
```

Edges are stored once. Direction is handled by `graph_out` (forward) and `graph_in` (reverse) indexes.

Edge kinds (source-of-truth direction only):

```
document  -> chunk        has_chunk
document  -> author       authored_by
author    -> author       coauthor              (undirected; arbitrary tie-breaker on src_id < dst_id)
chunk     -> bib_entry    cites
bib_entry -> document     resolved_to           (only when target_doc_id IS NOT NULL)
document  -> document     references            (derived: chunk → bib → resolved doc)
document  -> asset        has_asset
chunk     -> asset        near | contains | mentions   (relation kind matches chunk_assets.relation)
wiki_page -> chunk        cites_evidence
wiki_page -> document     grounded_in
wiki_page -> wiki_page    links_to
```

`coauthor` is the only undirected kind; storage convention is `src_id < dst_id` lex order. All others are directed and queried via `graph_in` for reverse walks (`cited_by`, `authored`, `containing_doc`, etc. are not separate edges).

`document → document similar_to` exists only when the explicit `similarity-edges build` job is run. Off by default.

## Graph Traversal API

```python
class GraphStore:
    def neighbors(node, *, direction="out", kinds=None, limit=100): ...
    def traverse(seeds, *, direction="out", kinds=None, max_depth=2, limit=500): ...
    def path(start, target, *, kinds=None, max_depth=4): ...
    def subgraph(seeds, *, kinds=None, depth=1, limit=500): ...
```

`neighbors` is one indexed lookup against `graph_out` or `graph_in`.

`traverse` and `subgraph` use the **default** recursive CTE (UNION-based dedup, depth cap, no path tracking):

```sql
WITH RECURSIVE walk(depth, node_type, node_id) AS (
  SELECT 0, :start_type, :start_id
  UNION
  SELECT walk.depth + 1, e.dst_type, e.dst_id
  FROM walk
  JOIN graph_edges e
    ON e.src_type = walk.node_type
   AND e.src_id   = walk.node_id
  WHERE walk.depth < :max_depth
    AND (:kind IS NULL OR e.kind = :kind)
)
SELECT * FROM walk WHERE depth > 0 LIMIT :limit;
```

`path` uses the **path-tracking** variant (string-built path, `instr()` cycle guard) — slower but only invoked when callers need the full path.

Use Python BFS over `neighbors` for: mixed directions, different kinds per hop, frontier ranking, weighted path search, early stopping, debug traces.

Every traversal in tests is verified with `EXPLAIN QUERY PLAN` to confirm `graph_out`/`graph_in` indexes are used.

## Metrics

Metrics are derived projections over named graph views.

```sql
CREATE TABLE graph_views (
  graph_name TEXT PRIMARY KEY,
  description TEXT,
  node_types_json TEXT,
  edge_kinds_json TEXT,
  directed INTEGER,
  weighted INTEGER,
  params_json TEXT,
  updated_at TEXT
);

CREATE TABLE node_metrics (
  graph_name TEXT NOT NULL,
  node_type TEXT NOT NULL,
  node_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  value REAL NOT NULL,
  computed_at TEXT NOT NULL,
  params_json TEXT,
  PRIMARY KEY (graph_name, node_type, node_id, metric)
);

CREATE TABLE edge_metrics (
  graph_name TEXT NOT NULL,
  src_type TEXT NOT NULL, src_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  dst_type TEXT NOT NULL, dst_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  value REAL NOT NULL,
  computed_at TEXT NOT NULL,
  params_json TEXT,
  PRIMARY KEY (graph_name, src_type, src_id, kind, dst_type, dst_id, metric)
);
```

Initial graph views:

```
corpus_citation   document nodes, references edges, directed
chunk_citation    chunk/bib_entry/document nodes, cites/resolved_to edges, directed
author_coauthor   author nodes, coauthor edges, undirected
wiki_links        wiki_page nodes, links_to edges, directed
wiki_grounding    wiki_page/chunk/document nodes, cites_evidence/grounded_in edges, directed
```

Metric refresh is split:

- **Cheap incremental** (Phase 5): in/out degree, citation count (= incoming `references` edge count), coauthor count. Updated per-document on add/update.
- **Global maintenance** (Phase 6): PageRank, h-index. Built on demand from a CSR matrix constructed from `graph_edges` rows; computed by `nx.pagerank` (or `scipy.sparse.linalg` power iteration). Not implicit on write — gated behind `wikify corpus metrics refresh --view corpus_citation`. Stale reads return values with a `stale=true` flag.

Adjacency matrices are computation artifacts. Build on demand, persist only `node_metrics` / `edge_metrics`.

## Refresh Model

Canonical rows change first; derived projections refresh per scope.

**Document add/update:**

1. Upsert `documents` / `chunks` / `authors` / `document_authors` / `bib_entries` / `assets` / `chunk_assets` / `chunk_citations` (`chunk_citations` and `document_authors` regenerated, not edited).
2. Refresh FTS rows for changed chunks/document.
3. Refresh embeddings for new/changed chunks (reuse current `_embed_chunks_incremental` fingerprint logic from `src/wikify/ingest/pipeline.py`).
4. Delete `graph_edges WHERE src_type='document' AND src_id=:doc_id`. Insert outbound edges.
5. **Re-resolve `bib_entries WHERE target_doc_id IS NULL`** whose DOI or title+year now matches the new document. Insert `bib_entry → document, kind='resolved_to'` and `document → document, kind='references'` edges as a result. (This is the inbound-edge correctness step that today's full rebuild handles implicitly.)
6. Mark affected `graph_views` / global metrics stale via `projection_status`.

**Wiki page add/update:**

1. Upsert `wiki_pages` (in `wiki.db`).
2. Refresh wiki FTS row.
3. Refresh wiki page embedding.
4. Delete `graph_edges WHERE src_type='wiki_page' AND src_id=:page_id`. Insert wiki links, evidence, and grounding edges.
5. Mark wiki views/metrics stale.

```sql
CREATE TABLE projection_status (
  projection TEXT NOT NULL,           -- fts | vectors | graph_edges | metrics
  scope_type TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  status TEXT NOT NULL,               -- fresh | stale | failed
  updated_at TEXT,
  error_json TEXT,
  PRIMARY KEY (projection, scope_type, scope_id)
);
```

**This is new behavior, not a port.** Today's `corpus refresh` does corpus-wide rebuilds for graph, citation edges, topics, and bibliography. Per-doc scoped refresh requires the inbound re-resolution step in (5) to match current full-rebuild correctness. Phase 2 acceptance enforces this with a parity test.

## CLI / MCP Compatibility

All of these stay shape-stable:

```
corpus build      preserved; ingest now writes wikify.db alongside legacy artifacts
corpus refresh    preserved; per-doc scoped refresh
corpus check      gains SQLite-aware health probe (PRAGMA integrity_check, FTS optimize state, embedding-space dim/model match, projection_status sweep)
corpus list       preserved (docs | chunks | files)
corpus find       preserved verbs; gains --rank bm25 and --rank hybrid
corpus sample     preserved (uses node_metrics for pagerank-weighted strategy)
corpus show       preserved
corpus traverse   preserved; routes through GraphStore
corpus schema     preserved; reflects current ontology + edge kinds + rank metrics
corpus repl       preserved
wiki list         preserved
wiki find         preserved; gains BM25
wiki show         preserved
wiki build        preserved
wiki check        gains SQLite-aware health probe
wiki commit       preserved; on commit, upsert into wiki_pages and refresh derived rows
render            preserved
eval              preserved
```

MCP wraps the same domain implementation as CLI. Search and traverse responses return handles, scores, relation metadata, and resource URIs — byte-stable with golden tests.

## Scaling Envelope

Per academic paper, rough order-of-magnitude (typical PDF): ~30 chunks, ~30–60 references, ~5–15 figures/tables, ~50–150 in-text citation markers, ~5–10 authors.

| Papers | `wikify.db` on disk | Vectors in RAM | Vector search | Notes |
|---|---|---|---|---|
| 100 | ~30 MB | ~5 MB | <1 ms | trivial |
| 1k | ~250 MB | ~50 MB | ~4 ms | trivial |
| **5k** | **~1.3 GB** | **~230 MB** | **~20–30 ms** | comfortable; ~150k chunks, ~1M edges |
| 20k | ~5 GB | ~900 MB | ~100 ms | still fine on 16 GB laptop |
| 50k | ~13 GB | ~2.3 GB | ~250 ms | RAM starts to bite; consider hnswlib sidecar |
| 100k+ | ~25+ GB | ~4.6 GB | ~500 ms+ | ANN by default; shard the DB |

Numbers assume 384-dim float32 unit-norm embeddings and brute-force `matrix @ query` matmul on a single laptop CPU.

Realistic working range for a single researcher's domain corpus (≤5k papers) is well within the comfort zone. Strictly better than today's NetworkX-pickle + NPZ setup at every size: lazy schema-level load, indexed lookups. Past 50k papers the in-RAM matrix becomes the first cliff, mitigated by an `hnswlib` sidecar (Phase 9).

## Implementation Phases

### Phase 0 — Decisions and Prototype (1 PR)

- Read this spec end to end. Confirm the locked defaults at the bottom of the file.
- Write `src/wikify/_prototype/sqlite_store.py` (~60–80 lines): in-memory schema build, load `tests/fixtures/tiny/`, run one BM25 query, one vector matmul, one recursive-CTE traversal. Verify each query plan with `EXPLAIN QUERY PLAN`.
- Write `notebooks/sqlite_store_schema_walkthrough.ipynb` against the prototype.

**Acceptance**: prototype reproduces a known query result on the tiny fixture; `EXPLAIN QUERY PLAN` shows `graph_out` / `graph_in` indexes used; the locked defaults are confirmed in writing.

### Phase 1 — Domain Store Layer (1 setup PR + 1 implementation PR)

**Setup PR** (sequential, must land first):

- `src/wikify/corpus/store/connection.py` — `connect()` with the full PRAGMA block, row factory, context manager.
- `src/wikify/corpus/store/schema.py` — DDL strings, `apply_schema()` idempotent.

**Implementation PR** — spawn 7 parallel agents in a single Agent tool block:

- **A** — `documents.py`, `chunks.py`, `authors.py`, `document_authors.py` CRUD. Source-of-truth: `src/wikify/corpus/models.py:55–121`. Preserve every field; promote query-driving columns; JSON-store the rest. Includes `equation_ids_json`.
- **B** — `bib_entries.py`, `chunk_citations.py`, DOI re-resolution, `corpus_papers.bib` / `cited_works.bib` exports. Round-trip with `.citestore.db` (read external lookup, copy resolved facts into local rows).
- **C** — `assets.py`, `chunk_assets.py`. Port `src/wikify/corpus/images_index.py` and `equations_index.py` write paths. PNGs and sidecars stay on disk; only metadata moves into rows.
- **D** — `graph.py` with `GraphStore`: `neighbors`, `traverse`, `path`, `subgraph`. UNION + depth-cap CTE default; path-tracking variant only when `path()` is called.
- **E** — `fts.py` external-content FTS5 setup + BM25 search + RRF fusion (k=60, top-200 each side).
- **F** — `vectors.py` with `embedding_spaces` / `embeddings` rows, BLOB encode/decode (float32 unit-norm), in-RAM `(n, d)` matrix cache, cosine search behind `store.vector_index()`.
- **G** — Phase 1 acceptance tests: connection pragmas, every CRUD path, BM25 top-k, cosine top-k, RRF determinism, one-hop neighbor, multi-hop CTE, cycle handling, path search, FK cascade on doc delete.

**Acceptance**: `pytest tests/wikify/store/` green; no new top-level pyproject dependency.

### Phase 2 — Ingest Dual-Write (1 PR)

- Update `src/wikify/ingest/dag.py` waves to write `wikify.db` alongside `vectors.npz` / `knowledge_graph.json` / `citations.json`. Keep legacy artifacts during the migration window.
- Reuse `_embed_chunks_incremental` (`src/wikify/ingest/pipeline.py:597`) for embeddings; dual-write into `embeddings`.
- Implement the `bib_entries` re-resolution step on doc add/update.

**Acceptance**:

- All existing tests pass unchanged.
- New parity test: for the tiny fixture, `documents` / `chunks` / `bib_entries` / `graph_edges` populated in `wikify.db` match what `Corpus.docs()` / `Corpus.chunks()` etc. yield from the legacy artifacts (modulo schema renames).
- Per-doc refresh test: ingesting one new doc into a 4-doc fixture leaves the other three docs' canonical and embedding rows unchanged.
- Inbound-resolution test: ingest doc1 with bib_entry citing `doi:X` (unresolved); ingest doc2 with `documents.doi='X'`; after refresh, `bib_entries.target_doc_id` and `graph_edges(doc1 → doc2, kind='references')` exist.

### Phase 3 — Query Path Migration (1 PR)

- Route `corpus.queries.find / show / list / traverse / sample` through the new store via `WIKIFY_QUERY_BACKEND=sqlite|legacy` (default `legacy`).
- Add `--rank bm25` and `--rank hybrid` to `corpus find`. Preserve `semantic`, `citation_count`, `pagerank`, `h_index`.
- Preserve MCP response shapes byte-for-byte.

**Acceptance**:

- Existing CLI/MCP tests pass with `WIKIFY_QUERY_BACKEND=sqlite`.
- `corpus find "GPC" --rank bm25` returns ≥1 hit on the GPC fixture without `--text`.
- Golden-output tests for ≥5 representative `corpus_find` / `corpus_traverse` invocations.

### Phase 4 — Wiki Store Projection (1 PR)

- Persist committed wiki pages on `wiki commit` to `wiki.db`.
- Wiki FTS, wiki embeddings, wiki graph edges (`wiki_page → chunk cites_evidence`, `wiki_page → wiki_page links_to`).
- Route `wiki find / show / list / traverse` through SQLite under the same feature flag.

**Acceptance**: empty-wiki path graceful; `wiki commit` of one page refreshes only that page's derived rows; wiki search returns the same hits as the current markdown-grep path on a fixture.

### Phase 5 — Metrics 5a (1 PR)

- `graph_views`, `node_metrics`, `edge_metrics`, `projection_status` tables.
- Cheap incremental metrics: in/out degree, citation count, coauthor count.
- `corpus find --by paper --rank citation_count` reads `node_metrics`.

**Acceptance**: per-doc refresh updates citation counts correctly without touching unaffected nodes.

### Phase 6 — Metrics 6 (1 PR)

- `wikify corpus metrics refresh --view corpus_citation` command.
- PageRank via `nx.pagerank` (or `scipy.sparse.linalg`) over CSR built on demand from `graph_edges`. Don't reimplement.
- h-index per author: port verbatim from `src/wikify/corpus/graph_build.py:398–412`.
- Stale flag on read.

**Acceptance**: PageRank values match current `knowledge_graph.json` PageRank within 1e-4 on the tiny fixture.

### Phase 7 — Cutover (1 PR)

- Default `WIKIFY_QUERY_BACKEND=sqlite`. Mark legacy artifacts as derivable exports.
- Keep ingest dual-writing them for one release window.

**Acceptance**: a fresh corpus build produces a working query path with only `wikify.db` plus markdown/images/docs source files.

### Phase 8 — Legacy Removal (1 PR; gated on Phase 7 soak)

- Delete `vectors.npz` writer, `knowledge_graph.json` writer, `citations.json` writer.
- Delete legacy code paths in `corpus.queries`.
- Blast-radius commit body lists every deleted file/symbol per CLAUDE.md.

### Phase 9 (deferred) — Accelerators

- Benchmark numpy matmul vs sqlite-vec brute force vs `hnswlib` sidecar at the actual corpus size.
- Ship only if measured win > 2× and Windows wheels exist.

## Required Tests

Beyond per-phase acceptance:

1. **Schema migration / FK cascade** — empty DB → schema applied → deleting a `documents` row cascades to `chunks` / `bib_entries` / `assets` / `chunk_citations` / `graph_edges` (where source matches), without affecting other docs.
2. **Embedding fingerprint reuse** — change embedder backend → new `embedding_spaces` row, old space rows queryable, no orphan embeddings.
3. **Hybrid RRF determinism** — fixed query, fixed corpus → top-k order is stable across runs.
4. **Recursive-CTE depth cap on cycles** — A→B→C→A graph → CTE returns each node at most once per depth bound.
5. **Inbound resolution** — see Phase 2 acceptance.
6. **Wiki-empty** — fresh corpus with no committed pages → `wiki find` returns empty; no errors.
7. **MCP golden** — record current outputs on the tiny fixture; replay under SQLite backend; diff is empty.
8. **Cross-platform smoke** — Windows + Linux + macOS open the same `wikify.db`. `unicode61` is in default SQLite builds on all three.

## Notebooks

Three:

- `notebooks/sqlite_store_schema_walkthrough.ipynb` — Phase 0.
- `notebooks/recursive_cte_traversal.ipynb` — visualize default vs path-tracking CTE on the tiny fixture.
- `notebooks/hybrid_retrieval_bm25_vector_rrf.ipynb` — worked RRF example.

`notebooks/kg_fluent_api.ipynb` already exists and covers BFS/DFS over the QueryBuilder; not duplicated.

## Files to Modify

- `src/wikify/corpus/queries.py` — query verbs; `text=true` literal grep at line 531.
- `src/wikify/corpus/graph.py` — NetworkX backend (lines 84–199); QueryBuilder (line 206+).
- `src/wikify/corpus/graph_build.py` — PageRank / h-index / citation count (lines 376–412).
- `src/wikify/corpus/vectors.py`, `vectors_meta.py` — embedding storage, fingerprint contract.
- `src/wikify/corpus/chunks.py` — `write_document`, `write_vector_store`, `write_knowledge_graph` writers.
- `src/wikify/corpus/models.py:55–121` — Document and Chunk models (canonical field source).
- `src/wikify/corpus/images_index.py`, `equations_index.py` — assets ingest.
- `src/wikify/citations/db.py` — DOI cache, kept separate.
- `src/wikify/ingest/pipeline.py` — `_parse_and_persist_worker`, `_embed_chunks_incremental` (line 597).
- `src/wikify/ingest/dag.py` — refresh waves A–F.
- `src/wikify/cli/corpus.py`, `cli/wiki.py` — CLI verbs.
- `src/wikify/mcp/server.py`, `mcp/context.py` — MCP tool surface, context binding.
- `src/wikify/bundle/wiki/queries.py`, `bundle/wiki/page.py` — wiki commit / read path.
- `src/wikify/eval/metrics.py` — verify whether eval reads `vectors.npz` / `knowledge_graph.json` directly; if so, route through the new store.
- `src/wikify/render/html/render.py` — verify render's read surface.
- `tests/fixtures/tiny/_build.py` — extend for SQLite parity.

## Locked Defaults

These are the answers to the design questions. Confirmed in Phase 0 before any non-prototype code lands.

| # | Question | Default |
|---|---|---|
| 1 | `wikify.db` location | `<corpus_root>/wikify.db` |
| 2 | Wiki rows | `<bundle_root>/wiki.db`, ATTACHed for cross-DB queries |
| 3 | FTS5 tokenizer | `unicode61 remove_diacritics 2 tokenchars '-'` (no porter) |
| 4 | FTS5 content shape | external-content over canonical `chunks` and `documents` |
| 5 | Reverse-edge policy | edges stored once; direction via `graph_in` / `graph_out` indexes |
| 6 | Similarity-edge policy | no persisted `similar_to` edges; vector search at query time |
| 7 | Migration window | dual-write legacy artifacts for 1 release after Phase 7 |
| 8 | Doc-level embeddings | deferred; compute from chunks at query time |
| 9 | First metrics | `citation_count` and `pagerank` only |

## Verification Plan

After Phase 3 ships:

```
uv run wikify corpus build tests/fixtures/tiny --out /tmp/sf-sqlite
uv run wikify corpus check /tmp/sf-sqlite --full
WIKIFY_QUERY_BACKEND=sqlite uv run wikify corpus find "GPC" --rank bm25
WIKIFY_QUERY_BACKEND=sqlite uv run wikify corpus find "growth per cycle" --rank hybrid
WIKIFY_QUERY_BACKEND=sqlite uv run wikify corpus traverse <doc_id> --to references
WIKIFY_QUERY_BACKEND=sqlite uv run wikify corpus sample --max 5 --strategy diverse
uv run pytest tests/wikify -q
uv run ruff check src/wikify tests/wikify
```

After Phase 6:

```
uv run wikify corpus metrics refresh --view corpus_citation
WIKIFY_QUERY_BACKEND=sqlite uv run wikify corpus find --by paper --rank citation_count --top-k 10
WIKIFY_QUERY_BACKEND=sqlite uv run wikify corpus find --by paper --rank pagerank --top-k 10
```

MCP smoke:

```
WIKIFY_CORPUS=/tmp/sf-sqlite WIKIFY_QUERY_BACKEND=sqlite uv run python -m wikify.mcp.server
```

## Kickoff Prompt for a Fresh Claude Session

```
Implement the SQLite query store for Wikify END TO END. Phases 0 through 8.
I will review the entire result at the end. Do not stop for confirmation
between phases. Do not open intermediate PRs for me to review. One
continuous implementation run, one final review.

Read these in full before writing any code:
- tasks/sqlite-query-store-plan.md (this spec - authoritative)
- CLAUDE.md (workflow, simplicity, blast-radius discipline)

Hard constraints (do not deviate):
- Pure stdlib sqlite3 + numpy + FTS5 + on-demand NetworkX/scipy.sparse.
- No sqlite-vec, no sqlite-muninn, no Apache AGE / Kuzu / DuckPGQ / Neo4j.
- No new top-level pyproject deps in Phases 1-6.
- Vectors live as float32 BLOBs in `embeddings`, loaded once per process
  into a numpy (n, d) matrix. matmul for cosine. Behind store.vector_index().
- FTS5 with `unicode61 remove_diacritics 2 tokenchars '-'`. No porter.
- Edges stored once. Direction via graph_in / graph_out indexes. No
  scope_type / scope_id columns.
- DOI cache (.citestore.db) stays separate. Resolved facts copied into
  bib_entries.
- Strategy stays in skills. CLI / MCP shapes preserved byte-for-byte.

The 9 design defaults at the bottom of the spec are LOCKED. Do not
re-litigate them; do not ask me to re-confirm. Echo them back once at
the start as a sanity check, then proceed.

Run order:

Phase 0 - Acknowledge the 9 locked defaults in writing. Write
src/wikify/_prototype/sqlite_store.py (60-80 lines) that builds the
schema in-memory, loads tests/fixtures/tiny/, runs one BM25 query, one
vector matmul, one recursive-CTE traversal; verify each with EXPLAIN
QUERY PLAN. Write notebooks/sqlite_store_schema_walkthrough.ipynb
against the prototype. Commit. Move on.

Phase 1 - Domain store at src/wikify/corpus/store/. First commit:
connection.py + schema.py with the full PRAGMA block. Second commit
(after the first lands locally): spawn 7 parallel agents in a single
Agent tool block:

  Agent A - documents/chunks/authors/document_authors CRUD + tests.
            Source-of-truth fields: src/wikify/corpus/models.py:55-121.
            Promote query-driving columns; JSON-store the rest. Include
            equation_ids_json.
  Agent B - bib_entries + chunk_citations + DOI re-resolution + the
            two .bib export commands. Round-trip with .citestore.db.
  Agent C - assets + chunk_assets. Port src/wikify/corpus/images_index.py
            and equations_index.py write paths into SQLite rows. PNGs
            and sidecars stay on disk; only metadata moves.
  Agent D - GraphStore: neighbors / traverse / path / subgraph. UNION +
            depth-cap CTE default; path-tracking variant only when
            path() is called. EXPLAIN QUERY PLAN on each traversal.
  Agent E - FTS5 external-content: chunks_fts over chunks (text only),
            documents_fts over documents (title + abstract). bm25() with
            column weights. RRF fusion (k=60) over BM25 top-200 and
            vector top-200.
  Agent F - embeddings table, BLOB encode/decode (float32 unit-norm),
            in-RAM (n, d) matrix cache, cosine search. Behind
            store.vector_index() so hnswlib swap-in is a one-file change.
  Agent G - Phase 1 acceptance tests covering everything above.

After all 7 agents return, integrate, fix any conflicts, run the gate,
commit. Move on.

Phase 2 - Ingest dual-write. Sequential. Implement per the spec, run
the parity test (one new doc into a 4-doc fixture leaves the other three
untouched) and the inbound-resolution test. Commit. Move on.

Phase 3 - Query path migration with WIKIFY_QUERY_BACKEND env flag.
Add --rank bm25 / --rank hybrid. MCP shapes byte-stable. Commit.

Phase 4 - Wiki store projection (wiki.db). Commit.
Phase 5 - Metrics 5a (cheap incremental). Commit.
Phase 6 - Metrics 6 (PageRank, h-index via explicit refresh). Commit.
Phase 7 - Cutover (default WIKIFY_QUERY_BACKEND=sqlite, keep dual-write).
Commit.
Phase 8 - Legacy removal. Delete vectors.npz / knowledge_graph.json /
citations.json writers and legacy corpus.queries paths. Blast-radius
commit body lists every deleted file/symbol.

Phase 9 (skip) - accelerators are deferred. Do not implement.

Verification gate, run after every phase commit:
  uv run ruff check src/wikify tests/wikify
  uv run pytest tests/wikify -q

If either fails: fix the cause. Do not skip hooks. Do not --no-verify.
Do not amend a previous phase's commit; create a new commit on top.

Final acceptance (run before declaring the work complete):
  uv run ruff check src/wikify tests/wikify
  uv run pytest tests/wikify -q
  uv run wikify corpus build tests/fixtures/tiny --out /tmp/sf-sqlite
  uv run wikify corpus check /tmp/sf-sqlite --full
  WIKIFY_QUERY_BACKEND=sqlite uv run wikify corpus find "GPC" --rank bm25
  WIKIFY_QUERY_BACKEND=sqlite uv run wikify corpus find "growth per cycle" --rank hybrid
  WIKIFY_QUERY_BACKEND=sqlite uv run wikify corpus traverse <doc_id> --to references
  WIKIFY_QUERY_BACKEND=sqlite uv run wikify corpus sample --max 5 --strategy diverse
  uv run wikify corpus metrics refresh --view corpus_citation
  WIKIFY_QUERY_BACKEND=sqlite uv run wikify corpus find --by paper --rank pagerank --top-k 10
  WIKIFY_CORPUS=/tmp/sf-sqlite WIKIFY_QUERY_BACKEND=sqlite uv run python -m wikify.mcp.server
  (smoke-test MCP startup, then exit)

Then post a single end-of-run summary: list every commit, every file
created/deleted/modified, the final pytest output (counts), and any
deviations from the spec with the reason. That summary is what I will
review.

Stop and re-plan only if a phase's acceptance criterion cannot be met
without violating a hard constraint. In that case: write a short
deviation note in tasks/sqlite-query-store-deviations.md, commit it,
continue with the next phase. Do not silently change the design.

Commit policy (per CLAUDE.md): list every file/symbol each change
touches in the commit body. Never include personal paths. ASCII only
in console output.
```
