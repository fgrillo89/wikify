# Retrieval & Context Assembly

## Three retrieval modes

**`retrieve_for_query(query, max_papers, max_tokens)`** — For chat/Q&A:
1. Encode query via sentence-transformers
2. Query ChromaDB for top-k similar papers (by abstract embedding)
3. Load chunks from SQLite for those papers
4. Pack chunks up to token budget (greedy, skip if over budget)

**`retrieve_all_papers(deep_read_top_n=3)`** — For review paper generation:
1. Compute graph metrics (PageRank)
2. Identify top N hub papers
3. For hub papers: load ALL chunks (deep read)
4. For rest: load first 3 chunks per paper (abstract + intro + methods)
5. Return all papers + chunks + graph metrics

**`retrieve_deep(paper_ids)`** — For explicit full-paper reading:
1. Load ALL chunks for the specified papers
2. No filtering or budget — caller decides

## Context formatting
`as_text()` groups chunks by paper, formats as:
```
### Title (Author1, Author2, Author3, Year)
chunk text...
---
### Next paper...
```

`paper_summaries()` gives one-line per paper for LLM planning prompts.

## Token budgets
- Chat: 12,000 tokens default
- Generation: no hard budget (controlled by chunk selection strategy)
- Literature context in writer prompts: truncated at 8,000 chars

## Where the code lives
- `retrieve/context.py`
