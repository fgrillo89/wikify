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
│   ├── models.py                   # Paper, Chunk, Citation, JournalTemplate, etc.
│   ├── db.py                       # Engine + session management
│   └── embeddings.py               # EmbeddingStore (DI-friendly)
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

## Evaluate: Semantic Coverage & Paper Vibes

```
src/scholarforge/evaluate/
├── __init__.py
└── coverage.py          # compute_coverage(), compute_paper_vibes()
```

**Coverage metric**: Embeds both corpus chunks and review/output chunks into
the same vector space (all-MiniLM-L6-v2, 384-dim), then measures what fraction
of the corpus's semantic content has a nearby counterpart in the output. This
approximates an information-theoretic compression quality metric: the review is
a lossy compression of the corpus, and coverage measures signal retention.

**Paper vibes**: Token-weighted centroid of chunk embeddings per paper. Produces
a single 384-dim vector capturing the paper's semantic identity. Used for:
- Orthogonal neighbor selection (read papers that cover *different* ground)
- Subgraph exhaustion detection (all nearby papers are semantically similar)
- Jump targeting (find the most uncovered distant region)

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
