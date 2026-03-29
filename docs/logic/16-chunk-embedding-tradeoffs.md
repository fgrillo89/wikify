# Chunk Embedding Tradeoffs

## Current architecture

```
Query → ChromaDB (abstract embeddings, ~1 per paper)
      → "which papers are relevant?"
      → SQLite chunks (ordered by section, filtered by budget)
      → "read those papers' text"
```

This is a two-tier approach: embeddings narrow to papers, SQL retrieves chunks.

## The case for chunk embeddings

Chunk-level embeddings would enable:
- "Find the paragraph about HfO2 growth rates" across ALL papers in one query
- Skip irrelevant sections entirely (don't waste tokens on Introduction if you want Methods)
- More precise RAG: retrieve exactly the 10 most relevant paragraphs, not "first 3 chunks of each paper"

## The case against

| Factor | Abstract-only (current) | Full chunk embeddings |
|---|---|---|
| Vectors | ~20 (for 20 papers) | ~800 (40x more) |
| Embedding time | ~2s total | ~80s total |
| ChromaDB size | ~1 MB | ~40 MB |
| At 200 papers | ~200 vectors | ~8,000 vectors |
| At 1000 papers | ~1,000 vectors | ~40,000 vectors |
| Ingestion speed | Fast (<0.2s/paper) | Slow (~4s/paper) |
| Re-embed on edit | Rare (abstracts don't change) | Frequent (chunks regenerate on re-parse) |

For a personal research tool, 40,000 vectors is fine technically. But:
- Ingestion goes from 2s/paper to 6s/paper
- Incremental re-ingestion now needs chunk-level diff detection
- ChromaDB collections get large and harder to debug
- Diminishing returns: abstract similarity already identifies the right papers ~90% of the time

## The user's insight: LLM-driven traversal

> "As long as we index papers and documents very well, the LLM can traverse the right
> chunks by itself and we don't need expensive embeddings."

This is correct. With the MCP server, the LLM already has:
1. `search_papers(query)` → find relevant papers by abstract
2. `get_paper(pattern)` → see ALL chunks with section paths
3. `deep_read(pattern)` → full text of a specific paper
4. `get_graph_metrics()` → know which papers are hubs

The LLM can do multi-hop reasoning:
```
search_papers("HfO2 growth rates")
→ finds Kim 2021 and Matveyev 2015
get_paper("Kim 2021")
→ sees section paths: "Methods.ALD Process", "Results.Growth Rate"
→ reads the relevant chunks directly
```

This is what LLMs are good at — following structure, reading selectively, asking follow-up
questions. We're already giving it good structure (section paths, chunk ordering, topic
tags, graph metrics).

## Middle ground: section-level embeddings (if ever needed)

If chunk-level search becomes genuinely needed, a lighter alternative:

**Embed section openings, not every chunk.**
- For each section (heading), concatenate the first ~200 tokens
- Embed those (~3-5 per paper instead of ~40)
- Vectors: ~100 for 20 papers, ~500 for 100 papers (5x, not 40x)
- Enables "find sections about X" without full chunk cost

This would mean adding a `SectionEmbedding` collection in ChromaDB, separate from
abstract embeddings. But this should only be built when there's a real user need —
right now, the LLM-driven traversal works.

## Decision

**Keep abstract-only embeddings.** The current two-tier approach (embeddings narrow,
SQL retrieves, LLM traverses) is the right architecture for a personal research tool.

Invest instead in:
1. Better structural indexing (section paths, topic tags, graph metrics) — already done
2. MCP tools that let the LLM navigate intelligently — already done
3. Synthetic abstracts for non-article documents — simple extension

If chunk-level search becomes a bottleneck later, section-level embeddings are the
middle ground. Full chunk embeddings are overkill for this use case.
