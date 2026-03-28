# Claude Code — Working Conventions

General rules for how Claude should operate in this project. Project-specific context
lives in `docs/architecture.md` and `docs/project-status.md`.

## Agent Usage

- **Always prefer sub-agents** for parallelizable work — launch independent tasks simultaneously
- **Use cheaper models** (haiku/sonnet) for sub-agents when possible; restrict their context to only what they need
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
