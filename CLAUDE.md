# Claude Code -- Working Conventions

Runtime-specific guidance for using this repo through Claude Code.
This file is not the architecture source of truth.

## Communication Style
Be concise and to the point. Make it simple but not simpler.

## Read First
1. `docs/project-status.md` -- current state and priorities
2. `docs/architecture.md` -- architectural boundaries and system model
3. `docs/refactor/wiki-deep-refactor-plan.md` -- active implementation plan
4. `docs/design/wiki-runtime-refactor-plan.md` -- focused wiki runtime design note

## What This Repo Is
Wikify is a local-first corpus platform with two product surfaces:

- `wiki`: the primary knowledge product
- `papers`: a separate writing surface for papers, reviews, and presentations

The wiki is general-purpose. Do not treat it as science-only, even though
scientific corpora remain an important use case.

## Architecture Reminder
The current target boundaries are:

- `core`
- `ingest`
- `wiki`
- `papers`

Important rules:

- `wiki` must not depend on `papers`
- visible wiki files and structured state must stay aligned
- graph metrics and run observability are first-class wiki concerns
- CLI, MCP, and runtime-specific skills are adapters, not architecture

## Claude Code In This Repo
Claude Code is one adapter over the same repo and runtime surfaces described in
the main docs.

Use `.claude/skills/` as Claude-specific operating guidance, but do not treat
those skill files as the canonical product design.

When working in Claude Code:

- prefer the current docs over archived design material
- follow the package boundaries in `docs/architecture.md`
- follow the execution slices in `docs/refactor/wiki-deep-refactor-plan.md`
- keep runtime-specific assumptions out of product docs and core modules

## Claude-Specific Skills
Available skill files under `.claude/skills/` are Claude Code helpers, not the
architecture source of truth.

Examples:

- `/wiki-epoch`
- `/wiki-campaign`
- `/wiki-ask`
- `/wiki-maintain`
- `/generate`
- `/ingest`

These may be useful operating surfaces in Claude Code, but the repo itself must
also remain coherent for other runtimes.

## Working Model
Use the repo as a combination of:

- Python and domain code for ingest, storage, graph logic, wiki operations, and exports
- structured state for retrieval, provenance, graph reasoning, maintenance, and telemetry
- visible wiki files for curated human-facing knowledge

Claude Code may help orchestrate work, but product behavior should not be
defined in Claude-specific terms.

## Preferred Operations
Prefer these workflows over ad hoc file mutation when appropriate:

- `wikify ingest`
- `wikify refresh`
- `wikify wiki epoch`
- `wikify wiki query`
- `wikify wiki maintain`
- `wikify wiki campaign`
- `wikify wiki html`

## Runtime Prompts
These are loaded by code and are not product documentation:

- `src/wikify/prompts/style_guide.md`
- `src/wikify/prompts/artifact_types/`
- `src/wikify/prompts/fields/`

## Python Tooling
- Package manager: `uv`
- Formatting and linting: `uv run ruff format .` and `uv run ruff check --fix .`
- Type checking: `uv run ty`
- Testing: `uv run pytest`

## Code Quality
- Prefer small responsibility-focused modules
- Prefer explicit boundaries and dependency direction
- Prefer dependency injection over hidden mutable globals
- Prefer contracts for real extension points
- Keep graph metric definition separate from orchestration code
- Keep visible wiki files and structured state aligned

## Git Workflow
- Commit and push regularly after meaningful progress

## Output Quality Review Criteria
When scoring or reviewing generated text, evaluate these criteria alongside
organization, citation quality, and style-guide compliance:

| Criterion | What to measure |
|-----------|-----------------|
| Sentence simplicity | Average words per sentence, percent of sentences over 30 words, and first-read parseability |
| Concept density | New concepts introduced per sentence; flag sentences that stack too many unfamiliar terms |
| Relative clause usage | Count `which` and `that` and `who` clauses; flag sentences with nested clauses |
| Subordinate clause frequency | Percent of sentences opening with `although`, `because`, `while`, or `since` |
| Abstract readability | Short clear opening sentence, one concept per sentence, and no unnecessary citations |
| Em-dash violations | Count ` -- `, ` --- `, and Unicode em/en dashes used as parenthetical separators |

## Corrections And Lessons Learned
When the user corrects a mistake or misinterpretation, add an entry below so the
same mistake is less likely to be repeated.

Format:
`- **Topic**: What went wrong -> what to do instead.`

<!-- Add corrections below this line -->
- **Data libraries**: Always use polars over pandas. User strongly prefers polars.
- **Package installs**: Always use `uv add` instead of `uv pip install` so `pyproject.toml` stays in sync.
- **Commit messages**: Never include absolute paths or personal PC paths in commit messages.
- **Vault output location**: The Obsidian vault output goes under `data/vault/`, not at project root.
- **No mock generation scripts**: Do not hardcode paper text in scripts; use the real corpus and runtime surfaces.
- **DOCX templates**: Let template-native styles handle formatting; do not override fonts or spacing programmatically.
- **BibTeX is corpus-level**: `data/library.bib` is generated on ingest and refresh, not per output.
- **Chemistry subscripts**: Apply Unicode subscripts only to markdown output; let DOCX export handle native subscripts.
- **Unicode on Windows**: Avoid special Unicode characters in console output; use ASCII.
- **No silent error swallowing**: Never hide failures with bare `except` or silent `pass` blocks.
- **Module-level instances**: Immutable module-level instances can be acceptable, but hidden mutable globals are not.
- **Performance-critical paths**: Keep an eye on embedding, graph, and retrieval hot paths if responsiveness becomes a problem.
- **Wikipedia pipeline is concept-first**: The wiki is built by discovering concepts from the corpus, not from a pre-planned sitemap.
- **Epoch model for convergence**: Wiki building is iterative, and the current model is documented in `docs/architecture.md` and `docs/design/wiki-runtime-refactor-plan.md`.
- **Model selection in epochs**: Use cheaper models for extraction and early drafting; reserve more capable models for harder synthesis or audit work.
- **Package name**: The Python package is `wikify`; the project root directory still happens to be `C:\dev\scholarforge`.
- **Skill files are adapters**: Claude-specific skill files are useful operating surfaces, but they are not the architecture source of truth.
- **Extraction template evolves**: The extraction template can evolve across epochs, but it must not drift into corpus-specific overfitting.
- **wikify_simple page names**: Use natural Wikipedia-style titles ("Atomic Layer Deposition", not "concept-atomic-layer-deposition"). The kind field distinguishes page types; the id IS the title.
- **wikify_simple writer**: Pages must be full Wikipedia-style encyclopedic articles, not stubs. Sections are guidance, not strict requirements. No visible [[wikilinks]] in prose.
- **wikify_simple person pages**: Author pages are deterministic (no model call) but get enriched when evidence accumulates. Non-author people mentioned in text also get pages.
- **Quote substring validation**: Uses tolerant normalization (NFKC + dash + brackets + emphasis). Picks verbatim phrases from clean chunks; don't normalize chunk text when selecting quotes.
- **Pipeline error handling**: Per-call ValidationError and QuoteNotInChunkError are caught and skipped. The run continues; .error.json artifacts are left for postmortem.
