# Knowledge Graph Design: Unified Academic Graph

## Vision

Build a knowledge fabric that starts with scientific papers and
generalizes to emails, slides, and notes. The graph is the agent's
primary reasoning interface — it doesn't call a "search API", it
traverses relationships and scopes vector search to graph neighborhoods.

The architecture follows the "Librarian-Miner-Writer" pattern:
- **Librarian**: traverses the graph, scores candidates, fetches context
- **Miner/Extractor**: extracts concepts, equations, relationships from text
- **Writer**: synthesizes evidence into articles with full provenance

## Design Principles

1. **API-first, backend-agnostic.** The `KnowledgeGraph` class defines
   the query interface. The backend (NetworkX, FalkorDB, Kuzu) is
   swappable without changing agent code.

2. **Traverse then rank.** Graph narrows the search space (topology),
   VectorStore ranks within it (semantics). Two systems, one pattern.

3. **Schema-flexible.** Adding a new source type (emails, slides) means
   adding a node type and edge types — no schema migration, no rewrite.

4. **Provenance is first-class.** Every node and edge carries source_id,
   ingest_timestamp, and confidence. Every wiki sentence traces back to
   a specific source.

## Architecture

```
┌─────────────────────────────────────┐
│         KnowledgeGraph API          │  <- agents call this
│  paper() | cited_by() | search()   │
│  author() | coauthors() | refs()   │
│  search_chunks() | neighborhood()  │
├──────────┬──────────────────────────┤
│  Graph   │      VectorStore        │
│ Backend  │       Backend           │
├──────────┼──────────────────────────┤
│ Phase 1: │  ChromaDB (existing)    │
│ NetworkX │                         │
│ + dicts  │                         │
├──────────┼──────────────────────────┤
│ Phase 2: │  ChromaDB or LanceDB   │
│ FalkorDB │  (if needed)           │
│ or Kuzu  │                         │
└──────────┴──────────────────────────┘
```

The API layer is ~200 lines. Backend swap = reimplement the adapter,
agent code unchanged.

## KnowledgeGraph API (backend-agnostic)

```python
class KnowledgeGraph:
    """The agent's interface to the knowledge fabric.
    
    Backend-agnostic: works on NetworkX+dicts (phase 1) or
    FalkorDB/Kuzu (phase 2). Agents never touch the backend directly.
    """
    
    # ---- Node lookup ----
    def paper(self, paper_id: str) -> PaperNode
    def author(self, author_key: str) -> AuthorNode
    def node(self, node_id: str) -> dict    # generic, any type
    
    # ---- Graph traversal ----
    def references(self, paper_id: str,
                   ords: list[int] | None = None) -> list[PaperNode]
    def cited_by(self, paper_id: str) -> list[PaperNode]
    def papers_by_author(self, author: str) -> list[PaperNode]
    def coauthors(self, author: str) -> list[AuthorNode]
    def neighborhood(self, node_id: str,
                     hops: int = 1) -> list[dict]
    
    # ---- Semantic search (delegates to VectorStore) ----
    def search_chunks(self, query: str, top_k: int = 10,
                      paper_ids: list[str] | None = None,
                      section: str | None = None) -> list[ChunkResult]
    def search_papers(self, query: str,
                      top_k: int = 5) -> list[PaperNode]
    
    # ---- Combined traverse + rank ----
    def search_in_neighborhood(self, node_id: str, query: str,
                               hops: int = 1,
                               top_k: int = 5) -> list[ChunkResult]
    def similar_authors(self, author: str,
                        top_k: int = 5) -> list[AuthorNode]
    def chunks_from_citing_papers(self, paper_id: str,
                                  query: str) -> list[ChunkResult]
    
    # ---- Metrics (pre-computed at build time) ----
    def pagerank(self, node_id: str) -> float
    def h_index(self, author: str) -> int
    def communities(self) -> dict[str, int]
    def corpus_stats(self) -> dict
    
    # ---- Multimodal ----
    def figures_of_paper(self, paper_id: str) -> list[FigureNode]
    def search_figures(self, query: str, top_k: int = 5) -> list[FigureNode]
        # caption embedding search across all figures
    def equations_of_paper(self, paper_id: str) -> list[EquationNode]
    def search_equations(self, query: str, top_k: int = 5) -> list[EquationNode]
        # context embedding search across all equations
    def sections(self, paper_id: str,
                 type: str | None = None) -> list[SectionNode]
    def chunks_near_figure(self, figure_id: str) -> list[ChunkResult]
    
    # ---- Librarian helpers ----
    def is_foundation_paper(self, paper_id: str,
                            threshold: int = 3) -> bool
        # cited_by count >= threshold
    def expand_equation_context(self, chunk_id: str,
                                extra_tokens: int = 1000) -> str
        # fetch preceding text for variable definitions
    
    # ---- Raw Cypher (for complex queries) ----
    def query(self, cypher: str, params: dict = None) -> list[dict]
        # Same Cypher works on NetworkX (phase 1) or FalkorDB (phase 2)
```

## Node Types

### Documents (Phase 1: papers; Phase 2: emails, slides, notes)

```
PaperNode
  id: str                       # doc_id or DOI
  title: str
  year: int
  doi: str
  venue: str
  authors: list[str]
  kind: "corpus" | "cited"
  citation_count: int
  ord_refs: dict[int, str]      # [N] -> target paper_id
  markdown_path: str            # -> full text on disk
  bibtex_key: str

AuthorNode
  id: str                       # normalized key
  display_name: str
  orcid: str | None
  paper_count: int
  citation_count: int
  h_index: int
  pagerank: float
```

### Content units (chunks, sections, LRUs)

```
ChunkNode
  id: str                       # chunk_id (key into VectorStore)
  paper_id: str                 # parent document
  ord: int                      # position in document
  section: str                  # section heading
  section_type: str             # "abstract" | "methods" | "results" | ...
  char_span: tuple[int, int]    # offset in full text
  token_count: int
  # Text + embedding live in VectorStore, not in graph.
  # Graph stores the ID; agent resolves via search_chunks().

SectionNode
  id: str                       # "{paper_id}::{section_heading}"
  paper_id: str
  heading: str
  level: int                    # h1, h2, h3
  chunk_ids: list[str]          # ordered chunks in this section
  # Enables: "give me the conclusions of papers citing X"
  #   -> cited_by(X) -> sections(type="conclusions") -> chunks
```

### Multimodal entities

```
FigureNode
  id: str                       # image ID
  paper_id: str
  label: str                    # "Figure 3", "Scheme 1"
  caption: str                  # extracted or VLM-generated
  path: str                     # filesystem path to image file
  page: int
  near_chunk_ids: list[str]     # chunks that discuss this figure
  # Enables: "find figures showing I-V curves across all papers"
  #   -> search by caption embedding -> figure nodes -> parent papers

EquationNode
  id: str                       # equation ID
  paper_id: str
  latex: str                    # LaTeX source
  label: str                    # "Eq. 1", "(3)"
  kind: str                     # "mathematical" | "chemical"
  context: str                  # surrounding text explaining variables
  chunk_id: str                 # chunk where equation appears
  # Enables: "find all equations for memristor I-V relationship"
  #   -> search by context embedding -> equation nodes
  #   -> expand_equation_context() for variable definitions
```

### Phase 2 additions

```
EmailNode
  id: str
  thread_id: str                # group by conversation
  from_author: str              # -> AuthorNode
  to_authors: list[str]
  subject: str
  date: datetime
  project: str                  # -> ProjectNode

SlideNode
  id: str
  deck_id: str                  # group of 3-5 slides
  title: str
  speaker_notes: str

NoteNode
  id: str
  tags: list[str]
  date: datetime

ProjectNode
  id: str
  name: str
  description: str
```

Adding a new type = add the dataclass + build logic. No schema migration.

## Edge Types

```
# Document structure
CONTAINS_CHUNK      Paper -> Chunk          (document contains chunk)
CONTAINS_SECTION    Paper -> Section        (document has section)
CHUNK_IN_SECTION    Chunk -> Section        (chunk belongs to section)
CONTAINS_FIGURE     Paper -> Figure         (document has figure)
CONTAINS_EQUATION   Paper -> Equation       (document has equation)
FIGURE_NEAR_CHUNK   Figure -> Chunk         (figure discussed in chunk)
EQUATION_IN_CHUNK   Equation -> Chunk       (equation appears in chunk)

# Citation & authorship
CITES               Paper -> Paper          (directed)
AUTHORED_BY         Paper -> Author         (with position: first|middle|last)
COLLABORATED        Author <-> Author       (co-authored >= 1 paper)
COUPLES             Paper <-> Paper         (bibliographic coupling)

# Semantic
SIMILAR_PAPER       Paper <-> Paper         (embedding cosine)
MENTIONS            Any -> Any              (entity mention across types)

# Phase 2
PART_OF_THREAD      Email -> Email          (reply chain)
PART_OF_PROJECT     Any -> Project          (project membership)
DISCUSSES           Email|Note -> Paper     (informal reference)
```

## Traversal Patterns (with multimodal content)

```
# "Give me the conclusions of papers citing paper X"
kg.cited_by("X")
  -> for each paper: kg.sections(paper_id, type="conclusions")
  -> for each section: resolve chunk_ids via VectorStore -> text

# "Find all I-V curve figures across the corpus"
kg.search_figures("I-V curve characteristic")  # caption embedding search
  -> FigureNode list with paths, captions, parent papers
  -> kg.chunks_near_figure(fig_id) -> context text

# "Find equations related to memristor switching"
kg.search_equations("memristor switching model")
  -> EquationNode list with LaTeX, labels, context
  -> kg.expand_equation_context(eq.chunk_id) -> variable definitions

# "Math-Guard: auto-expand equation context"
chunk = kg.get_chunk("c_123")
if chunk has equations:
    preceding = kg.expand_equation_context("c_123", extra_tokens=1000)
    # returns: chunk text + preceding 1000 tokens for variable defs

# "Give me the figure from paper Y that shows the switching behavior"
figures = kg.figures_of_paper("Y")
  -> filter by caption similarity to "switching behavior"
  -> FigureNode.path -> image file
  -> FigureNode.near_chunk_ids -> context chunks
```

## Backend Implementations

### Phase 1: NetworkX + grand-cypher + Inverted Indexes

```python
from grand_cypher import GrandCypher

class NetworkXBackend:
    """Graph storage on NetworkX with Cypher query support."""
    
    G: nx.MultiDiGraph              # canonical graph
    _cypher: GrandCypher            # Cypher query engine over G
    
    # Cached indexes (rebuilt from G at load time)
    _cited_by: dict[str, set[str]]
    _papers_of: dict[str, set[str]]
    _coauthors: dict[str, set[str]]
    _pagerank: dict[str, float]
    
    def query(self, cypher: str, params: dict = None) -> list[dict]:
        """Run a Cypher query against the graph.
        
        Agents write Cypher; this method executes it. Same queries
        work on FalkorDB later without changes.
        """
        return self._cypher.run(cypher, params)
    
    # Example: agents can write Cypher directly
    # kg.query('''
    #     MATCH (p:Paper)-[:CITES]->(target:Paper {id: $id})
    #     RETURN p.title, p.year
    #     ORDER BY p.year DESC
    # ''', {"id": "paper_X"})
    
    def _rebuild_indexes(self):
        """Rebuild inverted indexes from graph for hot-path queries."""
        # ... same as before, for O(1) lookups on frequent patterns
    
    def persist(self, path: Path):
        data = nx.node_link_data(self.G)
        path.write_text(json.dumps(data))
    
    def load(self, path: Path):
        data = json.loads(path.read_text())
        self.G = nx.node_link_graph(data)
        self._cypher = GrandCypher(self.G)
        self._rebuild_indexes()
```

Dependencies: `pip install grand-cypher` (pure Python, Apache-2.0).
Persistence: JSON via `nx.node_link_data()`. ~2-5MB for 1000 papers.
Load time: <100ms. Cypher query time: <10ms for typical patterns.

Key advantage: **agents write Cypher now, same queries work on
FalkorDB later.** The migration to a "real" graph DB is a backend
swap, not a query rewrite.

### Phase 2: FalkorDB or Kuzu (if needed)

Same `KnowledgeGraph` API, different backend:

```python
class FalkorDBBackend:
    def cited_by(self, paper_id):
        result = self.conn.execute_query(
            "MATCH (p)-[:CITES]->(target:Paper {id: $id}) RETURN p",
            {"id": paper_id},
        )
        return [PaperNode(**r) for r in result]
```

Migration trigger: when NetworkX JSON serialization becomes too slow
(>5s load) or when Cypher query expressiveness is needed for complex
multi-hop traversals that are awkward in Python.

## VectorStore Integration

ChromaDB (existing) holds chunk embeddings + metadata. The
KnowledgeGraph API delegates semantic search to it:

```python
def search_chunks(self, query, top_k=10, paper_ids=None, section=None):
    where = {}
    if paper_ids:
        where["paper_id"] = {"$in": paper_ids}
    if section:
        where["section"] = section
    return self._vector_store.query(
        query_texts=[query], n_results=top_k, where=where,
    )
```

### Long Retrieval Units (LRUs)

Phase 1 keeps existing ~300-token chunks for backward compat.
LRUs (~6000 tokens, full sections) are stored as a SECOND collection
in ChromaDB alongside chunks. The Librarian decides which to fetch:

```python
# Foundation paper: fetch full section LRU
if kg.is_foundation_paper(paper_id):
    lrus = kg.search_lrus(query, paper_ids=[paper_id])
# Specific reference: fetch targeted chunks
else:
    chunks = kg.search_chunks(query, paper_ids=[paper_id], top_k=3)
```

## What is NOT in the graph

| Data | Where | Why |
|------|-------|-----|
| Chunk text + embeddings | ChromaDB | Too large for JSON graph |
| Full markdown text | Filesystem (markdown_path) | Large, rarely traversed |
| Images (binary) | Filesystem | Binary blobs |
| DOI cache | data/doi_cache.db | Ingestion-time only |
| BibTeX files | Generated from graph | Derived artifact |

The graph stores **IDs and metadata**. Agents resolve IDs to content
via the VectorStore (chunks) or filesystem (full text, images).

## Build Pipeline

```
Wave A: similarity + topics + images           (independent)
Wave B: heuristic enrichment + DOI resolution   (citation metadata)
Wave C: citation edges + bibliography           (uses enriched data)
Wave D: build_knowledge_graph()                 (NEW)
         1. Paper nodes from docs + cited works
         2. Author nodes from paper metadata
         3. Citation edges from doc.cites
         4. Authorship + collaboration edges
         5. Paper similarity edges from embeddings
         6. Metrics: PageRank, h-index, communities
         7. Build inverted indexes
         8. Persist: graph.json
Wave E: derived artifacts (explorer, resave)
```

## Librarian Agent Integration

The Librarian uses the KnowledgeGraph API as its tool set:

```python
LIBRARIAN_TOOLS = {
    "get_paper": kg.paper,
    "get_references": kg.references,
    "get_cited_by": kg.cited_by,
    "get_neighborhood": kg.neighborhood,
    "search_chunks": kg.search_chunks,
    "search_in_neighborhood": kg.search_in_neighborhood,
    "is_foundation": kg.is_foundation_paper,
    "expand_equation_context": kg.expand_equation_context,
    "get_author": kg.author,
    "get_coauthors": kg.coauthors,
    "similar_authors": kg.similar_authors,
}
```

The agent reasons about which tool to call. The tools are
backend-agnostic — they work the same on NetworkX or FalkorDB.

## Migration Path

```
Phase 1 (now):
  NetworkX + ChromaDB
  JSON persistence
  Python dict indexes for hot queries
  ~200 lines of adapter code

Phase 2 (when generalizing to emails/slides):
  Evaluate: is NetworkX still fast enough?
  If yes: keep it, add new node/edge types
  If no: swap backend to FalkorDB or Kuzu
  Agent code unchanged (same KnowledgeGraph API)
  
Trigger for Phase 2 migration:
  - Graph JSON > 50MB (serialization too slow)
  - Need for Cypher expressiveness (complex multi-hop)
  - Need for concurrent access (multi-agent writes)
```
