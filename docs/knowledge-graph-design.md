# Knowledge Graph Design: Unified Academic Graph

> **Status (2026-04-15): Migration complete.** `CorpusGraph` is deleted.
> `KnowledgeGraph` (`citestore/graph.py`) and `build_knowledge_graph`
> (`citestore/graph_build.py`) are the live implementations. Chunk
> similarity edges (`similar_knn`, `similar_strong`, `STRONG_COS`) are
> removed; vector search via VectorStore replaces them. This document is
> retained as a historical design record.

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

### Embeddings, Images, and Equations

**Embeddings stay outside the graph.** Chunk embeddings are 768-dim
float vectors — too large to serialize in JSON. The VectorStore holds
them in a numpy matrix indexed by chunk_id. The graph stores chunk_ids
as nodes; the VectorStore is the companion index for similarity search.

```
Pattern: "find chunks about concept X from paper Y"
  1. Graph: paper_Y -> contains -> [chunk_1, chunk_2, ...]  # node IDs
  2. VectorStore: query(concept_X, filter=chunk_ids)          # similarity
```

Two structures, one query. The graph provides the topology (which chunks
belong to which paper); the VectorStore provides the geometry (which
chunks are semantically close). Neither replaces the other.

**Images and equations are node attributes, not separate node types.**
At this scale (50-1000 papers, ~50 images per paper), adding Image and
Equation nodes would bloat the graph without adding traversal value.
Instead, they live as attributes on Chunk and Paper nodes:

```
Chunk node attributes:
  equation_ids: list[str]     # equations in this chunk
  figure_refs: list[str]      # figures referenced by this chunk

Paper node attributes:
  images: list[ImageRef]      # all images from this paper
  equations: list[EquationRef] # all equations from this paper
```

If a distill agent needs "the figure that shows the I-V curve from
paper X", the traversal is:
```
paper_X -> contains -> chunks -> filter(figure_refs contains "fig3")
  -> chunk.text (context around the figure reference)
paper_X.images -> filter(id == "fig3") -> ImageRef.path
```

**When would images/equations become graph nodes?** If we needed to
answer "which papers share similar figures?" or "which equations appear
across multiple papers?" — cross-paper entity linking. At that point,
Image and Equation become first-class nodes with their own edges. For
now, attributes suffice.

### What fits in one structure vs. two

| Data | In the graph? | Why |
|------|--------------|-----|
| Paper metadata | Yes (node attrs) | Traversed constantly |
| Author identity | Yes (node type) | Enables author queries |
| Citation edges | Yes (edge type) | Core topology |
| Chunk text | Yes (node attr, truncated) | Needed for context |
| Chunk embeddings | No (VectorStore) | Too large, numpy-native |
| Images | No (filesystem + node attr ref) | Binary blobs |
| Equations | Yes (node attr on chunk) | Small, text-based |
| BibTeX | No (generated from graph) | Derived artifact |
| DOI cache | No (ingestion-time only) | Not needed at query time |

The graph + VectorStore together form the complete query-time index.
Everything else (JSON files, DOI cache, .bib files) is either a source
(feeds into the graph at build time) or a derived artifact (generated
from the graph at export time).

### Design Principle

The graph is the **query-time interface**. Everything distill needs
is reachable by traversing the graph. The graph is **built from**
Documents, Citations, Chunks, and VectorStore at ingest time but
**does not depend on them** at query time. Once built, the graph
is self-contained (except for embeddings in VectorStore and images
on disk).

### What This Enables

1. Writer can cite bibliography entries with context from the graph
2. Extractor knows which references a chunk makes and where they lead
3. Query engine can follow citation chains across papers and authors
4. Person pages grounded in actual publication/citation/collaboration data
5. Corpus-level analytics: most influential papers, key authors,
   research communities, citation flow
6. "Similar authors" via paper similarity transitivity
7. Multi-hop reasoning: concept -> chunks -> papers -> authors -> collaborators
