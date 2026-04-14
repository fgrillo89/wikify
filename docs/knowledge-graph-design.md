# Knowledge Graph Design: Unified Academic Graph

## Problem

Citation data is scattered across 3 stores (docs JSON, citations.json,
doi_cache.db). The graph has no author nodes, no reverse citation index,
and no way for distill agents to query "who cites paper X?" or "what did
author Y write?". Metrics are computed ad-hoc by rebuilding nx.Graph
from flat edge lists each time.

## Design: Typed Property Graph on NetworkX

Promote `CorpusGraph` from flat edge lists to a single `nx.MultiDiGraph`
with typed nodes and edges. This is the canonical graph structure that
ingest builds and distill queries.

### Node Types

```
Paper (doc_id)
  - title, year, doi, venue, authors: list[str]
  - kind: "corpus" | "cited"    # in corpus or only referenced
  - citation_count: int         # how many corpus papers cite this
  - ord_refs: dict[int, str]    # ordinal [N] -> target paper_id

Author (name_key)
  - display_name, orcid (optional)
  - h_index, paper_count, citation_count

Chunk (chunk_id)
  - doc_id, ord, section_path
```

### Edge Types

```
Paper -> Paper
  cites           : directed, A cites B
  cites_same      : undirected, bibliographic coupling

Paper -> Author
  authored_by     : with position (first, middle, last)

Author -> Author
  collaborated    : undirected, co-authored at least one paper

Chunk -> Chunk
  similar_knn     : embedding cosine top-K
  similar_strong  : cosine >= threshold

Paper -> Chunk
  contains        : doc contains chunk
```

### Storage

- `graph.json` via `nx.node_link_data()` / `nx.node_link_graph()`
- Same file, richer structure. Backward compatible (old edge kinds
  preserved, new node types added).
- DOI cache (`data/doi_cache.db`) remains the ingestion-time cache for
  CrossRef/OpenAlex results. Not a graph store.

### API for Distill Agents

```python
class KnowledgeGraph:
    """Query interface over the academic knowledge graph."""
    
    def __init__(self, G: nx.MultiDiGraph): ...
    
    # Paper queries
    def paper(self, doc_id: str) -> dict           # node attributes
    def references(self, doc_id: str, ords: list[int] = None) -> list[dict]
        # Get refs [1-4] or all
    def cited_by(self, doc_id: str, corpus_only: bool = False) -> list[dict]
        # Reverse citation lookup
    def papers_by_author(self, author: str) -> list[dict]
    
    # Author queries
    def author(self, name: str) -> dict             # node attributes + metrics
    def coauthors(self, name: str) -> list[dict]
    def author_citations(self, name: str) -> int    # total citations
    
    # Graph metrics (computed once, cached on nodes)
    def pagerank(self) -> dict[str, float]
    def communities(self) -> dict[str, int]
    def h_index(self, author: str) -> int
    
    # Chunk retrieval (for distill)
    def similar_chunks(self, chunk_id: str, top_k: int = 5) -> list[str]
    def chunks_from_cited_paper(self, doc_id: str, concept: str) -> list[str]
```

### Build Pipeline

```
Wave A: similarity + topics + images         (independent)
Wave B: heuristic enrichment + DOI resolution (citation metadata)
Wave C: citation edges + bibliography         (uses enriched data)
Wave D: build_knowledge_graph()               (NEW: unified graph)
         - Paper nodes from docs + cited works
         - Author nodes extracted from metadata
         - Citation edges from doc.cites
         - Authorship edges from metadata.authors
         - Collaboration edges from co-authorship
         - Chunk edges from embeddings
         - Compute metrics: PageRank, h-index, communities
Wave E: derived artifacts (explorer, resave)
```

### Migration from Current CorpusGraph

1. `CorpusGraph` dataclass replaced by `KnowledgeGraph` wrapper
2. All existing edge kinds preserved (contains, similar_knn, etc.)
3. New node types (Author) and edge types (authored_by, collaborated) added
4. `build_corpus_graph()` becomes `build_knowledge_graph()`
5. Consumers updated: explorer, community, metrics, distill preload

### Author Identity Resolution

For 50-1000 papers, simple name normalization:
- Normalize: "J. Smith" and "John Smith" -> same author node
- Key: lowercase last name + first initial (covers 95% of cases)
- ORCID matching when available (from CrossRef/OpenAlex metadata)
- No ML disambiguation needed at this scale

### Traversal Patterns

The graph must support multi-hop traversals in any direction.
Every query below should be a one-liner against the API.

**Author -> Papers -> Chunks (and back):**
```
author("Smith")
  -> papers_by_author("Smith")           # [Paper nodes]
  -> for each paper: G.successors(p, "contains")  # [Chunk nodes]
  -> chunk.text, chunk.embedding          # actual content

chunk("c_123")
  -> G.predecessors(c, "contains")        # Paper node
  -> G.predecessors(p, "authored_by")     # Author nodes
```

**"Authors who write similar papers to target author":**
```
papers_by_author("Smith")
  -> for each paper: G.neighbors(p, "doc_similar")  # similar papers
  -> for each similar: G.predecessors(p, "authored_by")  # their authors
  -> rank by frequency (authors who appear most = most similar)
```

**"What papers cite this author's work?":**
```
papers_by_author("Smith")
  -> for each paper: G.predecessors(p, "cites")    # papers that cite it
  -> unique set of citing papers
  -> their authors = "who builds on Smith's work"
```

**"Find evidence chunks about concept X from papers that cite paper Y":**
```
cited_by("paper_Y")
  -> for each citing paper: G.successors(p, "contains")  # chunks
  -> similarity_search(chunks, concept_X)                 # vector search
```

**"Research community around topic Z":**
```
search_chunks(topic_Z)
  -> unique papers (via "contains" edges)
  -> their authors (via "authored_by" edges)
  -> author collaboration subgraph
  -> community detection on that subgraph
```

### Relationship to Existing Data Structures

```
KnowledgeGraph (nx.MultiDiGraph)
  |
  +-- Paper nodes
  |     carries: CitationEntry metadata (title, doi, authors, venue)
  |     links to: Author nodes (authored_by), Chunk nodes (contains)
  |     links to: other Paper nodes (cites, doc_similar, cites_same)
  |
  +-- Author nodes
  |     carries: display_name, orcid, metrics (h_index, citation_count)
  |     links to: Paper nodes (authored_by, reverse)
  |     links to: other Author nodes (collaborated)
  |
  +-- Chunk nodes
        carries: doc_id, ord, section_path
        links to: Paper node (contains, reverse)
        links to: other Chunk nodes (similar_knn, similar_strong, co_section)
        embeddings: via VectorStore (separate, indexed by chunk_id)

VectorStore (existing)
  - chunk embeddings for similarity search
  - accessed via chunk_id from graph traversal
  - NOT inside the graph (too large), but reachable via node ID

DOI Cache (data/doi_cache.db)
  - ingestion-time cache only
  - NOT a query-time store
  - resolved metadata flows into Paper nodes at build time

Document JSON (corpus/docs/*.json)
  - source of truth for parsed content
  - feeds into Paper + Chunk nodes at graph build time
  - CitationEntry list -> Paper.ord_refs mapping
```

### Design Principle

The graph is the **query-time interface**. Everything distill needs
is reachable by traversing the graph. The graph is **built from**
Documents, Citations, Chunks, and VectorStore at ingest time but
**does not depend on them** at query time. Once built, the graph
is self-contained.

### What This Enables

1. Writer can cite bibliography entries with context from the graph
2. Extractor knows which references a chunk makes and where they lead
3. Query engine can follow citation chains across papers and authors
4. Person pages grounded in actual publication/citation/collaboration data
5. Corpus-level analytics: most influential papers, key authors,
   research communities, citation flow
6. "Similar authors" via paper similarity transitivity
7. Multi-hop reasoning: concept -> chunks -> papers -> authors -> collaborators
