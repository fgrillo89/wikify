# Wikify — Agent Contract

Canonical guide for any agentic runtime (Claude Code, Codex, …).
Sister file: `CLAUDE.md`. Keep the two in sync — the body is
identical; only the filename changes.

Architecture stays runtime-neutral. `.claude/skills/*` and MCP servers
are adapters, not architecture truth.

---

## Read First

1. `docs/architecture.md` — system design
2. `docs/study-design.md` — baseline / scripted / guided conditions
3. `docs/strategies.md` — E / M / X strategy science
4. `docs/metrics.md` — M1–M6, GT-P, GT-C
5. `docs/test-run-playbook.md` — required before any test run
6. `docs/distill-test-readiness.md` — current pre-study readiness state

---

## Current Focus

Active track is `wikify`. Core question: **exploration autonomy** —
does model-driven corpus navigation beat rule-based strategies, and
does anything beat simple retrieve-and-summarise? Modes `baseline` /
`scripted` (E/M/X) / `guided`; see `docs/study-design.md`.
Comparisons require shared telemetry and action interfaces across modes.

---

## Product

- **Input**: papers ingested into a corpus (`data/corpora/`).
- **Process**: distill loop extracts evidence, canonicalises concepts,
  writes wiki pages, iterates.
- **Output**: wiki bundle on disk (`data/wikis/`) rendered to static
  HTML (`_html/`).

Corpus is authoritative evidence. Wiki pages are authoritative
human-facing output. Telemetry is first-class — strategies, prompts,
and costs are compared over time.

---

## Boundaries

- `ingest/` — parse, chunk, embed, graph, citations, manifest.
- `distill/` — the distillation loop: strategy, explorer, pipeline,
  dossier, write prep.
- `eval/` — metrics (M1–M6, GT-P, GT-C).
- `render/html/` — static site generation.
- `store/` — persistence (wiki index, images index).
- `prompts/` — layered prompt system.
- Top-level: `types.py`, `config.py`, `schema.py`, `context.py`,
  `meter.py`, `cache.py`, `embedding.py`, `dispatch.py`, `models.py`,
  `paths.py`, `cli.py`.

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

All commands run under `uv run python -m`. See
`docs/architecture.md` § CLI workflows for full args.

```bash
wikify.cli ingest   <input> --out <corpus> [--mode additive|sync] [--parser default|docling]
wikify.cli distill  --preset <name> --budget 1x --seed 0 --corpus <> --bundle <>
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

## Working Rules

### Before coding

- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them; do not pick silently.
- If a simpler approach exists, say so and push back.
- Translate tasks into verifiable goals: "Add validation" →
  "Write tests for invalid inputs, then make them pass." For
  multi-step work, state a brief plan with verification steps.

### Simplicity first

- No features, abstractions, or configurability beyond what was asked.
- No error handling for impossible scenarios.
- If you wrote 200 lines and it could be 50, rewrite it.

### Surgical changes

- **Stay in lane. Do not touch code unrelated to the user's
  request.** Do not "improve" adjacent code, comments, or
  formatting. Do not refactor what is not broken. Match existing
  style. Every changed line should trace directly to the request.
- **Code YOUR change orphaned: delete in the same commit.** No
  exceptions. Dangling references mislead future readers.
- **Pre-existing dead code or nearby smells: leave them alone.**
  Mention only if they block the task or the user should know.

### Blast radius

Before any non-trivial change (new function, rename, schema field,
flag, module removal):

1. **Map every caller and every consumer.** Grep across `src/`,
   `tests/`, `.claude/skills/`, `docs/`, and any script that imports
   the symbol. No guessing — verify. This is the load-bearing step:
   **no broken link is acceptable.**
2. **Amend every caller in the same commit.** A PR that changes a
   signature and leaves callers broken is a bug. A skill or doc that
   points at a deleted helper is a bug.
3. **Delete code orphaned by this change in the same commit.** Do
   not leave "fallback" files behind.
4. **Name the radius in the commit body.** One sentence: "Touches
   X, Y, Z; no other callers." If you can't name it, you don't know
   what you changed.

---

## Architecture Style

- **Locality of behaviour.** Code that changes together lives together.
- **One data table + one factory** over scattered one-line modules or
  parallel preset / config layers.
- **Classify every new knob before adding it:**
  - **Strategy** → `StrategyConfig` (changes E/M/X science).
  - **Runtime** → `pipeline.run(...)` / `run_with_preloaded(...)`
    (changes this run, not E/M/X).
  - **Mode** → `RuntimeOverrides` or mode action schemas (adaptive
    behaviour during a run).
  - **Adapter** → CLI / skill / MCP wiring, passed inward explicitly.
- **Constructor injection** over hidden mutable globals. Immutable
  module-level instances are fine.
- **Runtime choices are explicit parameters**, not fields smuggled
  into domain config.
- **Protocols for real extension points only.**
- **Enums / dispatch tables** over `if/elif` chains on stable kinds.
- **`__init__.py` files stay boring** — public re-exports only.
- **Vendor / model / provider names stay at adapter boundaries.**
  Core logic uses domain terms (role, tier, strategy id, mode).
- **No dead versioning.** Delete the superseded file in the same
  change. Git history is the changelog; the filesystem is the version.

### Preferred distill shape

```python
class StrategyId(str, Enum):
    EXPLORE = "E"
    MIXED = "M"
    EXPLOIT = "X"


@dataclass
class StrategyConfig:
    name: str
    explorer: Explorer
    budget: BudgetAllocator
    extract_tier: ModelTier
    write_tier: ModelTier
    # edit / compact / orchestrate tiers default from ModelTier
    seed: int = 0


STRATEGY_CONFIGS = {
    StrategyId.MIXED.value: dict(
        name="M", explorer=LevyExplorer(...), budget=AdaptiveBudget(...),
        extract_tier=ModelTier.SMALL, write_tier=ModelTier.MEDIUM,
    ),
}


def build_strategy(strategy_id: StrategyId | str, *, seed: int = 0) -> StrategyConfig:
    key = strategy_id.value if isinstance(strategy_id, StrategyId) else strategy_id
    return StrategyConfig(**STRATEGY_CONFIGS[key], seed=seed)
```

Do NOT:

- split one-line strategy differences across `explore.py` / `mixed.py`
  / `exploit.py`;
- introduce a `Preset` layer that maps 1:1 to `Config`;
- store `model_id` on `StrategyConfig` (routing is by `ModelTier`; no
  `model_id_for_tier()` helper);
- put executable config / factory logic in `__init__.py`;
- add field guides, artifact templates, mode selection, prompt names,
  or CLI-only flags to `StrategyConfig` — those are run parameters or
  adapter concerns.

---

## Writer / Page Rules

- **Titles** are natural Wikipedia style (`Atomic Layer Deposition`,
  not `concept-atomic-layer-deposition`). The id IS the title; `kind`
  distinguishes page type.
- **Articles** are full Wikipedia-style encyclopedic prose — not
  stubs. Sections are guidance, not strict requirements.
- **No visible `[[wikilinks]]` in body prose.** Cross-links live in
  the `links: list[str]` field on `WikiPage` (see `models.py`).
- **Person pages** are written by the model in Wikipedia voice.
  `author_context` carries metadata (primary publications, citations,
  coauthors). The phrase "appears in this corpus" is banned. Degrades
  gracefully if `author_context` is missing.

---

## Error Handling

- Per-call `ValidationError` and `QuoteNotInChunkError` are caught,
  written to `.error.json` next to the request, and skipped so the
  run continues. The `.error.json` IS the log — this is explicit
  logging, not silent-passing.
- Staged `.response.json` must validate against its schema
  (`ExtractResponse`, `WriteResponse`, …) before being consumed.
- Outside those two named exceptions: no bare `except`, no silent
  `pass`. Failures are logged or re-raised — never hidden.

---

## Data-Handling Principles

Distilled from bugs we shipped. These apply to **new** features, not
just the current instances named under each rule. Counterparts to the
code-structure rules in Architecture Style above.

1. **One canonical surface per cross-cutting concern.** Parallel
   external-lookup, classifier, or telemetry paths diverge silently.
   Extend the existing path; don't fork it.
   — `util.doi_resolver.resolve_many` (CrossRef → doi.org → `.citestore.db`
   cache); `metadata.is_junk_title`; shared scripted/guided action dispatch.

2. **Source text is sacred; the query is not.** Normalise the query
   to fit the corpus; leave source text untouched so provenance back
   to raw bytes stays intact.
   — quote-substring validation uses tolerant NFKC + dash + brackets
   + emphasis normalisation on the query side only.

3. **Convert at the boundary; assert at storage.** When a concept is
   indexed differently across a boundary (0- vs 1-based, raw vs
   normalised, display vs storage), convert once at the boundary.
   Callers must not guess.
   — citation ordinals stored one-based in the KG (`ord + 1`) to
   match `[N]` markers.

4. **User-controlled input is ground truth.** Filenames, tags, front
   matter, passed-in parameters beat values inferred by extraction.
   Validate extractions against them; reject mismatches loudly.
   — `choose_document_title` (filename > heading > stem);
   `validate_authors_against_filename`.

5. **Per-field merge, not per-record.** When two sources disagree,
   the winner is decided per field. Record-level precedence hides
   bugs.
   — `doi.org` wins for title / journal / venue / volume / pages /
   publisher / issn / url; local wins for summary and year.

6. **Bidirectional edges are emitted both ways at build time.**
   Downstream code does not infer the reverse.
   — `CITES` edges go corpus→corpus AND corpus→cited; without the
   latter, external refs are isolated.

7. **State for cross-run comparison is persisted explicitly.** Static
   approximations of stateful signals invalidate comparisons.
   — `coverage_gap` residuals persist across refine epochs.

---

## Quality Review Protocol

When asked to assess a pipeline run or any generated artifact: **never
declare it good until you have opened the actual rendered artifact
the user would see.**

1. **Render the output the user would see.** Markdown is intermediate;
   open the HTML / PDF / rendered site. Do not pronounce judgement on
   a `.md` file when an `.html` sits next to it.
2. **Sample across kinds** — article, person, heavily cross-linked,
   sparsely-evidenced, skeleton. Not just the best-looking one.
3. **Compare against the user's implicit reference.** Wikipedia for
   wikis; published papers for papers. Do not lower the bar to match
   what the tool happens to produce.
4. **Check navigation and index.** Does the index enumerate real
   pages? Do internal links resolve?
5. **Look for failure modes explicitly.** Empty pages, run-on prose
   from broken bullets, orphan markers, placeholder text, meta-
   commentary ("this article appears in the corpus"), truncated
   titles, garbage characters.
6. **Report every issue.** Enumerate them. The user will prioritise.
7. **Metrics are a supplement, never a substitute.** M1 / M3 / M6 can
   pass while the HTML is visually broken.
8. **After pipeline changes, assume the output is broken until
   verified.** Green tests do not prove the artifacts look right.

If `docs/test-run-playbook.md` applies, follow it step by step. Do
not improvise the review.

---

## Response Style — Caveman Mode

Default on. Deactivate only when user says `normal mode` or `stop caveman`.

- **Scope**: assistant replies only — not code, commits, PRs, docs.
- **Style**: short, direct, normal grammar; no filler or hedging.
  Preferred shape: `Problem. Cause. Fix. Verify.`
- **Fidelity**: technical terms, code, paths, schemas, quotes exact.
- **Clarity override**: normal prose for security warnings,
  destructive actions, or visible user confusion. Resume after.

---

## Tooling and Interaction

- **Package manager**: `uv`. Use `uv add` (not `uv pip install`) so
  `pyproject.toml` stays in sync.
- **Data library**: polars, not pandas.
- **Tests**: `uv run pytest tests/wikify -q` (full: `uv run pytest -q`).
- **Lint**: `uv run ruff check src/wikify tests/wikify`.
- **Windows console**: ASCII only; no special Unicode.
- **Embeddings GPU**: auto-detected (CUDA > DirectML > CPU).
- **Commit messages**: never include absolute paths or personal PC paths.
- **Hooks**: never skip (`--no-verify`) or bypass signing unless the
  user explicitly asks.

---

## Corrections Log

When the user corrects a mistake, add an entry below. Format:

```
- **Topic**: what went wrong → what to do instead.
```

Promote anything that becomes a standing rule into the body of this
file. Prune duplicates when you promote.

<!-- Add corrections below this line -->
