# Wikify Agent Contract

This repo should work well across agentic runtimes such as Codex and Claude
Code. Product architecture must stay runtime-neutral.

## Docs

All docs live under `src/wikify/`. Read in this order:

1. `src/wikify/architecture.md` -- system design
2. `src/wikify/strategies.md` -- E/M/X strategy science
3. `src/wikify/metrics.md` -- M1-M6, GT-P, GT-C
4. `src/wikify/runbook.md` -- operational procedures
5. `src/wikify/test-run-playbook.md` -- required before any test run

## Product Model

- Input: PDF papers ingested into a corpus (`data/corpora/`).
- Process: distill loop extracts evidence, writes wiki pages, iterates.
- Output: wiki bundle on disk (`data/wikis/`), rendered as static HTML.
- Corpus is authoritative evidence; pages are authoritative human-facing output.
- Telemetry is first-class: strategies, prompts, and costs are compared over time.

## Boundaries

- `ingest/` -- parse, chunk, embed, graph, citations
- `distill/` -- the distillation loop: strategy, explorer, pipeline, dossier, write prep
- `eval/` -- metrics (M1-M6, GT-P, GT-C)
- `render/html/` -- static site generation
- `store/` -- persistence (wiki index, images index)
- `prompts/` -- layered prompt system
- Top-level modules: `types.py`, `config.py`, `schema.py`, `context.py`, `meter.py`,
  `cache.py`, `embedding.py`, `dispatch.py`, `models.py`, `paths.py`, `cli.py`

Rules:

- `distill` depends on `ingest` outputs but not on `eval` or `render`.
- `eval` and `render` consume wiki bundles but do not modify them.
- CLI is an adapter -- it wires dependencies, not business logic.

## Data Layout

```
data/
  corpora/    -- ingested corpora
  wikis/      -- wiki bundles
  papers/     -- input PDFs
  downloads/  -- downloaded sources
  sources/    -- raw source files
  cache/      -- extract cache
  test_runs/  -- test run outputs
```

## CLI Commands

```
uv run python -m wikify.cli ingest ...
uv run python -m wikify.cli distill --strategy {E|M|X} --mode {scripted|guided} ...
uv run python -m wikify.cli campaign --strategy M --iterations 3 ...
uv run python -m wikify.cli eval --bundle ... --corpus ...
uv run python -m wikify.cli query --bundle ... "question"
uv run python -m wikify.cli html --bundle ...
```

## Key Vocabulary

| Term | Location | Notes |
|------|----------|-------|
| `StrategyId` (E/M/X) | `types.py` | Explore, Mixed, Exploit |
| `ModelTier` (S/M/L) | `types.py` | Single tier vocabulary everywhere |
| `LevyExplorer` | `distill/explorer.py` | Corpus navigation + action dispatch |
| `BudgetAllocator` | `distill/strategy.py` | `StaticBudget`, `AdaptiveBudget` |
| `RuntimeOverrides` | `distill/strategy.py` | Mutable run-time controls |
| Mode: `scripted`/`guided` | CLI `--mode` | Was policy; scripted = rules, guided = LLM |
| `Dispatch` | `dispatch.py` | Single file-based request/response class |

## Architecture Style

- Locality of behavior: code that changes together lives together.
- One data table + one factory over scattered one-line modules.
- Classify knobs: strategy (StrategyConfig), runtime (pipeline args),
  mode (RuntimeOverrides), adapter (CLI wiring).
- `ModelTier` is the single vocabulary for S/M/L. Use `tier.value` for strings.
- Delete superseded modules/files in the same change. No dead versioning.

## Testing

```
uv run pytest tests/wikify -q
uv run ruff check src/wikify tests/wikify
```
