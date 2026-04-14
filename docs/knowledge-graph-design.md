# Knowledge Graph Design: Unified Academic Graph

## Vision

Build a knowledge fabric that starts with scientific papers and
generalizes to emails, slides, and notes. The graph is the agent's
primary reasoning interface — it traverses relationships and scopes
vector search to graph neighborhoods.

The architecture follows the "Librarian-Miner-Writer" pattern:
- **Librarian**: traverses the graph, scores candidates, fetches context
- **Miner/Extractor**: extracts concepts, equations, relationships
- **Writer**: synthesizes evidence into articles with full provenance

## Design Principles

1. **Fluent API.** Agents chain traversals like natural reasoning:
   `kg.paper("X").cited_by().sections(type="conclusions").search("topic")`

2. **Backend-agnostic.** NetworkX today, FalkorDB tomorrow. Agent code
   unchanged.

3. **Traverse then rank.** Graph narrows the search space (topology),
   VectorStore ranks within it (semantics).

4. **Schema-flexible.** New source types = new node types + edge types.
   No migration, no rewrite.

5. **Provenance is first-class.** Every node and edge carries source_id
   and confidence. Every wiki sentence traces to a specific source.

## Fluent Query API

```python
class KnowledgeGraph:
    """Entry point. Returns QueryBuilder for fluent chaining."""
    
    def paper(self, paper_id: str) -> QueryBuilder
    def author(self, author_key: str) -> QueryBuilder
    def papers(self, **filters) -> QueryBuilder     # all papers, optionally filtered
    def authors(self, **filters) -> QueryBuilder
    def chunks(self, **filters) -> QueryBuilder
    
    # Convenience for direct vector search (no graph traversal first)
    def search(self, query: str, top_k: int = 10) -> list[dict]
    
    # Metrics (pre-computed at build time)
    def corpus_stats(self) -> dict


class QueryBuilder:
    """Lazy, composable query builder over the knowledge graph.
    
    Each traversal method returns a new QueryBuilder scoped to the
    result set. Nothing executes until a terminal method is called.
    """
    
    # ---- Traversal (returns new QueryBuilder) ----
    
    # Citation graph
    def cited_by(self) -> QueryBuilder          # papers that cite these
    def references(self, ords=None) -> QueryBuilder  # papers cited by these
    def neighborhood(self, hops=1) -> QueryBuilder   # N-hop graph neighbors
    
    # Authorship
    def authors(self) -> QueryBuilder           # authors of these papers
    def papers(self) -> QueryBuilder            # papers by these authors
    def coauthors(self) -> QueryBuilder         # co-authors of these authors
    
    # Document structure
    def sections(self, type=None) -> QueryBuilder  # sections of these papers
    def chunks(self) -> QueryBuilder               # chunks of these papers/sections
    def figures(self) -> QueryBuilder               # figures of these papers
    def equations(self) -> QueryBuilder             # equations of these papers
    
    # Phase 2
    def project(self) -> QueryBuilder           # project these belong to
    def thread(self) -> QueryBuilder            # email thread
    
    # ---- Filters (returns narrowed QueryBuilder) ----
    def where(self, **kwargs) -> QueryBuilder   # filter by attributes
    def of_type(self, kind: str) -> QueryBuilder  # filter by node type
    def since(self, year: int) -> QueryBuilder  # year >= N
    def top(self, n: int, by: str) -> QueryBuilder  # top N by metric
    
    # ---- Vector search (scoped to current set) ----
    def search(self, query: str, top_k: int = 10) -> list[dict]
    
    # ---- Terminals (execute and return) ----
    def collect(self) -> list[dict]             # materialize all nodes
    def ids(self) -> list[str]                  # just the IDs
    def count(self) -> int                      # count matches
    def first(self) -> dict | None              # first result or None
    def exists(self) -> bool                    # any matches?
    
    # ---- Metrics on current set ----
    def pagerank(self) -> dict[str, float]
    def citation_count(self) -> dict[str, int]
```

## Use Cases as Fluent Chains

### Paper queries

```python
# UC1: "Give me the conclusions of papers citing paper X"
kg.paper("X").cited_by().sections(type="conclusions").chunks().collect()

# UC2: "Give me refs [1-4] from paper X"
kg.paper("X").references(ords=[1,2,3,4]).collect()

# UC3: "All papers citing X, sorted by year"
kg.paper("X").cited_by().top(20, by="year").collect()

# UC4: "Chunks about memristor switching from the whole corpus"
kg.search("memristor switching mechanism", top_k=10)
```

### Author queries

```python
# UC5: "All papers by author A"
kg.author("smith_j").papers().collect()

# UC6: "Authors who write papers similar to author A"
kg.author("smith_j").papers().search("", top_k=20).authors().collect()

# UC7: "Who does author A cite most?"
kg.author("smith_j").papers().references().authors().collect()
# -> count by frequency to find most-cited authors

# UC8: "Who collaborates with author A?"
kg.author("smith_j").coauthors().collect()
```

### Combined graph + vector

```python
# UC9: "Evidence about concept X from papers citing Y"
kg.paper("Y").cited_by().chunks().search("concept X", top_k=5)

# UC10: "Research community around topic Z"
kg.search("topic Z", top_k=30)  # chunks
# -> group by paper -> authors -> collaboration subgraph

# UC11: "Most influential papers"
kg.papers().top(10, by="pagerank").collect()

# UC12: "Author impact"
kg.author("smith_j").papers().cited_by().count()  # total citations
```

### Multimodal

```python
# "Find I-V curve figures across the corpus"
kg.papers().figures().search("I-V curve characteristic", top_k=5)

# "Equations related to memristor model"
kg.papers().equations().search("memristor switching model", top_k=5)

# "Expand equation context for variable definitions"
eq = kg.paper("X").equations().where(label="Eq. 1").first()
context = kg.paper("X").chunks().where(contains_equation=eq["id"]).collect()

# "Figures from papers that cite X"
kg.paper("X").cited_by().figures().collect()
```

### Librarian decision logic

```python
# Foundation check: cited by >3 papers -> fetch full sections
paper = kg.paper("X")
if paper.cited_by().count() > 3:
    # Foundation paper: get full Long Retrieval Units
    context = paper.sections().chunks().collect()
else:
    # Specific reference: targeted search
    context = paper.chunks().search(query, top_k=3)

# Math-Guard: auto-expand equation context
chunk = kg.paper("X").chunks().where(id="c_123").first()
if chunk.get("equations"):
    # Expand to preceding 1000 tokens for variable definitions
    expanded = kg.expand_context(chunk["id"], extra_tokens=1000)
```

### Phase 2: cross-source queries

```python
# "Papers related to Project Icarus"
kg.project("Icarus").papers().collect()

# "Emails about a topic that reference a specific paper"
kg.paper("X").cited_by(type="email").collect()

# "Timeline: emails + papers + notes for Project Icarus, ordered by date"
kg.project("Icarus").where(type__in=["paper","email","note"]).top(50, by="date").collect()
```

## Architecture

```
┌──────────────────────────────────────┐
│         KnowledgeGraph               │
│         + QueryBuilder               │  <- agents call this
│  (fluent API, backend-agnostic)      │
├──────────┬───────────────────────────┤
│  Graph   │      VectorStore          │
│ Backend  │       Backend             │
├──────────┼───────────────────────────┤
│ Phase 1: │  ChromaDB (existing)      │
│ NetworkX │                           │
│ + dicts  │                           │
├──────────┼───────────────────────────┤
│ Phase 2: │  ChromaDB or LanceDB     │
│ FalkorDB │  (if needed)             │
│ or Kuzu  │                           │
└──────────┴───────────────────────────┘
```

## Node Types

### Documents

```
PaperNode
  id, title, year, doi, venue, authors: list[str]
  kind: "corpus" | "cited"
  citation_count: int
  ord_refs: dict[int, str]      # [N] -> target paper_id
  markdown_path: str            # -> full text on disk
  bibtex_key: str
  pagerank: float

AuthorNode
  id, display_name, orcid
  paper_count, citation_count, h_index, pagerank: float
```

### Content units

```
ChunkNode
  id, paper_id, ord, section, section_type
  char_span: tuple[int, int]
  token_count: int
  equation_ids: list[str]
  figure_refs: list[str]
  # Text + embedding in VectorStore, not in graph

SectionNode
  id: "{paper_id}::{heading}"
  paper_id, heading, level
  chunk_ids: list[str]          # ordered chunks
```

### Multimodal entities

```
FigureNode
  id, paper_id, label, caption, path, page
  near_chunk_ids: list[str]

EquationNode
  id, paper_id, latex, label, kind, context, chunk_id
```

### Phase 2

```
EmailNode       id, thread_id, from, to, subject, date, project
SlideNode       id, deck_id, title, speaker_notes
NoteNode        id, tags, date
ProjectNode     id, name, description
```

## Edge Types

```
# Document structure
CONTAINS_CHUNK, CONTAINS_SECTION, CHUNK_IN_SECTION
CONTAINS_FIGURE, CONTAINS_EQUATION
FIGURE_NEAR_CHUNK, EQUATION_IN_CHUNK

# Citation & authorship
CITES (directed), AUTHORED_BY (with position), COLLABORATED
COUPLES (bibliographic coupling), SIMILAR_PAPER (embedding)

# Phase 2
PART_OF_THREAD, PART_OF_PROJECT, DISCUSSES, MENTIONS
```

## Backend: Phase 1 Implementation

```python
class NetworkXBackend:
    """Phase 1: NetworkX + inverted dict indexes."""
    
    G: nx.MultiDiGraph
    
    # Hot-path indexes (rebuilt from G at load time)
    _cited_by: dict[str, set[str]]
    _papers_of: dict[str, set[str]]
    _coauthors: dict[str, set[str]]
    _sections_of: dict[str, list[str]]
    _figures_of: dict[str, list[str]]
    _equations_of: dict[str, list[str]]
    _pagerank: dict[str, float]
    _h_index: dict[str, int]
    
    def persist(self, path: Path):
        json.dumps(nx.node_link_data(self.G)) -> path
    
    def load(self, path: Path):
        self.G = nx.node_link_graph(json.loads(path))
        self._rebuild_indexes()  # O(E) scan, <100ms
```

NetworkX used for: PageRank, community detection, shortest path
(batch computation at build time). Results cached in index dicts.
Query-time: pure dict/set operations, never touches NetworkX.

## QueryBuilder Implementation

```python
class QueryBuilder:
    def __init__(self, kg, node_ids: set[str], node_type: str | None):
        self._kg = kg
        self._ids = node_ids
        self._type = node_type
    
    def cited_by(self) -> QueryBuilder:
        result = set()
        for pid in self._ids:
            result |= self._kg._backend._cited_by.get(pid, set())
        return QueryBuilder(self._kg, result, "paper")
    
    def search(self, query: str, top_k: int = 10) -> list[dict]:
        # Terminal: execute vector search scoped to current IDs
        return self._kg._vector_store.query(
            query_texts=[query],
            where={"paper_id": {"$in": list(self._ids)}},
            n_results=top_k,
        )
    
    def collect(self) -> list[dict]:
        # Terminal: materialize all nodes
        return [self._kg._backend.G.nodes[nid] for nid in self._ids]
    
    def count(self) -> int:
        return len(self._ids)
```

Each traversal method: look up the inverted index, return new
QueryBuilder with the result set. O(k) per step where k = result size.
Lazy until a terminal is called.

## Build Pipeline

```
Wave A: similarity + topics + images
Wave B: heuristic enrichment + DOI resolution
Wave C: citation edges + bibliography
Wave D: build_knowledge_graph()
         1. Paper nodes from docs + cited works
         2. Author nodes from paper metadata
         3. Chunk/Section/Figure/Equation nodes from doc content
         4. Citation + authorship + collaboration edges
         5. Similarity edges from embeddings
         6. Metrics: PageRank, h-index, communities
         7. Build inverted indexes
         8. Persist: graph.json
Wave E: derived artifacts
```

## What is NOT in the graph

| Data | Where | Resolved via |
|------|-------|-------------|
| Chunk text + embeddings | ChromaDB | `search()` / chunk_id lookup |
| Full markdown text | Filesystem | `paper.markdown_path` |
| Images (binary) | Filesystem | `figure.path` |
| DOI cache | data/doi_cache.db | Ingestion-time only |
| BibTeX files | Generated | `paper.bibtex_key` |

## Migration Path

```
Phase 1 (now):   NetworkX + ChromaDB, JSON persistence, dict indexes
Phase 2 (later): FalkorDB/Kuzu backend, same QueryBuilder API

Trigger: graph JSON > 50MB, or need concurrent writes, or need
server-mode for multi-user access.

Migration: reimplement NetworkXBackend -> FalkorDBBackend.
QueryBuilder and agent code unchanged.
```

## Testing

The fluent API is highly testable:

```python
def test_cited_by():
    kg = build_test_graph()  # small fixture
    result = kg.paper("A").cited_by().collect()
    assert len(result) == 2
    assert {"B", "C"} == {r["id"] for r in result}

def test_search_scoped_to_citing_papers():
    kg = build_test_graph()
    results = kg.paper("A").cited_by().chunks().search("topic", top_k=3)
    # All results come from papers that cite A
    assert all(r["paper_id"] in kg.paper("A").cited_by().ids() for r in results)

def test_fluent_chain():
    # Each step narrows the scope
    all_papers = kg.papers().count()
    cited = kg.paper("X").cited_by().count()
    assert cited < all_papers
```
