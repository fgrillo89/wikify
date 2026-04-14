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

### Decision: Redis-Inspired Indexes + ChromaDB

Inspired by Redis's design philosophy: build fast, flexible queries
from simple primitives (sets, sorted sets, hashes) rather than a
monolithic graph object.

**Topology: typed Python dicts and sets (Redis-style indexes)**

Instead of one `nx.MultiDiGraph`, maintain explicit inverted indexes:

```python
@dataclass
class AcademicIndex:
    """Redis-style inverted indexes over the knowledge graph."""
    
    # Entity stores (Redis HASH analog)
    papers: dict[str, PaperNode]        # paper_id -> metadata
    authors: dict[str, AuthorNode]      # author_key -> metadata
    
    # Relationship indexes (Redis SET analog)
    cites: dict[str, set[str]]          # paper -> papers it cites
    cited_by: dict[str, set[str]]       # paper -> papers that cite it
    authored_by: dict[str, set[str]]    # paper -> author_keys
    papers_of: dict[str, set[str]]      # author -> paper_ids
    coauthors: dict[str, set[str]]      # author -> co-author_keys
    
    # Lookup indexes (Redis SET for secondary index)
    papers_by_year: dict[int, set[str]]
    papers_by_venue: dict[str, set[str]]
    ord_refs: dict[str, dict[int, str]] # (paper_id, ord) -> target paper_id
    
    # Ranked indexes (Redis SORTED SET analog)
    pagerank: dict[str, float]          # paper_id -> score
    citation_count: dict[str, int]      # paper_id -> count
    h_index: dict[str, int]             # author_key -> h-index
```

Why this over NetworkX:
- **5-10x faster** for attribute lookups and set queries
- **3-5x less memory** (no nested-dict-of-dict overhead)
- **Debuggable** with `print()` — plain dicts, not opaque graph objects
- Compound queries are one-liners: `papers_of[A] & cited_by[B]`
- NetworkX kept **only for batch algorithms** (PageRank, community
  detection) — run once at build time, dump results into sorted dicts

**Vectors (similarity): ChromaDB** (already in project)
- Embedded, works on Windows
- 50K chunks with HNSW index
- Filter by metadata (paper_id, section_type, author)
- Shared IDs with indexes for bridging

**Why not a single system?**
- DuckDB+VSS: graph traversal via recursive CTEs is awkward
- Kuzu: acquired by Apple, repo archived
- At this scale, simple primitives > complex query engines

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
    """Query interface for distill agents.
    
    Backed by AcademicIndex (Redis-style dicts/sets) for topology
    and ChromaDB for vector similarity. NetworkX used only for
    batch metric computation at build time.
    """
    
    def __init__(self, index: AcademicIndex, vector_store): ...
    
    # Paper queries — direct dict/set lookups, O(1) or O(k)
    def paper(self, paper_id: str) -> PaperNode
    def references(self, paper_id: str, ords: list[int] | None = None) -> list[PaperNode]
        # index.ord_refs[paper_id][n] for each n in ords
    def cited_by(self, paper_id: str) -> list[PaperNode]
        # [index.papers[p] for p in index.cited_by[paper_id]]
    def similar_papers(self, paper_id: str, top_k: int = 5) -> list[PaperNode]
        # vector_store.query(paper_embedding, n=top_k)
    
    # Author queries — set operations
    def author(self, name: str) -> AuthorNode
    def papers_by_author(self, name: str) -> list[PaperNode]
        # [index.papers[p] for p in index.papers_of[name]]
    def coauthors(self, name: str) -> list[AuthorNode]
        # [index.authors[a] for a in index.coauthors[name]]
    def similar_authors(self, name: str, top_k: int = 5) -> list[AuthorNode]
        # mean_pool(author's papers) -> vector_search -> result authors
    
    # Compound queries — set intersection + vector ranking
    def papers_by_author_cited_by(self, author: str, paper: str) -> list[PaperNode]
        # index.papers_of[author] & index.cited_by[paper]  <- one line
    def chunks_from_citing_papers(self, paper_id: str, query: str,
                                  top_k: int = 5) -> list[dict]
        # citing = index.cited_by[paper_id]
        # vector_store.query(query, where={"paper_id": {"$in": citing}})
    
    # Semantic search — delegates to VectorStore with optional filters
    def search_chunks(self, query: str, top_k: int = 10,
                      paper_ids: list[str] | None = None,
                      section: str | None = None) -> list[dict]
    def search_papers(self, query: str, top_k: int = 5) -> list[PaperNode]
    
    # Metrics — pre-computed at build time, O(1) lookup
    def paper_pagerank(self, paper_id: str) -> float
        # index.pagerank[paper_id]
    def author_h_index(self, author: str) -> int
        # index.h_index[author]
    def corpus_stats(self) -> dict
```

**Why this is better than NetworkX for queries:**

```python
# NetworkX: manual traversal, scans all edges
a_papers = {n for n in G.neighbors("A") if G.nodes[n]["kind"] == "paper"}
b_citing = {src for src, _ in G.in_edges("B") if G[src]["B"]["type"] == "cites"}
result = a_papers & b_citing

# AcademicIndex: pre-computed sets, one set intersection
result = index.papers_of["A"] & index.cited_by["B"]
```

NetworkX is kept ONLY for batch algorithm computation (PageRank,
Louvain community detection) at build time. Results are dumped
into the index dicts. At query time, NetworkX is never touched.

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
