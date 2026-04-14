# Knowledge Graph Design: Unified Academic Graph

## Problem

Citation data is scattered across 3 stores (docs JSON, citations.json,
doi_cache.db). The graph has no author nodes, no reverse citation index,
and no way for distill agents to query "who cites paper X?" or "what did
author Y write?". Metrics are computed ad-hoc by rebuilding nx.Graph
from flat edge lists each time.

## Use Cases (what the agent must be able to do)

### Paper queries
```
UC1: "Give me the conclusions of papers citing paper X"
  -> cited_by(X) -> [paper IDs] -> chunks(section="conclusions") -> text

UC2: "Give me refs [1-4] from paper X, formatted as BibTeX"
  -> paper(X).ord_refs[1:4] -> [target paper IDs] -> metadata -> format

UC3: "Give me all papers citing paper X so I can fetch them"
  -> cited_by(X) -> [paper nodes with metadata]

UC4: "Find chunks most closely related to semantic query Q"
  -> vector_search(Q, top_k=10) -> chunk IDs -> chunk text + paper context
```

### Author queries
```
UC5: "Give me all papers by author A"
  -> papers_by_author(A) -> [paper nodes]

UC6: "Give me authors who write papers semantically similar to author A"
  -> papers_by_author(A)
  -> vector_search(mean_embedding, filter=NOT_author_A)
  -> result papers -> their authors -> rank by frequency

UC7: "Give me authors whose papers are cited by author A's papers"
  -> papers_by_author(A) -> for each: references -> target papers
  -> their authors -> rank = who does A build on most

UC8: "Who collaborates with author A?"
  -> coauthors(A) -> [author nodes with paper count]
```

### Cross-cutting queries
```
UC9: "Find evidence chunks about concept X from papers that cite paper Y"
  -> cited_by(Y) -> [paper IDs]
  -> vector_search(X, filter=paper_ids) -> chunks with text

UC10: "Research community around topic Z"
  -> vector_search(Z) -> chunks -> unique papers -> their authors
  -> collaboration subgraph -> community detection

UC11: "Most influential papers in the corpus"
  -> PageRank on citation graph -> top N papers

UC12: "Author impact metrics"
  -> h_index(A), citation_count(A), paper_count(A), PageRank(A)
```

## Technology Choice

### Scale estimate (1000 papers)
```
Paper nodes:    ~1,500 (1000 corpus + 500 cited-only)
Author nodes:   ~500
Chunk nodes:    NOT in graph (too many, 50K; stay in VectorStore)
Edges:          ~15,000 (citations + authorship + collaboration)
Embeddings:     ~50,000 vectors (768-dim, in VectorStore)
```

### Decision: NetworkX + ChromaDB

**Graph (topology): NetworkX `nx.MultiDiGraph`**
- 15K edges loads in <10ms, PageRank in <50ms
- JSON persistence via `nx.node_link_data()` (~2MB for 1000 papers)
- All graph algorithms built-in (PageRank, community, shortest path)
- No server, no binary, no Windows issues
- Real graph DBs (Kuzu, Neo4j, Memgraph) solve problems at 10M+ edges

**Vectors (similarity): ChromaDB** (already in project)
- Embedded, works on Windows
- 50K chunks with HNSW index
- Filter by metadata (paper_id, section_type, author)
- Shared IDs with graph nodes for bridging

**Why not a single system?**
- DuckDB+VSS: graph traversal via recursive CTEs is awkward, VSS is experimental
- Kuzu: acquired by Apple, repo archived, fork immature
- LanceDB: great vectors, no graph traversal
- At this scale, two simple tools > one complex tool

### Bridging Pattern: Traverse Then Rank

Inspired by FalkorDB's graph+vector query model and Redis's
filter-then-KNN composition: every combined query follows the
**traverse-then-rank** pattern:

1. **Traverse** the graph to get a candidate set (IDs)
2. **Rank** candidates by vector similarity (scoped search)

```python
# UC9: "chunks about concept X from papers citing Y"
citing_papers = kg.cited_by("paper_Y")           # 1. traverse
paper_ids = [p.id for p in citing_papers]
chunks = vector_store.query(                       # 2. rank
    query_text="concept X",
    where={"paper_id": {"$in": paper_ids}},
    n_results=10,
)

# UC6: "authors who write papers similar to author A"
a_papers = kg.papers_by_author("A")              # 1. traverse
a_embedding = mean_pool(a_papers)                 # aggregate
similar = vector_store.query(                      # 2. rank
    query_embedding=a_embedding,
    where={"paper_id": {"$nin": [p.id for p in a_papers]}},
)
authors = kg.papers_to_authors(similar.paper_ids) # back to graph
```

This pattern works for any combined query:
- Graph narrows the search space (topology)
- VectorStore ranks within that space (semantics)
- Graph resolves results back to entities (context)

The two systems share string IDs — no ORM, no joins, no coupling.

## Graph Schema

### Node Types

```
Paper (paper_id: str)
  - title: str
  - year: int
  - doi: str
  - venue: str
  - authors: list[str]          # display names
  - kind: "corpus" | "cited"    # in corpus or referenced only
  - citation_count: int         # papers in corpus that cite this
  - sections: list[str]         # section headings (pointers to chunks)
  - images: list[str]           # image IDs (pointers to files)
  - equations: list[str]        # equation IDs
  - ord_refs: dict[int, str]    # [N] -> target paper_id
  - chunk_ids: list[str]        # pointers to VectorStore entries
  - markdown_path: str          # pointer to full text on disk
  - bibtex_key: str             # for bibliography formatting

Author (author_key: str)        # normalized: "lastname_firstinit"
  - display_name: str
  - orcid: str (optional)
  - paper_count: int
  - citation_count: int         # sum of citations across papers
  - h_index: int
  - pagerank: float
```

### Edge Types

```
Paper -> Paper
  CITES             directed, A references B
  SIMILAR           undirected, embedding cosine above threshold
  COUPLES           undirected, bibliographic coupling (shared refs)

Paper -> Author
  AUTHORED_BY       with position: "first" | "middle" | "last"

Author -> Author
  COLLABORATED      undirected, co-authored at least one paper
```

### What is NOT a graph node

| Data | Why not a node | Where it lives |
|------|---------------|----------------|
| Chunks | 50K at 1000 papers, too many for JSON graph | VectorStore (ChromaDB), indexed by chunk_id |
| Images | Binary files | Filesystem, referenced by paper.images |
| Equations | Small text | Paper node attribute (equation IDs -> lookup in doc JSON) |
| Full text | Large | Filesystem (markdown_path on Paper node) |
| Embeddings | 768-dim float vectors | VectorStore |

Chunks are the bridge: the graph points to them (paper.chunk_ids),
and the VectorStore holds their embeddings and text. The agent
resolves chunk_ids against the VectorStore for content.

## Query API

```python
class KnowledgeGraph:
    """Query interface for distill agents."""
    
    def __init__(self, G: nx.MultiDiGraph, vector_store): ...
    
    # Paper queries
    def paper(self, paper_id: str) -> dict
    def references(self, paper_id: str, ords: list[int] | None = None) -> list[dict]
    def cited_by(self, paper_id: str, corpus_only: bool = False) -> list[dict]
    def similar_papers(self, paper_id: str, top_k: int = 5) -> list[dict]
    
    # Author queries
    def author(self, name: str) -> dict
    def papers_by_author(self, name: str) -> list[dict]
    def coauthors(self, name: str) -> list[dict]
    def similar_authors(self, name: str, top_k: int = 5) -> list[dict]
    
    # Semantic search (delegates to VectorStore)
    def search_chunks(self, query: str, top_k: int = 10,
                      paper_ids: list[str] | None = None,
                      section: str | None = None) -> list[dict]
    def search_papers(self, query: str, top_k: int = 5) -> list[dict]
    
    # Combined graph + vector
    def chunks_from_citing_papers(self, paper_id: str, query: str,
                                  top_k: int = 5) -> list[dict]
    def authors_similar_to(self, author: str, top_k: int = 5) -> list[dict]
    
    # Metrics (computed once at build time, cached on nodes)
    def pagerank(self) -> dict[str, float]
    def communities(self) -> dict[str, int]
    def h_index(self, author: str) -> int
    def corpus_stats(self) -> dict
```

## Build Pipeline

```
Wave A: similarity + topics + images         (independent)
Wave B: heuristic enrichment + DOI resolution (citation metadata)
Wave C: citation edges + bibliography         (uses enriched data)
Wave D: build_knowledge_graph()               (NEW)
         1. Paper nodes from docs + cited works (CitationEntry)
         2. Author nodes from paper metadata
         3. Citation edges from doc.cites
         4. Authorship edges from metadata.authors
         5. Collaboration edges from co-authorship
         6. Paper similarity edges from doc embeddings
         7. Compute: PageRank, h-index, communities, citation_count
         8. Serialize: nx.node_link_data() -> graph.json
Wave E: derived artifacts (explorer, resave)
```

## Author Identity Resolution

For 50-1000 papers, simple normalization suffices:
```python
def author_key(name: str) -> str:
    parts = name.strip().split()
    last = parts[-1].lower()
    first_init = parts[0][0].lower() if parts else ""
    return f"{last}_{first_init}"
```
- "J. Smith" and "John Smith" -> "smith_j"
- ORCID matching when available from CrossRef/OpenAlex metadata
- No ML disambiguation (overkill at this scale)

## Migration from Current CorpusGraph

1. `CorpusGraph` dataclass -> `KnowledgeGraph` wrapper around `nx.MultiDiGraph`
2. All existing edge kinds preserved (contains -> chunk_ids on Paper node)
3. `build_corpus_graph()` -> `build_knowledge_graph()`
4. `graph.json` format: `nx.node_link_data()` (backward compatible for edges)
5. Consumers updated: explorer, community, metrics, distill preload
6. `citations.json` becomes redundant (graph has all the data)
