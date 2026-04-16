# Claude Code - Working Conventions

Runtime-specific guidance for using this repo through Claude Code.
This file is not the architecture source of truth.

## Current Focus

`wikify` is the active track for strategy science.
The core question is exploration autonomy: does giving the model control
over corpus navigation produce better wikis than rule-based strategies,
and does any of it beat simple retrieve-and-summarise?

- baseline mode: retrieve-and-summarise (no iterative exploration)
- scripted mode: deterministic LevyExplorer (E/M/X strategies)
- guided mode: model navigates via interactive KG tool-calling

The pipeline is parametric from fully deterministic to fully agentic.
Named presets capture key study conditions. See `docs/study-design.md`.

All comparisons must run under the same pipeline contract and telemetry.

## Behavioral Guidelines

### Think Before Coding
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them. Do not pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop and ask.

### Simplicity First
- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that was not requested.
- No error handling for impossible scenarios.
- If you wrote 200 lines and it could be 50, rewrite it.
- **No dead versioning.** When iterating on a file (prompt, schema, template, plan), delete the old version and keep the new one under the canonical name. Do NOT leave `foo_v1.yaml` sitting next to `foo_v2.yaml` as a fallback "just in case." Do NOT rename the file by appending a version suffix -- the file system IS the version, git history IS the changelog. The only acceptable version-suffixed files are those where the OLD version is still actively reachable from production code during a real migration, and in that case the migration must be on a tracked task with a deadline.

### Basic Caveman Mode
- Purpose: always-on, token-saving communication style for assistant replies.
- Activation: default on for all replies in this repo; no opt-in phrase required.
- Deactivation: only when user says `normal mode` or `stop caveman`.
- Scope: applies to assistant replies only, not generated code, commit messages, PR text, docs, or user-facing artifacts unless explicitly requested.
- Persistence: stays active across turns until explicitly stopped.
- Trigger policy: always enabled by default; brevity requests do not change mode state.
- Style: concise and direct with normal grammar, clear ordering, and no filler, pleasantries, or soft hedging.
- Fidelity: keep technical terms, code, commands, errors, paths, schemas, and quoted text exact.
- Structure preference: `Problem. Cause. Fix. Verify.`
- Temporary clarity override: use normal clarity for security warnings, destructive actions, multi-step instructions where terse phrasing risks mistakes, or visible user confusion, then resume caveman style.
- Prohibited: heavy abbreviation, stylized dialects, fake primitive speech, or comic phrasing.

### Architectural Style
Write code so the reader can understand the business behavior without jumping
through a maze of tiny abstractions. Locality of behavior is the default.

- Code that changes together should live together.
- Prefer one explicit data table over several one-line modules or subclasses
  when behavior differs only by configuration.
- Prefer one config object with clear ownership over parallel concepts such as
  "preset", "strategy", "factory config", and "runtime config" unless each has
  a distinct job that can be explained in one sentence.
- Keep `__init__.py` files boring: public re-exports only. Do not put config,
  factories, side effects, or business behavior there.
- Classify every new knob before adding it:
  - Domain or strategy knob: belongs with the business object it changes.
  - Runtime knob: belongs on the pipeline/service function that runs the work.
  - Adapter knob: belongs in CLI/MCP/skill/runtime wiring and is passed inward
    explicitly.
  - Mode knob: belongs in `RuntimeOverrides` or the strategy's mode logic.
- Do not smuggle runtime choices into domain config by mutating config objects
  after construction. Pass runtime choices as explicit parameters.
- For small closed vocabularies, use a shared enum at the contract boundary.
  Do not scatter ad hoc strings or tiny conversion helpers through the code.
- Vendor/model/provider names should stay at adapter boundaries. Core business
  logic should use domain terms such as role, tier, strategy id, or mode.
- A factory should instantiate; it should not hide a second registry. If a
  registry stores constructor defaults, prefer `Thing(**DEFAULTS[key], seed=seed)`
  over building an object and then cloning/replacing it.
- Delete superseded structure in the same change. Leaving the old module,
  alias, preset layer, or helper behind creates a second source of truth.

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

Bad smell:

```python
# Three modules that only differ by constants.
# A Preset object that only maps 1:1 to Config.
# A Strategy object whose only behavior is returning Config.
# A domain config mutated by the CLI to carry runtime-only options.
# A helper that turns Enum("M") into "tier-M" when tier.value would do.
```

### Surgical Changes
When editing existing code:
- Do not "improve" adjacent code, comments, or formatting.
- Do not refactor things that are not broken.
- Match existing style, even if you would do it differently.
- If you notice unrelated dead code, mention it. Do not delete it.
- Remove imports/variables/functions that YOUR changes made unused.
- Do not remove pre-existing dead code unless asked.
- Every changed line should trace directly to the user's request.

### Blast Radius Discipline
Before shipping ANY non-trivial change (new function, renamed symbol,
rewritten handler, schema field, CLI flag, removed module):
1. **Enumerate every caller and every consumer** of the thing you're
   changing. Use Grep aggressively. Do not guess -- verify.
2. **Amend every caller in the same commit.** A PR that updates a
   function signature but leaves callers broken is a bug. A skill that
   references a deleted helper is a bug.
3. **Delete orphaned code.** If your change makes a helper, a branch, a
   test fixture, or an entire module unused, DELETE it in the same
   commit. Dangling references to removed features are worse than the
   features themselves -- they mislead future readers.
4. **Delete superseded files, don't leave them as "fallback."** See the
   no-dead-versioning rule. A file left "just in case" becomes a second
   source of truth that silently diverges.
5. **When in doubt, grep for the symbol name across `src/`, `tests/`,
   and `.claude/skills/`.**
6. **Name the blast radius in your commit body.** One sentence:
   "Touches X, Y, Z; no other callers." This forces you to actually
   look. If you can't name the radius, you don't know what you changed.

### Goal-Driven Execution
Transform tasks into verifiable goals:
- "Add validation" -> "Write tests for invalid inputs, then make them pass"
- "Fix the bug" -> "Write a test that reproduces it, then make it pass"

For multi-step tasks, state a brief plan with verification steps.

### Quality Review Protocol

When asked to assess the output of a pipeline run, a test run, or any generated artifact: **never declare it good until you have opened the actual rendered artifact the user would see.** This rule exists because I have shipped "looks great" reviews based on intermediate formats while the rendered output was broken. Concretely:

1. **Render the output the way the user would see it.** Markdown bodies are intermediate; the user looks at HTML, PDFs, rendered sites, or downstream tool output. Open those files. Do not pronounce judgement on a `.md` file when an `.html` sits next to it.
2. **Sample across all kinds and variations, not the best-looking one.** For wikis: at least one article (concept) page, one people page, one heavily cross-linked page, one sparsely-evidenced page, one skeleton. For papers: intro, methods, a figure page. For agents: first response, middle response, final response. Cherry-picking the best example is not quality review.
3. **Compare against the user's implicit reference.** If the target is Wikipedia, the standard is a real Wikipedia article: H2 sections with meaningful labels, inline citations, no meta-commentary ("this article appears in the corpus"), clean bullet lists, wikilinks that resolve. If the target is a scientific paper, the standard is a real published paper. Do not lower the bar to match what the tool happens to produce.
4. **Check navigation and index.** Does the index only enumerate real pages? Do internal links resolve? Does the HTML landing page reflect the real page count?
5. **Look for failure modes explicitly.** Empty pages, broken bullet lists rendered as run-on prose, orphan markers, placeholder text (`1.`, "See references", `[TITLE]`), meta-commentary that refers to "the corpus" as an entity, missing sections, truncated titles, garbage characters.
6. **Report every issue you find.** Do not pick the worst one and stop. Enumerate them. The user will prioritize.
7. **Metrics are a supplement, never a substitute.** M1/M3/M6 can pass while the rendered output is visually broken; the converse is rarely true. A green metrics report with broken HTML means the metrics are wrong, not that the output is fine.
8. **After pipeline changes, assume the output is broken until you have verified otherwise.** A "green" test suite proves the code compiles and tests pass; it does not prove the generated artifacts look right.

If a test-run playbook exists (`docs/test-run-playbook.md`), follow it step by step. Do not improvise the review.

## Read First

For `wikify` work, read in this order:

1. `docs/architecture.md`
2. `docs/study-design.md`
3. `docs/strategies.md`
4. `docs/metrics.md`
5. `docs/test-run-playbook.md` (required before any test run)

## Wikify Ground Rules

- Product artifact is the wiki bundle on disk.
- Corpus is authoritative evidence; pages are authoritative human-facing outputs.
- Structured state supports retrieval, provenance, graph reasoning, and telemetry.
- Strategy comparisons are only valid when telemetry and action interfaces are shared.
- Iteration is first-class: `create`, `refine`, `merge`.
- Run and provenance history are append-only.
- Coverage memory persists across epochs where refine semantics require it.

## Distill Design Rules

The general architectural style above applies directly to distill. Keep distill
easy to read at a glance. Structure should follow the business logic of a run:

- `distill/strategy.py` owns the E/M/X strategy table, budget allocation,
  run modes (scripted/guided), and the single factory.
- `distill/explorer.py` owns corpus navigation (`LevyExplorer`), action
  dispatch, and `build_snapshot`.
- `distill/pipeline.py` owns run-time execution and prompt-layer choices.
- CLI code is an adapter. It wires dependencies and passes user choices in,
  but should not become a second place where distill behavior lives.

Strategy config is for what actually varies between E/M/X: explorer, budget
allocator, tiers, allocation override, and seed. Do not add field guides,
artifact templates, mode selection, prompt names, model ids, cache paths,
or CLI-only flags to `StrategyConfig`. Those are run parameters or adapter
concerns and should be explicit arguments to the pipeline.

Preferred distill shape:

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
    edit_tier: ModelTier = ModelTier.MEDIUM
    compact_tier: ModelTier = ModelTier.SMALL
    orchestrate_tier: ModelTier = ModelTier.LARGE
    exploit_fraction_override: float | None = None
    seed: int = 0


STRATEGY_CONFIGS = {
    StrategyId.MIXED.value: dict(
        name="M",
        explorer=LevyExplorer(...),
        budget=AdaptiveBudget(...),
        extract_tier=ModelTier.SMALL,
        write_tier=ModelTier.MEDIUM,
    ),
}


def build_strategy(strategy_id: StrategyId | str, *, seed: int = 0) -> StrategyConfig:
    key = strategy_id.value if isinstance(strategy_id, StrategyId) else strategy_id
    return StrategyConfig(**STRATEGY_CONFIGS[key], seed=seed)
```

Avoid:

```python
# Do not split one-line strategy differences across explore.py/mixed.py/exploit.py.
# Do not make both "preset" and "config" layers unless they have different jobs.
# Do not store model_id on StrategyConfig when routing is by ModelTier.
# Do not put executable config/factory logic in __init__.py.
```

`ModelTier` is the single vocabulary for `S`, `M`, and `L`. Request schemas,
mode runtime, strategy configs, and cost accounting should use `ModelTier`
directly. When a string label is needed for cache keys, provenance, or JSON,
use `tier.value`; do not introduce a `model_id_for_tier()` helper or a parallel
strategy-level `model_id` field.

When introducing a new distill knob, first classify it:

- Strategy knob: changes E/M/X science, belongs in `StrategyConfig`.
- Runtime knob: changes this run without defining E/M/X, belongs on
  `pipeline.run(...)` / `run_with_preloaded(...)`.
- Mode knob: changes adaptive behavior during a run, belongs in
  `RuntimeOverrides` or mode action schemas.
- Adapter knob: CLI or runtime-specific wiring only, stays in the
  adapter and is passed inward explicitly.

## Runtime Neutrality

Keep product architecture runtime-neutral:

- `distill/*` owns business logic and strategy behavior.
- `dispatch.py` is the single adapter for file-based request/response.
- `.claude/skills/*` are execution helpers, not architecture truth.

No product logic should depend on one runtime vendor.

## Preferred Operations

Use `wikify` CLI workflows instead of ad hoc file mutation:

- `uv run python -m wikify.cli ingest ... [--mode additive|sync] [--parser default|docling]`
- `uv run python -m wikify.cli distill --strategy {E|M|X} --mode {scripted|guided} ...`
- `uv run python -m wikify.cli distill --phase extract|write|all ...`
- `uv run python -m wikify.cli campaign --strategy M --iterations 3 ...`
- `uv run python -m wikify.cli eval --bundle ... --corpus ...`
- `uv run python -m wikify.cli query --bundle ... "question"`
- `uv run python -m wikify.cli html --bundle ...`

## Prompt and Schema Rules

- Staged `.response.json` files must validate against the matching schema
  (`ExtractResponse`, `WriteResponse`, etc.) before consuming them.
- Validation failures should produce explicit `.error.json` artifacts.

## Python Tooling

- Package manager: `uv`
- Lint (focused): `uv run ruff check src/wikify tests/wikify`
- Tests (focused): `uv run pytest tests/wikify -q`
- Full tests when needed: `uv run pytest -q`

## Code Quality

- Prefer small, responsibility-focused modules.
- Prefer explicit boundaries and dependency direction.
- Prefer constructor injection over hidden mutable globals.
- Use protocols only for real extension points.
- Keep explorer, mode, and metric logic testable without live model calls.
- Keep scale-sensitive paths near-linear where possible (explorer and crosslink hot paths).

## Corrections And Lessons Learned

When the user corrects a mistake or misinterpretation, add an entry below.

Format:
`- **Topic**: What went wrong -> what to do instead.`

<!-- Add corrections below this line -->
- **Data libraries**: Always use polars over pandas. User strongly prefers polars.
- **Package installs**: Always use `uv add` instead of `uv pip install` so `pyproject.toml` stays in sync.
- **Commit messages**: Never include absolute paths or personal PC paths in commit messages.
- **Unicode on Windows**: Avoid special Unicode characters in console output; use ASCII.
- **No silent error swallowing**: Never hide failures with bare `except` or silent `pass` blocks.
- **Module-level instances**: Immutable module-level instances can be acceptable, but hidden mutable globals are not.
- **Skill files are adapters**: Claude-specific skill files are useful operating surfaces, but they are not the architecture source of truth.
- **wikify page names**: Use natural Wikipedia-style titles ("Atomic Layer Deposition", not "concept-atomic-layer-deposition"). The kind field distinguishes page types; the id IS the title.
- **wikify writer**: Pages must be full Wikipedia-style encyclopedic articles, not stubs. Sections are guidance, not strict requirements. No visible `[[wikilinks]]` in prose.
- **wikify person pages**: Person pages are written by the model like article pages. Author metadata (primary publications, citations, coauthors) is assembled at ingest/distill time and attached to the writer's `WriteRequest` as `author_context` for grounding. The writer produces biographical prose in Wikipedia voice; the "appears in this corpus" phrasing is banned. Robust to missing `author_context` for persons mentioned in text but not authors.
- **No dead versioned files**: Always delete the superseded version when you ship the new one. If you're ever tempted to keep the old file "just in case," that's a signal the new one isn't ready or the deprecation needs a tracked migration.
- **Quality review means rendered HTML**: Never declare output "good" based on the intermediate markdown. Open the rendered artifact the user would see. Sample pages across kinds (article, person, edge cases), not just the best-looking one. Compare against the user's implicit reference (Wikipedia for wikis, real papers for papers). Enumerate every failure mode you find. See the "Quality Review Protocol" in the Behavioral Guidelines section above for the full protocol.
- **Quote substring validation**: Uses tolerant normalization (NFKC + dash + brackets + emphasis). Picks verbatim phrases from clean chunks; do not normalize chunk text when selecting quotes.
- **Pipeline error handling**: Per-call `ValidationError` and `QuoteNotInChunkError` are caught and skipped. The run continues; `.error.json` artifacts are left for postmortem.
- **Coverage gap must be stateful**: Strategy experiments require real `coverage_gap` updates and persistence across refine epochs; static coverage scores invalidate comparisons.
- **Mode comparability**: `scripted` and `guided` modes must emit actions through one shared interface with common telemetry fields.
- **Locality of behavior**: Prefer a clear local data table plus one factory over scattered one-line modules, parallel preset/config layers, or `__init__.py` behavior. Classify knobs before adding them: domain/strategy, runtime, mode, or adapter. Runtime choices should be explicit parameters, not fields smuggled into domain config.
- **Docling parser**: Default: formulas ON (granite-docling-258M), OCR off, images_scale=3.0. Converter is cached at module level. `DOCLING_FORMULAS=0` to disable for fast iteration. `DOCLING_OCR=1` for scanned PDFs. Docling strips inline `[N]` citation brackets; `_bracketize_refs` restores them using bibliography entry count as the valid range.
- **Equation-chunk binding**: Default parser uses `char_span` overlap. Docling HybridChunker uses whitespace-normalized text containment (`use_text_match=True`) because HybridChunker char_spans don't match markdown offsets.
- **Citation ordinals**: `CitationEntry.ord` is zero-based from extraction. KG `ord_refs` stores as `cit.ord + 1` (one-based) to match `[N]` markers in text.
- **Embeddings GPU**: `embedding.py` auto-detects CUDA > DirectML > CPU via onnxruntime providers. No config needed.
- **BibTeX author names**: `_clean_author_name` strips affiliation symbols (Oriya, asterisks, daggers, PUA glyphs) and title-cases all-caps/all-lowercase names. Preserves particles (van, de, von) and mixed case (McMaster).
