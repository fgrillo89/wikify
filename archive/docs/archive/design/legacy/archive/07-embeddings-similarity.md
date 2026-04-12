# Abstract Embeddings & k-NN Similarity

## What it does
Embeds paper abstracts into ChromaDB for semantic similarity search.

## Scope
- **Only abstracts** go into ChromaDB (~200 vectors for 200 papers). NOT chunks.
- Chunks live in SQLite. ChromaDB is purely for abstract-level similarity.

## Embedding model
`all-MiniLM-L6-v2` via sentence-transformers. Fast, local, ~90MB model.

## k-NN similarity
- For each paper, query top-5 nearest neighbors (cosine distance)
- Self-match excluded
- Batch query: all papers queried in one call (not N individual queries)

## How it's used
1. **Vault notes**: Similar Papers section shows k-NN neighbors as wikilinks
2. **Graph metrics**: Similarity edges feed into PageRank/centrality computation
3. **Retrieval**: `retrieve_for_query()` uses ChromaDB to find relevant papers for generation/chat

## Where the code lives
- `store/embeddings.py` — embed, query, batch operations
- ChromaDB stored at `data/chromadb/` (persistent client)
