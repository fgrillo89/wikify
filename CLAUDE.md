# Claude Code — Working Conventions

General rules for how Claude should operate in this project.

**Key docs (read in order):**
1. `docs/project-status.md` — current state, what works, remaining work
2. `docs/architecture.md` — module layout, writing pipeline, data flow

**Runtime prompts** (loaded by code, NOT documentation):
- `src/scholarforge/prompts/style_guide.md` — base writing style
- `src/scholarforge/prompts/artifact_types/` — per-document-type rules
- `src/scholarforge/prompts/fields/` — per-field writing guides

**Design docs**: `docs/design/` — architecture decisions, design plans (reference only)

## Agent Usage

- **Always prefer sub-agents** for parallelizable work — launch independent tasks simultaneously
- **Model triaging**: Use haiku for simple tasks (file searches, formatting, simple edits). Use sonnet for moderate tasks (code generation, research). Reserve opus for complex reasoning only.
- Foreground agents only when results are needed before proceeding; background otherwise

## Python Tooling

- **Package manager**: Always use `uv` (never pip/pip-tools)
- **Formatting/linting**: Use `ruff` for all Python files (`uv run ruff format .` and `uv run ruff check --fix .`)
- **Type checking**: Run `uv run ty` on all code after changes
- **Testing**: `uv run pytest`

## Code Quality

- Follow **SOLID principles** where applicable:
  - **S**ingle Responsibility — one reason to change per module/class
  - **O**pen/Closed — extend via new classes, not modifying existing ones
  - **L**iskov Substitution — subtypes must be substitutable for base types
  - **I**nterface Segregation — small, focused interfaces over fat ones
  - **D**ependency Inversion — depend on abstractions, not concretions

## Git Workflow

- **Commit and push regularly**: Commit and push after every meaningful progress — don't batch up changes. This includes code changes, doc updates, and bug fixes. Small frequent commits are better than large infrequent ones.

## Output Quality Review Criteria

When scoring or reviewing generated text (strategy comparisons, quality assessments, etc.), always evaluate these criteria alongside thematic organization, citation density, and style guide compliance:

| Criterion | What to measure |
|-----------|----------------|
| **Sentence simplicity** | Avg words per sentence; % of sentences > 30 words; are sentences parseable on first read? |
| **Concept density** | New concepts introduced per sentence (target: 1). Flag sentences that stack 2+ unfamiliar terms. |
| **Relative clause usage** | Count of which/that/who clauses; flag sentences with 2+ nested clauses. |
| **Subordinate clause frequency** | % of sentences opening with although/because/while/since (target: <20%). |
| **Abstract readability** | First sentence <15 words? One concept per sentence? Zero citations (unless foundational)? |
| **Em-dash violations** | Count of " -- ", " --- ", unicode em/en-dashes used as parenthetical separators (target: 0). |

## Corrections & Lessons Learned

When the user corrects a mistake or misinterpretation, **add an entry below** so the
same mistake is never repeated. Format: `- **Topic**: What went wrong → what to do instead.`

<!-- Add corrections below this line -->
- **Data libraries**: Always use polars over pandas. User strongly prefers polars.
- **Package installs**: Always use `uv add` (not `uv pip install`) to add dependencies. This keeps pyproject.toml in sync.
- **Commit messages**: Never include absolute paths or personal PC paths in commit messages. Use relative paths or project-relative references only.
- **Vault output location**: The Obsidian vault output goes under `data/vault/` (gitignored), NOT at project root. The vault Python *code* lives in `src/scholarforge/vault/` — don't confuse code with output.
- **No mock generation scripts**: Don't hardcode paper text in scripts. Use the MCP server + Claude Code agents to generate real papers from the knowledge base.
- **DOCX templates**: When using a publisher template, clone paragraph exemplars from the template XML. Never override template fonts/spacing programmatically — let the template's native styles handle formatting.
- **BibTeX is corpus-level**: `library.bib` is auto-generated on ingest/refresh at `data/library.bib`, not per-output.
- **Chemistry subscripts**: Apply Unicode subscripts (HfO₂) only to markdown output. DOCX gets raw text (HfO2) and the exporter renders native Word subscripts.
- **Unicode on Windows**: Avoid Unicode arrows/special chars in console print statements. Use ASCII alternatives.
- **No silent error swallowing**: NEVER use bare `except: pass` or `try/except` that hides failures. If something fails, log it or raise it. Silent swallowing is obfuscation by design. If ChromaDB fails, the user needs to know and fix it, not get silently degraded results.
- **Module-level instances are fine, mutable globals are not**: A module-level constant like `_db = DatabaseManager()` (assigned once, never reassigned) is acceptable — same pattern as `settings = Settings()`. The `global` keyword for mutation (`global _foo; _foo = ...`) is banned. Prefer dependency injection (pass the instance as a constructor/function argument) when the instance needs to be swapped in tests.
- **Performance-critical paths**: Consider whether hot paths (embedding, graph computation, chunk retrieval) should eventually be compiled (Rust via PyO3/maturin, or Cython). Python's GIL and startup cost are real bottlenecks for a responsive app.
