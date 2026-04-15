# Knowledge Graph fluent API

The `KnowledgeGraph` is the agent's primary reasoning interface for corpus
traversal, citation analysis, and scoped vector search. All agent code uses
this API. NetworkX is internal and never exposed.

Source: `src/wikify/citestore/graph.py`
Builder: `src/wikify/citestore/graph_build.py`

## Node types

| Type | Key format | Examples |
|------|-----------|----------|
| `source` | doc_id | `"paper_A"`, `"[2020 Smith] ALD Review_abc123"` |
| `author` | normalized name | `"smith j"`, `"jones k"` |
| `chunk` | chunk_id | `"paper_A__c0003__a1b2c3"` |
| `section` | `"{source_id}::{heading_path}"` | `"paper_A::Methods"` |
| `figure` | `"{source_id}/fig_NN"` | `"paper_A/fig_01"` |
| `equation` | equation hash | `"paper_A_eq1"` |

Sources have `kind`: `"corpus"` (full text available) or `"cited"` (metadata only).

## Entry points

```python
kg = preloaded.knowledge_graph

# Single node
kg.source("paper_A")          # -> QueryBuilder scoped to one source
kg.author("smith j")          # -> QueryBuilder scoped to one author

# All nodes of a type
kg.sources()                  # -> all sources
kg.sources(kind="corpus")     # -> corpus-only sources
kg.authors()                  # -> all authors
kg.chunks()                   # -> all chunks

# Direct vector search (no graph traversal)
kg.search("memristor switching", top_k=10)

# Corpus-level metrics
kg.corpus_stats()  # -> {sources, authors, chunks, sections, figures, equations, edges}
```

## Traversal methods (return new QueryBuilder)

Each traversal returns a new QueryBuilder scoped to the result set. Nothing
executes until a terminal is called. All operations are O(k) via dict lookups.

### Citation graph

```python
qb.cited_by()                 # sources that cite these sources
qb.references()               # sources cited by these sources
qb.references(ords=[1,2,3])   # specific ordinal references from [N] markers
qb.neighborhood(hops=2)       # N-hop undirected graph neighbors
```

### Authorship

```python
qb.authors()                  # authors of these sources
qb.sources()                  # sources by these authors
qb.coauthors()                # co-authors of these authors (excludes self)
```

### Document structure

```python
qb.sections()                 # all sections of these sources
qb.sections(type="conclusions")  # filtered by section_type
qb.chunks()                   # chunks of these sources or sections
qb.figures()                  # figures of these sources
qb.equations()                # equations of these sources
qb.nearby_figures()           # figures linked to these chunks (FIGURE_NEAR_CHUNK)
qb.nearby_equations()         # equations in these chunks (EQUATION_IN_CHUNK)
```

### Chunk-to-chunk similarity

```python
qb.similar_to(chunk_id, top_k=10)  # chunks similar by vector cosine (uses existing embedding)
```

Uses the chunk's existing vector -- no re-embedding, no text needed.
Scoped to the current set. Excludes the seed chunk itself.

### Filters

```python
qb.where(kind="corpus")       # filter by any node attribute
qb.of_type("source")          # filter to specific node type
qb.since(2020)                # year >= N
qb.top(10, by="pagerank")     # top N by metric (pagerank, year, citation_count, h_index)
qb.match("caption", "IV curve")  # case-insensitive substring match on a field
```

## Terminal methods (execute and return)

```python
qb.collect()     # -> list[dict]     materialize all nodes
qb.ids()         # -> list[str]      just node IDs
qb.count()       # -> int            count matches
qb.first()       # -> dict | None    first result or None
qb.exists()      # -> bool           any matches?
```

## Scoped vector search

```python
qb.search(query, top_k=10)   # -> list[dict] with score field
```

`search()` embeds the query text, computes cosine similarity against the
VectorStore, then filters to only chunk IDs reachable from the current set.
If the current set contains sources/sections/figures/equations, their chunks
are resolved automatically. Returns dicts with `id`, `source_id`, `score`.

## Metrics on current set

```python
qb.pagerank()         # -> {node_id: float}
qb.citation_count()   # -> {node_id: int}
```

Pre-computed at build time. PageRank over citation subgraph. Citation count
is in-degree of CITES edges. h-index stored on author nodes.

## Use cases

### UC1: Conclusions of papers citing paper X

```python
kg.source("X").cited_by().sections(type="conclusions").chunks().collect()
```

### UC2: Specific ordinal references [1-4] from paper X

```python
kg.source("X").references(ords=[1,2,3,4]).collect()
```

### UC3: All papers by an author, sorted by year

```python
kg.author("smith j").sources().top(20, by="year").collect()
```

### UC4: Semantic search scoped to citing papers

```python
kg.source("Y").cited_by().chunks().search("concept X", top_k=5)
```

### UC5: Most influential papers in corpus

```python
kg.sources().top(10, by="pagerank").collect()
```

### UC6: Author total citation count

```python
kg.author("smith j").sources().cited_by().count()
```

### UC7: Figures from papers that cite X

```python
kg.source("X").cited_by().figures().collect()
```

### UC8: Equations related to a topic (via chunk proximity)

```python
kg.search("memristor switching model", top_k=5).nearby_equations()
```

### UC9: Figures by caption keyword

```python
# Global: find all figures with "IV" in caption
kg.sources().figures().match("caption", "IV curve")

# Local: figures in one source matching keyword
kg.source(doc_id).figures().match("caption", "schematic")
```

### UC10: Figures near chunks about a topic

```python
# Find figures discussed near chunks about a topic
kg.source(doc_id).chunks().search("IV curve", top_k=3).nearby_figures()

# Global: figures near topic-relevant chunks
kg.search("resistive switching", top_k=10).nearby_figures()
```

### UC11: Equations by label

```python
kg.source(doc_id).equations().match("label", "Eq. 1")
```

### UC12: Similar chunks (local and global)

```python
# Local: chunks similar to seed within same source
kg.source(doc_id).chunks().similar_to(chunk_id, top_k=5)

# Global: chunks similar to seed across entire corpus
kg.chunks().similar_to(chunk_id, top_k=10)
```

## Librarian decision patterns

These patterns implement the Librarian agent logic from the harness design.

### Foundation vs specific reference

A source cited by >3 papers is a foundation paper. Fetch full sections.
A source cited once is a specific reference. Use targeted search.

```python
source = kg.source(source_id)
if source.cited_by().count() > 3:
    # Foundation paper: get full Long Retrieval Units
    context = source.sections().chunks().collect()
else:
    # Specific reference: targeted search only
    context = source.chunks().search(query, top_k=3)
```

### Math-Guard: equation context expansion

When a chunk contains equations, auto-expand context to capture variable
definitions from surrounding chunks in the same section.

```python
chunk = kg.source(source_id).chunks().where(id=chunk_id).first()
if chunk and chunk.get("equation_ids"):
    # Get the section this chunk belongs to
    section_chunks = kg.source(source_id).sections().chunks().collect()
    # Find chunks in same section near this one for variable definitions
    chunk_ord = chunk["ord"]
    expanded = [
        c for c in section_chunks
        if abs(c["ord"] - chunk_ord) <= 2  # preceding/following chunks
    ]
```

### Graph-neighborhood discovery

Find research communities and trace influence paths.

```python
# Research community around a topic
hits = kg.search("topic Z", top_k=30)
source_ids = {h["source_id"] for h in hits}
# -> group by source, get authors, trace collaboration subgraph

# Who does author A cite most?
cited_authors = kg.author("smith j").sources().references().authors().collect()
# -> count by frequency to find most-cited authors

# 2-hop citation neighborhood
kg.source("X").neighborhood(hops=2).of_type("source").collect()
```

### Cited corpus chunks for write prep

Replace the old RefLookup pattern. For each evidence source, find corpus
sources it cites and retrieve relevant chunks via scoped vector search.

```python
for doc_id in page_doc_ids:
    cited = kg.source(doc_id).references()
    for cited_id in cited.ids():
        hits = kg.source(cited_id).chunks().search(page.title, top_k=3)
```

### Citation marker resolution for extract

Build citation_refs for an ExtractRequest from the KG's ord_refs index.
The source node stores `ord_refs: {N: target_source_id}` mapping inline
markers [N] to resolved target sources.

```python
source_node = kg.source(doc_id).first()
ord_refs = source_node.get("ord_refs", {})
for marker_ord in parsed_marker_ords:
    target_id = ord_refs.get(marker_ord)
    if target_id:
        target = kg.source(target_id).first()
        # target has: title, year, doi, authors, kind
```

## Node attributes

### Source node

```python
{
    "id": "paper_A",
    "type": "source",
    "title": "Paper Title",
    "year": 2020,
    "doi": "10.1/abc",
    "venue": "Nature",
    "authors": ["Smith, J.", "Jones, K."],
    "kind": "corpus",          # or "cited"
    "markdown_path": "corpus/markdown/paper_A.md",
    "n_chunks": 42,
    "n_tokens": 15000,
    "pagerank": 0.0234,
    "citation_count": 5,
    "ord_refs": {1: "paper_B", 3: "paper_C"},  # [N] -> target source_id
}
```

### Author node

```python
{
    "id": "smith j",
    "type": "author",
    "display_name": "Smith, J.",
    "source_count": 3,
    "h_index": 2,
    "citation_count": 12,
}
```

### Chunk node

```python
{
    "id": "paper_A__c0003__a1b2",
    "type": "chunk",
    "source_id": "paper_A",
    "ord": 3,
    "section_type": "methods",
    "char_span": [1200, 1800],
    "equation_ids": ["paper_A_eq1"],
}
```

Chunk text and embeddings live in the VectorStore, not the graph.

### Section node

```python
{
    "id": "paper_A::Methods",
    "type": "section",
    "source_id": "paper_A",
    "heading": "Methods",
    "level": 1,
    "section_type": "methods",
    "chunk_ids": ["paper_A__c0003__a1b2", "paper_A__c0004__c3d4"],
}
```

### Figure node

```python
{
    "id": "paper_A/fig_01",
    "type": "figure",
    "source_id": "paper_A",
    "caption": "IV curve of the memristor device",
    "path": "corpus/images/paper_A/fig_01.png",
    "page": 3,
    "near_chunk_ids": ["paper_A__c0003__a1b2"],
}
```

### Equation node

```python
{
    "id": "paper_A_eq1",
    "type": "equation",
    "source_id": "paper_A",
    "latex": "V = IR",
    "label": "Eq. 1",
    "kind": "inline",
}
```
