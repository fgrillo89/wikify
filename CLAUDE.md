## Current Focus

`wikify` is the active track for strategy science. The core question is strategy quality vs token cost vs wall-clock time, comparing:

- `scripted` mode: scripted exploration and budget allocation
- `guided` mode: model-driven exploration and budget allocation

All comparisons must run under the same pipeline contract and telemetry.

## Communication Style

- Concise and direct. No filler, no pleasantries, no soft hedging.
- Keep technical terms, code, commands, errors, paths, schemas, and quoted text exact.
- Default structure: `Problem. Cause. Fix. Verify.`
- Override terseness for security warnings, destructive actions, multi-step instructions where brevity risks mistakes, or visible user confusion.

## Think Before Coding

- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them. Do not pick silently.
- If a simpler approach exists, say so. Push back when warranted.

## Simplicity First

- No features, abstractions, or error handling beyond what was asked.
- No "flexibility" or "configurability" that was not requested.
- If you wrote 200 lines and it could be 50, rewrite it.
- **No dead versioning.** The filesystem IS the version, git history IS the changelog. When a new prompt, schema, template, module, or plan supersedes an old one, delete the old one and keep the new one under the canonical name. Do NOT leave `foo_v1.yaml` next to `foo_v2.yaml` as "fallback." Do NOT append version suffixes. The only exception is a real migration where the old version is still reachable from production code, tracked with a deadline. A file left "just in case" becomes a second source of truth that silently diverges.

## Architectural Style

Write code so the reader can understand business behavior without jumping through a maze of tiny abstractions. SOLID principles apply: single responsibility per unit, open/closed when extension is a real requirement, substitutability at interface boundaries, narrow interfaces over fat ones, depend on abstractions at adapter seams.

- Locality of behavior: code that changes together lives together.
- Prefer one explicit data table over several one-line modules or subclasses when behavior differs only by configuration.
- Keep `__init__.py` boring: public re-exports only. No config, factories, side effects, or business behavior.
- Do not smuggle runtime choices into domain config by mutating objects after construction. Pass runtime choices as explicit parameters.
- For small closed vocabularies, use a shared enum at the contract boundary. Do not scatter ad hoc strings or tiny conversion helpers.
- Vendor/model/provider names belong at adapter boundaries. Core logic uses domain terms (role, tier, strategy id, mode).
- Prefer explicit boundaries and dependency direction.
- Prefer constructor injection over hidden mutable globals. Immutable module-level instances are fine; mutable globals are not.
- Use protocols only for real extension points.
- Keep explorer, mode, and metric logic testable without live model calls.
- Keep scale-sensitive paths near-linear (explorer and crosslink hot paths).

Good shape:

```python
DEFAULTS = {
    "balanced": dict(
        explorer=LevyExplorer(...),
        budget=AdaptiveBudget(...),
        tier=ModelTier.MEDIUM,
    ),
}


def build_config(kind: ConfigId | str, *, seed: int = 0) -> Config:
    key = kind.value if isinstance(kind, ConfigId) else kind
    return Config(**DEFAULTS[key], seed=seed)
```

Bad smells:

```python
# Several modules that only differ by constants.
# A Preset object that only maps 1:1 to Config.
# A Strategy object whose only behavior is returning Config.
# Domain config mutated by the CLI to carry runtime-only options.
# A helper that turns Enum("M") into "tier-M" when tier.value would do.
# Executable config/factory logic in __init__.py.
```

## Surgical Changes

When editing existing code:

- Do not "improve" adjacent code, comments, or formatting.
- Do not refactor things that are not broken.
- Match existing style, even if you would do it differently.
- If you notice pre-existing, unrelated dead code, mention it. Do not delete it.
- Every changed line should trace directly to the user's request.

## Blast Radius Discipline

Before shipping ANY non-trivial change (new function, renamed symbol, rewritten handler, schema field, CLI flag, removed module):

1. **Enumerate every caller and consumer.** Use Grep aggressively across `src/`, `tests/`, and `.claude/skills/`. Do not guess — verify.
2. **Amend every caller in the same commit.** A signature change that leaves callers broken is a bug. A skill that references a deleted helper is a bug.
3. **Delete orphans in the same commit.** If your change makes a helper, branch, test fixture, or module unused, delete it. Also remove imports, variables, and functions your change made unused. Dangling references to removed features mislead future readers.
4. **Name the blast radius in the commit body.** One sentence: "Touches X, Y, Z; no other callers." If you can't name the radius, you don't know what you changed.

## Goal-Driven Execution

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"

For multi-step tasks, state a brief plan with verification steps.

## Parallelism

Use subagents whenever the task is parallelisable: independent file reads, broad codebase searches, multi-target audits, independent tool calls. Dispatch them in a single message so they run concurrently. Do not serialize work that has no data dependency.

## Quality Review Protocol

When assessing the output of a pipeline run, a test run, or any generated artifact: **never declare it good until you have opened the actual rendered artifact the user would see.** This rule exists because "looks great" reviews have shipped based on intermediate formats while the rendered output was broken.

1. **Render the output the way the user would see it.** Markdown is intermediate; open the HTML, PDF, rendered site, or downstream tool output. Do not judge a `.md` file when an `.html` sits next to it.
2. **Sample across all kinds and variations.** For wikis: an article page, a person page, a heavily cross-linked page, a sparsely-evidenced page, a skeleton. For papers: intro, methods, a figure page. For agents: first, middle, final response. No cherry-picking.
3. **Compare against the user's implicit reference.** Wikipedia standard = real Wikipedia article (H2 sections with meaningful labels, inline citations, no meta-commentary, clean bullet lists, wikilinks that resolve). Paper standard = real published paper. Do not lower the bar to match what the tool happens to produce.
4. **Check navigation and index.** Does the index only enumerate real pages? Do internal links resolve? Does the HTML landing page reflect the real page count?
5. **Look for failure modes explicitly.** Empty pages, bullet lists rendered as run-on prose, orphan markers, placeholder text (`1.`, "See references", `[TITLE]`), meta-commentary about "the corpus", missing sections, truncated titles, garbage characters.
6. **Enumerate every issue. Do not stop at the worst.** The user will prioritize.
7. **Metrics are a supplement, never a substitute.** M1/M3/M6 can pass while rendered output is broken; the converse is rare. Green metrics + broken HTML means the metrics are wrong, not that the output is fine.
8. **After pipeline changes, assume output is broken until verified.** Green tests prove the code compiles and tests pass; they do not prove artifacts look right.

## Wikify

### Read First

For `wikify` work, read in this order:

1. `docs/architecture.md`
2. `docs/strategies.md`
3. `docs/metrics.md`
4. `docs/runbook.md`
5. `docs/test-run-playbook.md` (required before any test run)

### Ground Rules

- Product artifact is the wiki bundle on disk.
- Corpus is authoritative evidence; pages are authoritative human-facing outputs.
- Structured state supports retrieval, provenance, graph reasoning, and telemetry.
- Iteration is first-class: `create`, `refine`, `merge`.
- Run and provenance history are append-only.
- Coverage memory must be stateful: real `coverage_gap` updates persist across refine epochs. Static coverage scores invalidate strategy comparisons.
- `scripted` and `guided` modes must emit actions through one shared interface with common telemetry fields. Strategy comparisons are only valid when telemetry and action interfaces match.

## Distill Design Rules

Structure follows the business logic of a run:

- `distill/strategy.py` owns the E/M/X strategy table, budget allocation, run modes (scripted/guided), and the single factory.
- `distill/explorer.py` owns corpus navigation (`LevyExplorer`), action dispatch, and `build_snapshot`.
- `distill/pipeline.py` owns run-time execution and prompt-layer choices.
- CLI code is an adapter. It wires dependencies and passes user choices in; it is not a second place for distill behavior.

`StrategyConfig` is for what actually varies between E/M/X: explorer, budget allocator, tiers, allocation override, seed. It does NOT carry field guides, artifact templates, mode selection, prompt names, model ids, cache paths, or CLI-only flags — those are run parameters or adapter concerns, passed as explicit arguments to the pipeline.

`ModelTier` is the single vocabulary for `S`, `M`, `L`. Request schemas, mode runtime, strategy configs, and cost accounting use `ModelTier` directly. When a string label is needed for cache keys, provenance, or JSON, use `tier.value`. Do not introduce a `model_id_for_tier()` helper or a parallel `model_id` field on `StrategyConfig`.

When introducing a new distill knob, classify it first:

- **Strategy knob**: changes E/M/X science → `StrategyConfig`.
- **Runtime knob**: changes this run without defining E/M/X → `pipeline.run(...)` / `run_with_preloaded(...)`.
- **Mode knob**: changes adaptive behavior during a run → `RuntimeOverrides` or mode action schemas.
- **Adapter knob**: CLI- or runtime-specific wiring → the adapter, passed inward explicitly.

## Runtime Neutrality

- `distill/*` owns business logic and strategy behavior.
- `dispatch.py` is the single adapter for file-based request/response.
- `.claude/skills/*` are execution helpers (adapters), not architecture truth.

No product logic should depend on one runtime vendor.

## Preferred Operations

Use `wikify` CLI workflows instead of ad hoc file mutation:

- `uv run python -m wikify.cli ingest ... [--mode additive|sync] [--parser default]`
- `uv run python -m wikify.cli distill --strategy {E|M|X} --mode {scripted|guided} ...`
- `uv run python -m wikify.cli distill --phase extract|write|all ...`
- `uv run python -m wikify.cli campaign --strategy M --iterations 3 ...`
- `uv run python -m wikify.cli eval --bundle ... --corpus ...`
- `uv run python -m wikify.cli query --bundle ... "question"`
- `uv run python -m wikify.cli html --bundle ...`

## Prompt and Schema Rules

- Staged `.response.json` files must validate against the matching schema (`ExtractResponse`, `WriteResponse`, etc.) before consumption.
- Validation failures must produce explicit `.error.json` artifacts.
- Per-call `ValidationError` and `QuoteNotInChunkError` are caught and skipped. The run continues; `.error.json` artifacts are left for postmortem. Never hide failures with bare `except` or silent `pass`.
- Quote substring validation uses tolerant normalization (NFKC + dash + brackets + emphasis). Pick verbatim phrases from clean chunks; do not normalize chunk text when selecting quotes.

## Python Tooling

- Package manager: `uv`. Always use `uv add`, not `uv pip install`, so `pyproject.toml` stays in sync.
- Lint (focused): `uv run ruff check src/wikify tests/wikify`
- Tests (focused): `uv run pytest tests/wikify -q`
- Full tests when needed: `uv run pytest -q`

## Corrections And Lessons Learned

When the user corrects a mistake or misinterpretation, add an entry below.

Format: `- **Topic**: What went wrong → what to do instead.`

- **Data libraries**: Use polars over pandas.
- **Commit messages**: Never include absolute or personal PC paths.
- **Unicode on Windows**: Avoid non-ASCII in console output; use ASCII.
- **wikify page names**: Use natural Wikipedia-style titles ("Atomic Layer Deposition", not "concept-atomic-layer-deposition"). The `kind` field distinguishes page types; the `id` IS the title.
- **wikify writer**: Pages must be full Wikipedia-style encyclopedic articles, not stubs. Sections are guidance, not strict requirements. No visible `[[wikilinks]]` in prose.
- **wikify person pages**: Person pages are written by the model like article pages. Author metadata (primary publications, citations, coauthors) is assembled at ingest/distill time and attached to the writer's `WriteRequest` as `author_context` for grounding. The writer produces biographical prose in Wikipedia voice; the "appears in this corpus" phrasing is banned. Must be robust to missing `author_context` for persons mentioned in text but not authors.
