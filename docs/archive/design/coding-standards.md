# Wikify Coding Standards & Architecture Patterns

Short reference for consistency across the codebase.

## Architecture Principles

1. **Dependency injection over globals.** Pass instances as arguments. Module-level singletons (e.g., `_store = EmbeddingStore()`) are acceptable if assigned once and never mutated via `global`.
2. **Tools are plain functions.** Each tool in `agent/tools.py` is a standalone function with type-hinted args and a Google-style docstring. Schema is auto-generated via introspection in `tool_schema.py`.
3. **Agent loop is generic.** `WikifyAgent` in `core.py` doesn't know about papers — it dispatches tool calls to whatever functions are passed in.
4. **Pydantic for contracts.** LLM output schemas use Pydantic BaseModel. DB models use SQLModel. The two don't mix.
5. **Evaluate module is pure computation.** Quality metrics take text + embeddings as input, return dataclasses. No LLM calls, no DB writes.
6. **Model-agnostic.** All LLM interactions go through litellm. No provider-specific annotations (no `cache_control`, no OpenAI function_call format). Everything works with Claude, GPT-4, DeepSeek, Ollama.

## File Organization

```
agent/          -- LLM orchestration (tools, loop, prompts, workflows)
evaluate/       -- Quality metrics + exploration strategies (pure math)
export/         -- Output formatting (DOCX, PDF, chemistry)
extract/        -- Parsing support (chunking, metadata, citations)
generate/       -- Writing pipeline (planner, writer, verifier)
graph/          -- NetworkX analysis (PageRank, centrality)
ingest/         -- PDF/DOCX ingestion (no LLM)
llm/            -- litellm client + hooks
prompts/        -- Runtime prompt files (style guide, artifact types, fields)
store/          -- SQLite + ChromaDB (models, DB, embeddings)
vault/          -- Obsidian vault generation
```

## Naming Conventions

- **Tools**: verb_noun (`deep_read`, `find_corpus_gaps`, `record_paper_summary`)
- **Metrics**: compute_noun (`compute_coverage`, `compute_bridge_vectors`)
- **Models**: PascalCase nouns (`Paper`, `SourceSummary`, `ResearchNotes`)
- **Config**: snake_case with `WIKIFY_` env prefix
- **CLI commands**: lowercase verbs (`ingest`, `generate`, `refresh`)

## Token Efficiency Patterns

1. **Read-once-summarize**: after deep_read, call record_paper_summary to distill findings. The raw text is compacted; the summary persists.
2. **Per-result compaction**: large tool results truncated after the LLM processes them. Context-aware — papers with summaries are compacted more aggressively.
3. **Session context injection**: all paper summaries auto-injected as a system message after compaction. The model never needs to explicitly recall summaries.
4. **Session-level compaction**: when total message chars exceed threshold, old turns are dropped. System messages and session context preserved.

## Tool Design Rules

- Every tool returns a string (not a dict, not a Pydantic model)
- Tools that read papers accept `reason: str = ""` for reading log
- Tools that modify state (record_paper_summary) return a confirmation string
- Tools should be idempotent when possible (re-calling with same args = same result)
- Large tool results (>5KB) should have a compact alternative (digest vs deep_read)

## Testing

- `uv run pytest` for all tests (647)
- `uv run ruff check --fix .` for linting
- `uv run ruff format .` for formatting
- No mocks for generation tests — use subagent patterns with the corpus
