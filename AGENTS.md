# Wikify — Agent Contract

Canonical project reference for any agentic runtime. Behavior rules (planning, simplicity, blast radius, corrections, etc.) live in `CLAUDE.md` — read that first.

---

## Read First

1. `docs/architecture.md` — system design
2. `docs/study-design.md` — baseline / scripted / guided conditions
3. `docs/strategies.md` — E / M / X strategy science
4. `docs/metrics.md` — M1–M6, GT-P, GT-C
5. `docs/test-run-playbook.md` — required before any test run
6. `docs/distill-test-readiness.md` — current pre-study readiness state

---

## Product

- **Input**: papers ingested into a corpus (`data/corpora/`).
- **Process**: distill loop extracts evidence, canonicalises concepts, writes wiki pages, iterates.
- **Output**: wiki bundle on disk (`data/wikis/`) rendered to static HTML (`_html/`).

Corpus is authoritative evidence. Wiki pages are authoritative human-facing output. Telemetry is first-class — strategies, prompts, and costs are compared over time.

---

## Boundaries

- `ingest/` — parse, chunk, embed, graph, citations, manifest.
- `distill/` — strategy, explorer, pipeline, dossier, write prep.
- `eval/` — metrics (M1–M6, GT-P, GT-C).
- `render/html/` — static site generation.
- `store/` — persistence (wiki index, images index).
- `prompts/` — layered prompt system.
- Top-level: `types.py`, `config.py`, `schema.py`, `context.py`, `meter.py`, `cache.py`, `embedding.py`, `dispatch.py`, `models.py`, `paths.py`, `cli.py`.

Dependency rules:
- `distill` reads `ingest` outputs; does not touch `eval` or `render`.
- `eval` and `render` consume wiki bundles, never modify them.
- `cli.py` is a thin adapter — dependencies in, business logic out.

---

## Data Layout

```
data/
  corpora/    ingested corpora
  wikis/      wiki bundles
  papers/     input PDFs
  cache/      extract cache
  test_runs/  test run outputs
```

---

## CLI

All commands run under `uv run python -m`. See `docs/architecture.md` § CLI workflows for full args.

```bash
wikify.cli ingest   <input> --out <corpus> [--mode additive|sync] [--parser default|docling]
wikify.cli distill  --strategy {E|M|X} --mode {scripted|guided} --phase {all|extract|write} ...
wikify.cli campaign --strategy M --iterations 3 ...
wikify.cli study    --presets <csv> --budgets <csv> --seeds <csv>
wikify.cli eval     --bundle <> --corpus <>
wikify.cli html     --bundle <>
wikify.cli query    --bundle <> "question"
```

---

## Key Vocabulary

| Term | Location | Notes |
|---|---|---|
| `StrategyId` (E / M / X) | `types.py` | Explore, Mixed, Exploit |
| `ModelTier` (S / M / L) | `types.py` | Single tier vocabulary; use `tier.value` for strings |
| `LevyExplorer` | `distill/explorer.py` | Corpus navigation + action dispatch |
| `StrategyConfig` | `distill/strategy.py` | Strategy-knob schema (explorer + budget + tiers + seed) |
| `BudgetAllocator` | `distill/strategy.py` | `StaticBudget`, `AdaptiveBudget` |
| `RuntimeOverrides` | `distill/strategy.py` | Mutable run-time controls |
| Mode `scripted` / `guided` | CLI `--mode` | scripted = rules, guided = LLM |
| Iteration `create` / `refine` / `merge` | CLI `--iteration` | Coverage memory persists across refine |
| `Dispatch` | `dispatch.py` | Single file-based request / response class |

---

## Architecture Style

Classify every new knob before adding it:

- **Strategy** → `StrategyConfig` (changes E/M/X science).
- **Runtime** → `pipeline.run(...)` / `run_with_preloaded(...)` (changes this run, not E/M/X).
- **Mode** → `RuntimeOverrides` or mode action schemas (adaptive behaviour during a run).
- **Adapter** → CLI / skill / MCP wiring, passed inward explicitly.

`StrategyConfig` holds: explorer, budget allocator, tiers, allocation override, seed. It does NOT hold: field guides, artifact templates, mode selection, prompt names, model ids, cache paths, or CLI-only flags.

`ModelTier` is the single vocabulary for S / M / L. Use `tier.value` for cache keys, provenance, or JSON. No `model_id_for_tier()` helper, no parallel `model_id` field.

---

## Writer / Page Rules

- **Titles**: natural Wikipedia style (`Atomic Layer Deposition`, not `concept-atomic-layer-deposition`). The id IS the title; `kind` distinguishes page type.
- **Articles**: full Wikipedia-style encyclopedic prose — not stubs. Sections are guidance, not strict requirements.
- **No visible `[[wikilinks]]` in body prose.** Cross-links live in the `links: list[str]` field on `WikiPage`.
- **Person pages**: written in Wikipedia voice. `author_context` carries metadata (publications, citations, coauthors). The phrase "appears in this corpus" is banned. Degrades gracefully if `author_context` is missing.

---

## Error Handling

- Per-call `ValidationError` and `QuoteNotInChunkError` are caught, written to `.error.json` next to the request, and skipped so the run continues.
- Staged `.response.json` must validate against its schema (`ExtractResponse`, `WriteResponse`, …) before being consumed.
- No bare `except`, no silent `pass`. Failures are logged or re-raised — never hidden.

---

## Data-Handling Principles

1. **One canonical surface per cross-cutting concern.** Extend the existing lookup / classifier / telemetry path; don't fork it.
2. **Source text is sacred; the query is not.** Normalise the query to fit the corpus; leave source text untouched so provenance stays intact.
3. **Convert at the boundary; assert at storage.** Convert once at the seam (e.g. 0- vs 1-based, raw vs normalised). Callers must not guess.
4. **User-controlled input is ground truth.** Filenames, tags, front matter, passed-in parameters beat inferred values. Validate extractions against them; reject mismatches loudly.
5. **Per-field merge, not per-record.** When two sources disagree, the winner is decided per field.
6. **Bidirectional edges are emitted both ways at build time.** Downstream code does not infer the reverse.
7. **State for cross-run comparison is persisted explicitly.** Static approximations of stateful signals invalidate comparisons (`coverage_gap` residuals persist across refine epochs).
