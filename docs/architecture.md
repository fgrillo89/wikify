# ScholarForge -- Architecture

## What is ScholarForge?

A local-first Python pipeline that turns academic PDFs into a knowledge graph,
then writes reviews, papers, and presentations from that knowledge. Model-agnostic
(Claude, GPT-4, DeepSeek, Ollama via litellm).

## Module Layout

```
src/scholarforge/
├── agent/                    # Agent loop + tools + workflows
│   ├── core.py               # ScholarForgeAgent, tool compaction, structured tool errors
│   ├── tools.py              # 25+ KB tools (read, search, gaps, citations, vibes)
│   ├── defaults.py           # Tool sets + prompt builders (explorer, writer)
│   ├── workflows.py          # generate_paper, explore_corpus, export_paper
│   ├── scripted.py           # Scripted pipeline (Python explore + LLM write)
│   ├── fast_generate.py      # One-shot pipeline (pre-compute + single LLM call)
│   ├── research_notes.py     # ResearchNotes + SourceSummary (explorer->writer handoff)
│   ├── run_context.py        # Run-scoped state (summaries, reading log, concept graph)
│   ├── concept_graph.py      # ConceptGraph (concept->paper edges, per-run)
│   ├── reading_log.py        # File-backed reading trace for the active run
│   └── tool_schema.py        # fn -> litellm tool schema introspection
│
├── evaluate/                 # Quality metrics + exploration strategies
│   ├── quality.py            # 10-component composite + comprehensive_quality_report()
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

## Five Generation Modes

```
┌──────────────────────────────────────────────────────────────────┐
│              scholarforge generate                                │
├──────────┬──────────┬──────────┬───────────┬────────────────────┤
│  Skill   │ Skill    │ Scripted │ Two-Agent │  Fast (exp.)       │
│  Route   │ Hierarchi│  Route   │  Route    │  One-Shot          │
│  (v1)    │ cal (v2) │          │           │                    │
│          │          │          │           │                    │
│ LLM runs │ LLM uses │ Python   │ Explorer  │ Pre-compute all    │
│ the whole│ 4-level  │ explores,│ LLM ->    │ context (10s),     │
│ loop via │ progres- │ LLM just │ Notes ->  │ single LLM call    │
│ tool_use │ sive     │ writes   │ Writer    │ (~5 min)           │
│ deep+dig │ disclosure│          │ LLM      │                    │
│          │          │          │           │                    │
│ 25 min   │ ~10 min  │ 4 min    │ 8 min     │ 6 min              │
│ 133K tok │ ~80K tok │ 6K tok   │ 70K tok   │ 58K tok            │
└──────────┴──────────┴──────────┴───────────┴────────────────────┘
              All share: tools, export, quality metrics
```

### Default Reading Policy -- Progressive Disclosure

Uses 4 reading levels instead of binary deep/digest:
1. `get_paper` (~200 chars) -- what is this paper about?
2. `read_paper_digest` (~1.5KB) -- TOC + section summaries
3. `read_section` (~5KB) -- full text of one section
4. `deep_read` (~70KB) -- full paper (rarely needed)

Current default guidance is hierarchical: plan with
`get_frontier_exploration_order`, survey with `read_paper_digest`, drill with
`read_section`, and reserve `deep_read` for cases where digest + section reads
are still insufficient.

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
| Section summaries | Extractive (first 1-2 sentences per section) | <0.1s |

Cache location: `data/cache/precomputed/`. Invalidated on every `run_batch_steps`.

## Token Efficiency

- **Tool compaction**: large tool results truncated after LLM processes them. Context-aware (papers with summaries compacted more aggressively).
- **Run context injection**: paper summaries from the active run are auto-injected as system context after compaction.
- **Session-level compaction**: old turns dropped when total chars exceed adaptive threshold.
- **Read-once-summarize**: `record_paper_summary` distills findings, `get_session_context` recalls them from the active run.

## Run-Scoped State

- **RunContext** is the canonical mutable state for one exploration or generation run.
- It owns the reading log, paper summaries, concept graph, phase-level usage, and run warnings.
- `ScholarForgeAgent`, `workflows.py`, and `scripted.py` bind tool calls to the active run context.
- This replaces the older process-global session model and is the foundation for future multi-session app surfaces.

## Ingest Boundary

- `ingest/service.py` is the public application boundary for file and directory ingestion.
- `ingest/corpus_refresh.py` owns post-ingest refresh work such as topic linking, embeddings, vault regeneration, BibTeX rebuild, and precompute refresh.
- CLI commands and agent tools should call these public modules rather than reaching into private helpers in `ingest/registry.py`.
- `ingest/registry.py` is now a legacy compatibility shim that delegates its public entry points to the service modules.

## Writer Handoff

- `ResearchNotes` is now the canonical explorer-to-writer boundary for note-driven generation.
- `writer_input.py` builds the final writer request used by the two-agent, scripted, and fast one-shot routes.
- Fast generation now converts deterministic precomputed paper context into `ResearchNotes` with evidence excerpts instead of maintaining a separate writer prompt format.
- This keeps citation lists, artifact guidance, and writer instructions aligned across routes.

## Tool Result Contracts

- Markdown-oriented tools still return plain text for direct LLM consumption.
- JSON-oriented tools such as `list_papers`, `list_topics`, `get_graph_metrics`, `deep_read`, and `ingest_paper` now return envelopes with `ok: true/false`.
- Agent-side tool execution failures are also normalized to JSON with `ok`, `tool`, and `error`, so failures are distinguishable from weak evidence.
- Export fallback is logged explicitly when DOCX-to-PDF conversion fails and the workflow drops to HTML-to-PDF, so degraded output quality is operationally visible.

## Quality Metrics

### Automated Composite (10 components)

| Metric | Weight | What it measures |
|--------|--------|------------------|
| Prose quality | 0.20 | Citation clustering, synthesis depth, discourse quality |
| Frontier shift | 0.14 | Push toward sparse regions |
| Gap detection | 0.14 | Embedding voids + gap-claim phrases |
| Arg. coherence | 0.12 | Consecutive chunk pairs preserved in review |
| Factual specificity | 0.12 | Numbers + formulas per 1k words |
| Semantic coverage | 0.10 | Corpus chunks covered by review |
| Bridge vectors | 0.10 | Chunks connecting dissimilar papers |
| Semantic residual | 0.10 | Synthesis vs summarization (SVD) |
| Topic coverage | 0.10 | PaperTopic vocabulary in review |
| Centroid alignment | 0.08 | Review center vs corpus center |

The automated report now includes prose quality directly, but PI-style review
still matters because it captures research judgment and contribution framing
that the composite only approximates.

## Data Model

- **Paper.origin**: `"corpus"` (ingested) vs `"generated"` (output). All metrics filter on corpus only.
- **Project** + **ProjectPaper**: many-to-many scoping for multi-project support.
- **GeneratedOutput**: tracks each writing run (strategy, cost, coverage).
- **ChromaDB**: three collections (summaries + chunks + section_summaries). Only corpus content embedded.
- **Concept graph**: per-run, saved as JSON alongside output. Never in corpus DB.

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
