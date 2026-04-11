# Claude Code - Working Conventions

Runtime-specific guidance for using this repo through Claude Code.
This file is not the architecture source of truth.

## Current Focus

`wikify_simple` is the active track for strategy science.
The core question is strategy quality vs token cost vs wall-clock time:

- rules-driven exploration and budget allocation (`rule_policy`)
- model-driven exploration and budget allocation (`llm_policy`)

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
- **No dead versioning.** When iterating on a file (prompt, schema, template, plan), delete the old version and keep the new one under the canonical name. Do NOT leave `foo_v1.yaml` sitting next to `foo_v2.yaml` as a fallback "just in case." Do NOT rename the file by appending a version suffix — the file system IS the version, git history IS the changelog. The only acceptable version-suffixed files are those where the OLD version is still actively reachable from production code during a real migration, and in that case the migration must be on a tracked task with a deadline.

### Surgical Changes
When editing existing code:
- Do not "improve" adjacent code, comments, or formatting.
- Do not refactor things that are not broken.
- Match existing style, even if you would do it differently.
- If you notice unrelated dead code, mention it. Do not delete it.
- Remove imports/variables/functions that YOUR changes made unused.
- Do not remove pre-existing dead code unless asked.
- Every changed line should trace directly to the user's request.

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

If a test-run playbook exists (`docs/refactor/test-run-playbook.md` when the wikify_simple version lands), follow it step by step. Do not improvise the review.

## Read First

For `wikify_simple` work, read in this order:

1. `src/wikify_simple/architecture.md`
2. `src/wikify_simple/strategies.md`
3. `src/wikify_simple/metrics.md`
4. `src/wikify_simple/runbook.md`
5. `src/wikify_simple/test-run-playbook.md` (required before any test run)
6. `src/wikify_simple/plans/structural-improvements.md` (the current structural roadmap — phases landed vs pending)
7. `docs/architecture.md` (repo-wide boundaries)

If a task explicitly touches legacy `src/wikify/*`, then also read:

1. `docs/project-status.md`
2. `docs/architecture.md`
3. `docs/refactor/wiki-deep-refactor-plan.md`

## Wikify Simple Ground Rules

- Product artifact is the wiki bundle on disk.
- Corpus is authoritative evidence; pages are authoritative human-facing outputs.
- Structured state supports retrieval, provenance, graph reasoning, and telemetry.
- Strategy comparisons are only valid when telemetry and action interfaces are shared.
- Iteration is first-class: `create`, `refine`, `merge`.
- Run and provenance history are append-only.
- Coverage memory persists across epochs where refine semantics require it.

## Runtime Neutrality

Keep product architecture runtime-neutral:

- `agents/*` defines contracts and schemas.
- `distill/*` owns business logic and strategy behavior.
- `bindings/*` are adapters only.
- `.claude/skills/*` are execution helpers, not architecture truth.

No product logic should depend on one runtime vendor.

## Preferred Operations

Use `wikify_simple` CLI workflows instead of ad hoc file mutation:

- `uv run python -m wikify_simple.cli ingest ...`
- `uv run python -m wikify_simple.cli distill --strategy {E|M|X} --binding ... --policy ... --iteration ...`
- `uv run python -m wikify_simple.cli distill --phase extract|write|all ...`
- `uv run python -m wikify_simple.cli eval --bundle ... --corpus ...`
- `uv run python -m wikify_simple.cli query --bundle ... "question"`
- `uv run python -m wikify_simple.cli html --bundle ...`

## Prompt and Schema Rules

- Runtime prompt selection for writer should use `write/v2` when available.
- Keep `write/v1` fallback behavior intact.
- Staged `.response.json` files must validate against the matching schema
  (`ExtractResponse`, `WriteResponse`, etc.) before consuming them.
- Validation failures should produce explicit `.error.json` artifacts.

## Python Tooling

- Package manager: `uv`
- Lint (focused): `uv run ruff check src/wikify_simple tests/wikify_simple`
- Tests (focused): `uv run pytest tests/wikify_simple -q`
- Full tests when needed: `uv run pytest -q`

## Code Quality

- Prefer small, responsibility-focused modules.
- Prefer explicit boundaries and dependency direction.
- Prefer constructor injection over hidden mutable globals.
- Use protocols only for real extension points.
- Keep sampling, policy, and metric logic testable without live model calls.
- Keep scale-sensitive paths near-linear where possible (sampler and crosslink hot paths).

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
- **wikify_simple page names**: Use natural Wikipedia-style titles ("Atomic Layer Deposition", not "concept-atomic-layer-deposition"). The kind field distinguishes page types; the id IS the title.
- **wikify_simple writer**: Pages must be full Wikipedia-style encyclopedic articles, not stubs. Sections are guidance, not strict requirements. No visible `[[wikilinks]]` in prose.
- **wikify_simple person pages**: Person pages are written by the model like article pages. Author metadata (primary publications, citations, coauthors) is assembled at ingest/distill time and attached to the writer's `WriteRequest` as `author_context` for grounding. The writer produces biographical prose in Wikipedia voice; the "appears in this corpus" phrasing is banned. Robust to missing `author_context` for persons mentioned in text but not authors. (This supersedes the older deterministic `build_author_pages` behaviour, which is being retired in Phase 6B of `src/wikify_simple/plans/structural-improvements.md`.)
- **No dead versioned files**: The prompt registry once had `extract_v1.yaml`/`extract_v2.yaml`/`write_v1.yaml`/`write_v2.yaml` living side by side — v1 was a silent fallback path and nobody ever went back to clean up. Always delete the superseded version when you ship the new one. If you're ever tempted to keep the old file "just in case," that's a signal the new one isn't ready or the deprecation needs a tracked migration.
- **Quality review means rendered HTML**: Never declare output "good" based on the intermediate markdown. Open the rendered artifact the user would see. Sample pages across kinds (article, person, edge cases), not just the best-looking one. Compare against the user's implicit reference (Wikipedia for wikis, real papers for papers). Enumerate every failure mode you find. See the "Quality Review Protocol" in the Behavioral Guidelines section above for the full protocol.
- **Quote substring validation**: Uses tolerant normalization (NFKC + dash + brackets + emphasis). Picks verbatim phrases from clean chunks; do not normalize chunk text when selecting quotes.
- **Pipeline error handling**: Per-call `ValidationError` and `QuoteNotInChunkError` are caught and skipped. The run continues; `.error.json` artifacts are left for postmortem.
- **Coverage gap must be stateful**: Strategy experiments require real `coverage_gap` updates and persistence across refine epochs; static coverage scores invalidate comparisons.
- **Policy comparability**: `rule_policy` and `llm_policy` must emit actions through one shared interface with common telemetry fields.
