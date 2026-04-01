# ScholarForge -- Architecture

## What is ScholarForge?

A local-first Python pipeline that turns academic PDFs into a knowledge graph,
then writes reviews, papers, and presentations from that knowledge. Model-agnostic
(Claude, GPT-4, DeepSeek, Ollama via litellm).

## Module Layout

```
src/scholarforge/
├── agent/                    # Agent loop + tools + workflows
│   ├── core.py               # ScholarForgeAgent, tool compaction, session context
│   ├── tools.py              # 25+ KB tools (read, search, gaps, citations, vibes)
│   ├── defaults.py           # Tool sets + prompt builders (explorer, writer)
│   ├── workflows.py          # generate_paper, explore_corpus, export_paper
│   ├── scripted.py           # Scripted pipeline (Python explore + LLM write)
│   ├── fast_generate.py      # One-shot pipeline (pre-compute + single LLM call)
│   ├── research_notes.py     # ResearchNotes + SourceSummary (explorer->writer handoff)
│   ├── concept_graph.py      # ConceptGraph (concept->paper edges, per-session)
│   ├── reading_log.py        # File-backed reading trace
│   └── tool_schema.py        # fn -> litellm tool schema introspection
│
├── evaluate/                 # Quality metrics + exploration strategies
│   ├── quality.py            # 9 metrics + comprehensive_quality_report()
│   ├── coverage.py           # Semantic coverage, paper vibes
│   ├── strategies.py         # greedy_submodular, max_distance, spectral, hub_bfs
│   └── frontier.py           # frontier_exploration_order (4-phase reading order)
│
├── store/                    # SQLite + ChromaDB + pre-compute cache
│   ├── models.py             # Paper, Chunk, Citation, Project, GeneratedOutput
│   ├── db.py                 # Engine + session management + migrations
│   ├── embeddings.py         # EmbeddingStore + paper/science vibe vectors
│   └── precompute.py         # Ingest-time cache (KMeans, gaps, links, vibes)
│
├── ingest/                   # PDF/DOCX/PPTX ingestion (no LLM)
├── extract/                  # Chunking, metadata, citations, figures
├── graph/                    # NetworkX: PageRank (citation-only), centrality
├── generate/                 # Planner, writer, verifier, references
├── export/                   # DOCX, PDF, chemistry subscripts
├── vault/                    # Obsidian vault generation
├── zotero/                   # BibTeX library
└── llm/                      # litellm client + hooks
```

## Four Generation Modes

```
┌─────────────────────────────────────────────────────┐
│              scholarforge generate                    │
├──────────┬──────────┬──────────┬───────────────────┤
│  Skill   │ Scripted │ Two-Agent│  Fast (exp.)      │
│  Route   │  Route   │  Route   │  One-Shot         │
│          │          │          │                   │
│ LLM runs │ Python   │ Explorer │ Pre-compute all   │
│ the whole│ explores,│ LLM ->   │ context (10s),    │
│ loop via │ LLM just │ Notes -> │ single LLM call   │
│ tool_use │ writes   │ Writer   │ (~5 min)          │
│          │          │ LLM      │                   │
│ 25 min   │ 4 min    │ 8 min    │ 6 min             │
│ 133K tok │ 6K tok   │ 70K tok  │ 58K tok           │
└──────────┴──────────┴──────────┴───────────────────┘
              All share: tools, export, quality metrics
```

## Pre-Compute Cache (built at ingest, loaded at generation)

| Artifact | What | Load time |
|----------|------|-----------|
| Paper vibe vectors | Token-weighted chunk centroids (all sections) | <0.1s |
| Science vibes | Centroids from results/discussion/conclusion only | <0.1s |
| KMeans centroids | 12 clusters on 6,531 chunk embeddings | <0.1s |
| Graph metrics | Citation-only PageRank, betweenness, degree | <0.1s |
| Topic embeddings | 56 normalized topic name vectors | <0.1s |
| Boilerplate IDs | 304 chunks appearing in 5+ papers (k-NN detected) | <0.1s |
| Divergent gaps | 17 coupled-but-divergent paper pairs | <0.1s |
| Concept links | 30 section-filtered, IDF-labeled paper connections | <0.1s |

Cache location: `data/cache/precomputed/`. Invalidated on every `run_batch_steps`.

## Token Efficiency

- **Tool compaction**: large tool results truncated after LLM processes them. Context-aware (papers with summaries compacted more aggressively).
- **Session context**: paper summaries auto-injected as system message after compaction.
- **Session-level compaction**: old turns dropped when total chars exceed adaptive threshold.
- **Read-once-summarize**: `record_paper_summary` distills findings, `get_session_context` recalls them.

## Quality Metrics (9 dimensions)

| Metric | Weight | What it measures |
|--------|--------|------------------|
| Frontier shift | 0.14 | Push toward sparse regions |
| Gap detection | 0.14 | Embedding voids + gap-claim phrases |
| Arg. coherence | 0.12 | Consecutive chunk pairs preserved in review |
| Factual specificity | 0.12 | Numbers + formulas per 1k words |
| Semantic coverage | 0.10 | Corpus chunks covered by review |
| Bridge vectors | 0.10 | Chunks connecting dissimilar papers |
| Semantic residual | 0.10 | Synthesis vs summarization (SVD) |
| Topic coverage | 0.10 | PaperTopic vocabulary in review |
| Centroid alignment | 0.08 | Review center vs corpus center |

## Data Model

- **Paper.origin**: `"corpus"` (ingested) vs `"generated"` (output). All metrics filter on corpus only.
- **Project** + **ProjectPaper**: many-to-many scoping for multi-project support.
- **GeneratedOutput**: tracks each writing run (strategy, cost, coverage).
- **ChromaDB**: two collections (summaries + chunks). Only corpus content embedded.
- **Concept graph**: per-session, saved as JSON alongside output. Never in corpus DB.

## Data Layout

```
data/
├── papers.db                 # SQLite (Paper, Chunk, Citation, etc.)
├── chromadb/                 # Embedding vectors (summaries + chunks)
├── cache/precomputed/        # Ingest-time cache (vibes, KMeans, gaps, links)
├── library.bib               # Auto-generated BibTeX
├── output/                   # Generated papers + reading logs + concept graphs
└── vault/                    # Obsidian vault
```
