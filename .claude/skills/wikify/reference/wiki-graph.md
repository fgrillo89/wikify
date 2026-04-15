# Wiki Knowledge Graph fluent API

The `WikiKnowledgeGraph` is the agent's interface for navigating wiki pages,
their relationships, and scoped vector search over page content. Independent
from the corpus `KnowledgeGraph`; communicates via shared string IDs
(chunk_id, doc_id).

Source: `src/wikify/store/wiki_graph.py`

## Node types

| Type | Key format | Examples |
|------|-----------|----------|
| `page` | page_id (= title) | `"Atomic Layer Deposition"`, `"Stuart Parkin"` |
| `evidence` | `"{page_id}::eN"` | `"Atomic Layer Deposition::ee1"` |

## Entry points

```python
wkg = preloaded.wiki_knowledge_graph  # or loaded from bundle

# Single page
wkg.page("Atomic Layer Deposition")

# All pages
wkg.pages()
wkg.pages(kind="article")
wkg.pages(kind="person")

# Direct vector search over page bodies
wkg.search("memristor switching", top_k=5)

# Stats
wkg.stats()  # -> {pages, evidence, edges}
```

## Traversal methods (return new WikiQueryBuilder)

### Page relationships

```python
qb.links()          # pages this page links to (from crosslink)
qb.linked_by()      # pages that link to this page
qb.co_evidence()    # pages sharing evidence source documents (excludes self)
qb.evidence()       # evidence entries for these pages
```

### Filters

```python
qb.where(kind="article")       # filter by any node attribute
qb.top(5, by="n_evidence")     # top N by metric
```

## Terminal methods

```python
qb.collect()     # -> list[dict]     materialize all nodes
qb.ids()         # -> list[str]      just node IDs
qb.count()       # -> int
qb.first()       # -> dict | None
qb.exists()      # -> bool
```

## Scoped vector search

```python
qb.search(query, top_k=10)   # -> list[dict] with score field
```

Embeds the query, scopes cosine similarity to pages in the current set.

## Page node attributes

```python
{
    "id": "Atomic Layer Deposition",
    "type": "page",
    "title": "Atomic Layer Deposition",
    "kind": "article",           # or "person"
    "n_evidence": 12,
    "n_links": 3,
    "has_body": True,
    "aliases": ["ALD"],
    "evidence_doc_ids": ["p1", "p2", "p5"],  # corpus doc_ids in evidence
}
```

## Evidence node attributes

```python
{
    "id": "Atomic Layer Deposition::ee1",
    "type": "evidence",
    "page_id": "Atomic Layer Deposition",
    "chunk_id": "p1__c0003__a1b2",   # -> corpus KG chunk node
    "doc_id": "p1",                   # -> corpus KG source node
    "quote": "ALD is a thin-film growth technique...",
}
```

## Cross-graph search patterns

The wiki graph and corpus graph are independent. The model bridges them
using text as query input. The embedder is shared, so vector spaces are
compatible.

### Wiki -> Corpus: find corpus evidence for a wiki page

```python
# Use page title/content as query into corpus KG
page = wkg.page("Atomic Layer Deposition").first()
kg.search(page["title"], top_k=10)  # corpus chunks similar to page topic

# Or use specific evidence doc_ids to trace back
for doc_id in page["evidence_doc_ids"]:
    kg.source(doc_id).chunks().search("specific aspect", top_k=3)
```

### Corpus -> Wiki: check if a concept is already covered

```python
# Before extracting a new concept, check if a wiki page exists
source = kg.source("paper_A").first()
hits = wkg.search(source["title"], top_k=3)
if hits and hits[0]["score"] > 0.7:
    # Already covered -- extend existing page instead of creating new one
    existing_page = hits[0]
```

### Wiki -> Wiki: find duplicate or overlapping pages

```python
page = wkg.page("ALD").first()
similar = wkg.search(page["title"], top_k=5)
for s in similar:
    if s["id"] != page["id"] and s["score"] > 0.8:
        # Candidate for merge
        pass
```

## Use cases

### UC1: Does the wiki already cover a concept?

```python
hits = wkg.search("resistive switching mechanism", top_k=3)
if hits and hits[0]["score"] > 0.6:
    # Covered -- check depth
    page = wkg.page(hits[0]["id"])
    if page.first()["n_evidence"] < 3:
        # Thin coverage -- needs more evidence
        pass
```

### UC2: Which pages need more substance?

```python
thin = wkg.pages().where(has_body=True).top(10, by="n_evidence")
# Pages with fewest evidence entries need attention
for p in thin.collect():
    if p["n_evidence"] < 3:
        # Find corpus evidence to strengthen
        kg.search(p["title"], top_k=10)
```

### UC3: Find isolated pages (no links, no co-evidence)

```python
for p in wkg.pages().collect():
    links_out = wkg.page(p["id"]).links().count()
    links_in = wkg.page(p["id"]).linked_by().count()
    co_ev = wkg.page(p["id"]).co_evidence().count()
    if links_out + links_in + co_ev == 0:
        # Orphan page -- needs crosslinks or more evidence
        pass
```

### UC4: Editor decides section depth from wiki graph

```python
page = wkg.page(page_id).first()
# How connected is this page?
n_co = wkg.page(page_id).co_evidence().count()
n_links = page["n_links"]
# Well-connected page -> longer article, more sections
# Isolated page -> shorter, focused article
```

### UC5: Orchestrator checks wiki coverage before sampling

```python
# Before jump_gap or pick_chunks, check what the wiki already has
concept = "hafnium oxide switching"
wiki_hits = wkg.search(concept, top_k=3)
if wiki_hits and wiki_hits[0]["score"] > 0.7:
    # Wiki covers this -- skip or walk_local for depth
    pass
else:
    # Wiki gap -- prioritize extraction for this topic
    corpus_chunks = kg.search(concept, top_k=10)
    # -> pick_chunks from these
```
