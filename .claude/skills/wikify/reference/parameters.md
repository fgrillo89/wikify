---
name: wikify/reference/parameters
description: User-settable parameter catalog for wikify workflows and CLI — defaults, ranges, categories.
---

# wikify parameters reference

Every user-settable knob, grouped by category, with defaults and acceptable ranges. Refer to this when building workflows or invoking the CLI directly.

## Paths

| Flag | Default | Description |
|---|---|---|
| `--corpus` | `data/corpus` | Ingested corpus directory. |
| `--out` | `data/wikis` | Bundle output directory. |
| `--cache` | `data/cache/extract` | Per-chunk extraction cache. |
| `--merge-from` | — | For `--iteration merge`: the second bundle to merge. |

## Run identity

| Flag | Default | Description |
|---|---|---|
| `--strategy` | required | `E` (breadth), `M` (mixed, headline), `X` (depth). |
| `--seed` | `0` | RNG seed. |
| `--iteration` | `create` | `create` (from empty) / `refine` (add to existing) / `merge` (union two bundles). |
| `--phase` | `all` | `extract` (stop after saving write requests) / `write` (resume) / `all`. |

## Policy

| Flag | Default | Description |
|---|---|---|
| `--mode` | `scripted` | `scripted` (deterministic sampler) / `guided` (orchestrator picks actions). |

## Budget

| Flag | Default | Description |
|---|---|---|
| `--budget` | `1x` | Haiku-equivalent tokens. Accepts: raw integer (`50000`), suffixed (`50k`, `1.5M`), or shortcut (`0.1x`=5k, `1x`=50k, `3x`=150k). |

## Tier (per-role model size)

Tiers are `S` (haiku-class), `M` (sonnet-class), `L` (opus-class). Cost per token grows ~15x from S to M and ~5x from M to L.

| Flag | Default | Description |
|---|---|---|
| `--extract-tier` | `S` | Chunk extraction model tier. |
| `--write-tier` | `M` | Page writer model tier. |
| `--edit-tier` | `M` | Editor brief model tier. |
| `--compact-tier` | `S` | Dossier compactor model tier. |

**`orchestrate_tier` is LOCKED at `L` (opus).** It is not a CLI flag. This is what makes escalation work: the orchestrator is always the highest tier so lower tiers can reach out to it for judgment calls.

## Allocation

| Flag | Default | Description |
|---|---|---|
| `--exploit-fraction` | strategy default | Fraction of budget (0..1) allocated to the write phase. The remainder goes to extract (minus a fixed 5% curate slice). |

Strategy defaults: E=0.2, M=0.65 (adaptive), X=0.6.

## Prompt layering

| Flag | Default | Description |
|---|---|---|
| `--field` | auto-detect from corpus topics | Field guide YAML (e.g. `materials_science`, `biology`, `physics`). See `src/wikify/prompts/fields/`. |
| `--artifact` | `wiki_article` | Artifact template (one of `wiki_article`, `wiki_person`). See `src/wikify/prompts/artifact_types/`. |

## Environment variables

| Var | Default | Description |
|---|---|---|
| `WIKIFY_DISPATCH_DIR` | `data/dispatch` | Base directory for file-dispatch requests. |
| `WIKIFY_EMBEDDER` | `fastembed` | Embedder backend: `fastembed` (ONNX, default) or `hash` (offline, 128d, CI only). |
| `WIKIFY_EMBED_MODEL` | `jinaai/jina-embeddings-v2-small-en` | HF model name. See `_MODEL_CONFIGS` in `src/wikify/embedding.py`: MiniLM (fast, 512-tok), bge-small-v1.5, jina-v2-small (default, 8192-tok), nomic-v1.5-Q (8192-tok, slow). |
| `WIKIFY_EMBED_BATCH_SIZE` | per-model | Override per-model batch size (nomic defaults to 32, MiniLM/bge to 256, jina to 128). |
| `WIKIFY_SKIP_PAGE_ID_MIGRATION` | unset | Skip the `concept-*.md` → natural-title migration pass. |

## What the user CANNOT set directly
- Orchestrate tier (locked at L).
- The orchestrator's action selection (that's the guided's job).
- The per-chunk cache key (computed from prompt + chunk content).
- The cost meter thresholds (hardcoded at 1.05× budget abort).

## What the LLM policy can override mid-run
When running with `--mode guided`, the orchestrator can change:
- `exploit_fraction` (via `set_allocation`)
- `extract_tier`, `write_tier`, `edit_tier`, `compact_tier` (via `set_tier`)

See `reference/orchestrator.md` for the full action catalog.
