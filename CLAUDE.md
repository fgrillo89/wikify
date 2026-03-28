# Claude Code — Working Conventions

General rules for how Claude should operate in this project. Project-specific context
lives in `docs/architecture.md` and `docs/project-status.md`.

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

- **Commit and push after major changes**: Whenever making significant code changes or
  updating `docs/project-status.md`, commit and push immediately. Don't batch up changes.

## Corrections & Lessons Learned

When the user corrects a mistake or misinterpretation, **add an entry below** so the
same mistake is never repeated. Format: `- **Topic**: What went wrong → what to do instead.`

<!-- Add corrections below this line -->
- **Data libraries**: Always use polars over pandas. User strongly prefers polars.
- **Package installs**: Always use `uv add` (not `uv pip install`) to add dependencies. This keeps pyproject.toml in sync.
- **Commit messages**: Never include absolute paths or personal PC paths in commit messages. Use relative paths or project-relative references only.
- **Vault output location**: The Obsidian vault output goes under `data/vault/` (gitignored), NOT at project root. The vault Python *code* lives in `src/scholarforge/vault/` — don't confuse code with output.
