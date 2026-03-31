# ScholarForge -- Architecture

## What is ScholarForge?

A local-first Python pipeline that turns a folder of academic PDFs into a
knowledge graph, then uses an internal agent loop (litellm + tool_use) to
write papers, reviews, and presentations from that knowledge.

## Design Principles

1. **Agent-first**: `ScholarForgeAgent` is the core orchestration mechanism.
   LLM decides what tools to call, in what order, and how to use results.
2. **Dependency injection**: Tools, hooks, and prompts are passed explicitly.
   No globals, no singletons, no hidden state.
3. **Contracts over conventions**: Every LLM interaction has a Pydantic schema
   defining expected output. Validation failures are retried with feedback.
4. **Vault-first output**: Obsidian vault is the primary user-facing output.
5. **Local-first**: Parsing, embedding, and graph computation run locally.
   LLM calls are the only network dependency (configurable: Claude, OpenAI, Ollama).

## Core: The Agent Loop

```
User prompt
    |
    v
ScholarForgeAgent(model, tools, hooks, system_prompt)
    |
    |-- LLM decides which tool to call
    |-- Tool executes (list_papers, search_papers, deep_read, ...)
    |-- Result fed back to LLM
    |-- Repeat until LLM produces final output
    |
    v
AgentResult (content, tool_calls, token counts)
    |
    v
Export (DOCX, PDF, Markdown)
```

The agent receives:
- **Tools**: Plain Python functions (list_papers, search_papers, etc.)
- **Hooks**: Cross-cutting concerns (CostTracker, TokenBudget, CallLogger)
- **System prompt**: Layered instructions (style guide + artifact type + field guide)
- **Output contract**: Optional Pydantic model for validated structured output

## Module Layout

```
src/scholarforge/
├── agent/                          # Agent loop (core orchestration)
│   ├── core.py                     # ScholarForgeAgent, AgentResult, ToolCallRecord
│   ├── tools.py                    # KB tool functions (shared by agent + MCP)
│   ├── tool_schema.py              # fn -> litellm tool schema introspection
│   ├── defaults.py                 # get_default_tools(), get_default_hooks()
│   └── workflows.py                # High-level workflows (generate_paper, etc.)
│
├── cli.py                          # Typer CLI + template subcommands
├── config.py                       # pydantic-settings (.env support)
├── mcp_server.py                   # MCP server (thin wrapper over agent/tools.py)
│
├── ingest/                         # Document ingestion (no LLM)
│   ├── pdf.py, docx.py, pptx.py   # Parsers
│   └── registry.py                 # Dispatcher + batch orchestration
│
├── extract/                        # Structured extraction (no LLM)
│   ├── chunker.py                  # Section-aware chunking
│   ├── metadata.py                 # Title, authors, DOI, year
│   ├── figure_refs.py              # Caption-first figure/table refs
│   ├── citations.py                # Bibliography extraction
│   └── cite_match.py               # Fuzzy citation matching
│
├── store/                          # SQLite + ChromaDB
│   ├── models.py                   # Paper, Chunk, Citation, Project, GeneratedOutput, etc.
│   ├── db.py                       # Engine + session management + migrations
│   └── embeddings.py               # EmbeddingStore (summaries + chunks, DI-friendly)
│
├── vault/                          # Obsidian vault (no LLM)
│   ├── writer.py                   # Paper/author note generation
│   ├── linker.py                   # Topic extraction + hubs
│   ├── templates.py                # Note templates
│   └── coupler.py                  # Bibliographic coupling
│
├── graph/                          # NetworkX graph analysis
│   └── metrics.py                  # PageRank, centrality, hub/bridge/frontier
│
├── retrieve/                       # Context assembly
│   ├── context.py                  # RetrievedContext, SectionContext
│   └── strategies/                 # 5 retrieval strategies
│
├── generate/                       # Content generation support
│   ├── planner.py                  # Paper outline from prompt
│   ├── writer.py                   # Section-by-section writing
│   ├── verifier.py                 # Plan compliance + paper verification
│   ├── persona.py                  # System prompt builder
│   ├── references.py               # [REF:...] -> [N] resolver
│   ├── figures.py                  # Figure placeholder extraction
│   ├── field_guide.py              # Field detection + guide loading
│   └── artifact_types/             # Document type definitions (7 types)
│
├── export/                         # Output formatting
│   ├── docx_export.py              # DOCX with template cloning
│   ├── pdf_export.py               # HTML->PDF
│   ├── chemistry.py                # Chemical formula subscripts
│   ├── journal_profile.py          # JournalProfile model
│   ├── journals/                   # JSON profiles (AFM, Nature, ACS, etc.)
│   └── templates/                  # Template registry + DOCX files
│
├── zotero/                         # Reference management
│   ├── bibtex_builder.py           # Paper -> BibTeX
│   └── bibtex_library.py           # Corpus-wide library.bib
│
└── llm/                            # LLM interface
    ├── client.py                   # litellm wrapper, complete_structured
    ├── schemas.py                  # Pydantic output models
    └── hooks.py                    # LLMHook protocol, CostTracker, etc.
```

## Writing Pipeline

The agent's system prompt is layered:
```
1. Base style guide (680 words)     <- docs/logic/academic_writing_style.md
2. Artifact type rules              <- docs/logic/artifact_types/{type}.md
3. Field-specific guide             <- docs/logic/fields/{field}.md
4. Figure instructions              <- per-section, body sections only
5. Journal constraints              <- export/journals/{journal}.json
```

## Two Interfaces to the Same Tools

```
Agent Loop (primary)          MCP Server (external clients)
  ScholarForgeAgent             @mcp.tool() wrappers
       |                              |
       v                              v
  agent/tools.py  <--- shared --->  agent/tools.py
       |
       v
  litellm.completion(tools=...)
```

Both call the same Python functions. Agent loop uses litellm's native
tool_use. MCP server wraps them for external clients (Claude Code, Cursor).

## Evaluate: Quality Metrics & Analysis Tools

```
src/scholarforge/evaluate/
├── __init__.py
├── coverage.py          # compute_coverage(), compute_paper_vibes()
├── quality.py           # 9 quality metrics + comprehensive_quality_report()
└── strategies.py        # greedy_submodular, max_distance, spectral, hub_bfs
```

### Three layers of quality measurement

**Layer 1 — Content presence** (does the review contain what's in the corpus?):
- **Semantic coverage**: Chunk embedding proximity (what fraction is covered)
- **Topic coverage gap**: Which corpus topics appear in the review text
- **Cross-reference density**: How many distinct papers are semantically touched
- **Thematic centroid**: Is the review's center of gravity aligned with the corpus

**Layer 2 — Structural quality** (is the content well-organized?):
- **Argumentative coherence**: Do consecutive corpus chunks (causal chains) map
  to nearby positions in the review? A review that scatters related ideas across
  sections scores low. Chain preservation ratio = ordered_matches / total_matches.
- **Semantic span**: Convex hull volume ratio in PCA space + Hausdorff distance.
  Measures whether the review spans the same semantic volume as the corpus.
- **Information density**: Gzip compression ratio as Kolmogorov complexity proxy.
- **Reconstruction fidelity (NCD)**: Normalized compression distance — how much
  information the review shares with the corpus at the byte level.

**Layer 3 — Intellectual contribution** (does the review go beyond the corpus?):
- **Gap detection**: Identifies what's MISSING from the corpus — unexplored
  intersections between topics, embedding space voids between research clusters,
  contradictions between papers' conclusions. A good review should name these gaps.
- **Novel synthesis**: Measures whether review chunks draw from multiple papers
  simultaneously without closely copying any single one. A high synthesis score
  means the review creates insights that emerge from combining sources — the
  whole is greater than the sum of parts.

### Gap detection (design)

Two computable signals for gaps in the literature:

1. **Topical intersection gaps**: For each pair of corpus topics (A, B), count
   papers with both. If |A| > 10 and |B| > 10 but |A ∩ B| < 2, the intersection
   is an unexplored gap. Example: "ALD" (45 papers) + "flexible substrates" (12)
   but "ALD on flexible substrates" (2) — an opportunity.

2. **Embedding space voids**: Cluster corpus chunk embeddings. Compute the
   inter-cluster centroid distances. Large voids between clusters represent
   conceptual territories between established themes. Review chunks that fall
   in these voids are addressing gaps.

Both are exposed as agent tools (`find_corpus_gaps`, `find_synthesis_opportunities`)
so the model can discover gaps during exploration, not just have them measured
post-hoc.

### Novel synthesis scoring (design)

For each review chunk r:
1. Find top-k nearest corpus chunks c1..ck
2. Count distinct source papers: source_diversity = |{paper(ci)}|
3. Measure novelty: 1 - max_similarity_to_any_single_chunk
4. Synthesis score = source_diversity * novelty * relevance

A review chunk near 4 different papers at distance ~0.4 each is **synthesizing**.
A chunk near 1 paper at distance ~0.05 is **paraphrasing**.
A chunk far from all corpus chunks is **hallucinating** (or identifying a gap).

The aggregate synthesis score = fraction of review chunks with high synthesis.

### Paper vibes

Token-weighted centroid of chunk embeddings per paper. Produces a single
384-dim vector. Used for:
- Orthogonal neighbor selection (read papers that cover *different* ground)
- Subgraph exhaustion detection (all nearby papers are semantically similar)
- Jump targeting (find the most uncovered distant region)
- Greedy submodular paper ordering (marginal coverage gain per paper)

## Iterative Write-Measure-Read Loop

The agent's exploration strategy is coverage-driven:

```
get_graph_metrics() -> deep_read(hub_1..3) -> write draft
                                                   |
                                                   v
                                        get_coverage_gaps(draft)
                                                   |
                                          delta >= 2%? ----YES----> suggest_next_papers()
                                                   |                        |
                                                  NO                  read 1-3 papers
                                                   |                        |
                                             gaps remain?             revise draft
                                                   |                        |
                                                  YES                       v
                                                   |              get_coverage_gaps()
                                                   v                  (loop back)
                                          find_jump_target()
                                                   |
                                             target found? --NO--> STOP, export
                                                   |
                                                  YES -> read, revise, re-measure
```

Three navigation tools drive the loop:
- `suggest_next_papers`: 0.7 * orthogonality + 0.3 * graph proximity
- `get_coverage_gaps`: coverage delta + gap-to-paper mapping + convergence signal
- `find_jump_target`: detects local exhaustion, jumps to most uncovered distant region

## Dual-Mode Exploration: Generate & Talk

The same exploration tools and coverage-driven navigation apply to both modes:

1. **Generate mode** (`/generate`): Agent reads corpus, writes a paper, iterates
   on coverage. Output is a document (markdown + DOCX + PDF).

2. **Talk mode** (chat/Q&A): Agent uses the same tools to explore the corpus
   in response to user questions. The coverage metric measures how well the
   agent's answers span the relevant corpus content. The navigation tools
   (`suggest_next_papers`, `find_jump_target`) help the agent discover papers
   relevant to follow-up questions without re-reading already-covered ground.

Both modes share: reading log, paper vibes, coverage metric, graph navigation.
The difference is output format (document vs. conversational answers) and
convergence criterion (coverage plateau vs. user satisfaction).

## Scalability & Corpus Growth

### What is incremental (adding a paper does not recompute the world)

| Component | On new paper | On batch ingest |
|-----------|-------------|-----------------|
| **PDF parsing** | Parse 1 file (~3.5s) | Parallel across cores |
| **Chunk embedding** | Embed ~25 chunks (~850ms) | Batch-encode all new chunks |
| **Summary embedding** | Embed 1 summary (<100ms) | Batch-encode all new summaries |
| **k-NN similarity** | Query ChromaDB for 1 paper | Batch query all new papers |
| **Citation matching** | Match 1 paper's refs | Pre-compiled regex, index-based |
| **Topic extraction** | Extract from 1 paper | Pre-compiled vocab patterns |
| **Vault notes** | Write 1 note | Batch write new notes |

ChromaDB's HNSW index supports incremental upsert — adding a paper does not
rebuild the index. SQLite handles concurrent reads naturally.

### What is recomputed on demand (not on every ingest)

| Component | When | Cost at 500 papers |
|-----------|------|-------------------|
| **Graph metrics** (PageRank, centrality) | Before generation | <1s (NetworkX on ~500 nodes) |
| **Paper vibe vectors** | Before generation | 0.4s from stored chunk embeddings |
| **Greedy submodular order** | Before generation | ~3s (lazy greedy with pre-computed paper_sims) |
| **Coverage metric** | After each draft revision | ~17s (encode review chunks + matrix ops) |

These are intentionally not cached because they depend on the full corpus state.
Adding paper #501 changes PageRank for all papers and shifts the greedy order.
Recomputing is cheap enough that caching would add complexity without meaningful
speedup.

### Storage scaling

| Papers | Chunks | SQLite | ChromaDB (summaries) | ChromaDB (chunks) | Total |
|--------|--------|--------|---------------------|-------------------|-------|
| 50 | ~1,300 | 5 MB | 1 MB | 6 MB | ~12 MB |
| 200 | ~5,000 | 18 MB | 4 MB | 24 MB | ~46 MB |
| 500 | ~12,500 | 45 MB | 10 MB | 60 MB | ~115 MB |
| 1,000 | ~25,000 | 90 MB | 20 MB | 120 MB | ~230 MB |

### Known scaling limits

- **Coverage metric**: Encodes review chunks on every call (~2s for the encoding,
  ~15s for the corpus matrix multiply at 1,300 chunks). At 25,000 chunks the
  matrix multiply would take ~5min. Mitigation: approximate nearest neighbors
  via ChromaDB query instead of brute-force matrix multiply.
- **Greedy submodular phase 1**: Pre-computes paper_sims as N matrix multiplies
  of (C, D) @ (K, D).T where C = corpus chunks. At 12,500 chunks this is ~2s
  for 500 papers. Linear scaling, acceptable to 1,000+ papers.
- **Graph construction**: Builds a NetworkX DiGraph with citation + similarity +
  coupling edges. At 500 papers (~1,500 edges) this is <1s. NetworkX handles
  10,000+ nodes without issue.

## Data Model: Corpus vs Output Isolation

```
┌──────────────────────────────────────────────────────────┐
│                     SQLite (papers.db)                     │
│                                                            │
│  ┌─────────┐  origin="corpus"   ┌────────┐                │
│  │  Paper   │──────────────────>│  Chunk  │                │
│  │(ingested)│   1:many          │(corpus) │                │
│  └────┬─────┘                   └─────────┘                │
│       │                                                    │
│       │ many:many                                          │
│       │                                                    │
│  ┌────┴──────────┐                                         │
│  │ ProjectPaper   │                                        │
│  └────┬──────────┘                                         │
│       │                                                    │
│  ┌────┴─────┐   1:many   ┌─────────────────┐              │
│  │ Project   │──────────>│ GeneratedOutput   │              │
│  │(scope)    │           │ (review, paper)   │              │
│  └──────────┘            └──────────────────┘              │
└──────────────────────────────────────────────────────────┘

┌─────────────────────────────────┐
│      ChromaDB (two collections)  │
│                                  │
│  document_summaries (per-paper)  │
│  chunk_embeddings   (per-chunk)  │
│                                  │
│  Only corpus chunks are stored.  │
│  Generated output is NEVER       │
│  embedded into these collections.│
└─────────────────────────────────┘
```

**Key separation rules:**

1. **Paper.origin**: Every paper is either `"corpus"` (ingested from a file) or
   `"generated"` (produced by the writing pipeline). All metric computations
   (coverage, vibes, graph, strategies) filter on `origin="corpus"` only.

2. **Project scoping**: A `Project` groups papers (via `ProjectPaper`) and owns
   outputs (via `GeneratedOutput`). This supports multiple independent research
   projects sharing the same database without cross-contamination. Metrics
   can be scoped to a project's corpus subset.

3. **GeneratedOutput**: Tracks each writing run with metadata (strategy, coverage
   score, token cost, duration). Output files live in `data/output/` — they are
   never ingested back into the corpus unless the user explicitly runs `/ingest`
   on them (which would create a new Paper with `origin="corpus"`).

4. **ChromaDB isolation**: The `chunk_embeddings` and `document_summaries`
   collections store only corpus content. Generated output is never embedded
   into these collections. Coverage is computed by encoding review chunks
   on-the-fly and comparing against the stored corpus embeddings.

## Data Layout

```
data/
├── papers.db               # SQLite
├── chromadb/               # Embedding vectors
├── library.bib             # Auto-generated BibTeX
├── cache/                  # LLM response cache
├── output/                 # Generated papers
└── vault/                  # Obsidian vault
```
