## Current Focus

`wikify` is the active track for strategy science. The core question is strategy quality vs token cost vs wall-clock time, comparing:

- `scripted` mode: scripted exploration and budget allocation
- `guided` mode: model-driven exploration and budget allocation

All comparisons must run under the same pipeline contract and telemetry.

## Workflow Orchestration

**1. Plan Mode Default**
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately - don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

**2. Subagent Strategy**
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One tack per subagent for focused execution

**3. Self-Improvement Loop**
- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

**4. Verification Before Done**
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

**5. Demand Elegance (Balanced)**
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes - don't over-engineer
- Challenge your own work before presenting it

**6. Autonomous Bug Fixing**
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests - then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management

1. **Plan First**: Write plan to `tasks/todo.md` with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review section to `tasks/todo.md`
6. **Capture Lessons**: Update `tasks/lessons.md` after corrections

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
- **No dead versioning.** The filesystem IS the version, git history IS the changelog. Delete superseded files; do not leave `foo_v1.yaml` next to `foo_v2.yaml`.

## Surgical Changes

When editing existing code:

- Do not "improve" adjacent code, comments, or formatting.
- Do not refactor things that are not broken.
- Match existing style, even if you would do it differently.
- If you notice pre-existing, unrelated dead code, mention it. Do not delete it.
- Every changed line should trace directly to the user's request.

## Blast Radius Discipline

Before shipping ANY non-trivial change:

1. **Enumerate every caller and consumer.** Grep across `src/`, `tests/`, `.claude/skills/`. Do not guess — verify.
2. **Amend every caller in the same commit.** A signature change that leaves callers broken is a bug.
3. **Delete orphans in the same commit.** Remove imports, variables, and functions your change made unused.
4. **Name the blast radius in the commit body.** "Touches X, Y, Z; no other callers."

## Python Tooling

- Package manager: `uv`. Always use `uv add`, not `uv pip install`.
- Lint: `uv run ruff check src/wikify tests/wikify`
- Tests: `uv run pytest tests/wikify -q`

## Corrections And Lessons Learned

After ANY correction, add an entry here AND to `tasks/lessons.md`.

Format: `- **Topic**: What went wrong → what to do instead.`

- **Data libraries**: Use polars over pandas.
- **Commit messages**: Never include absolute or personal PC paths.
- **Unicode on Windows**: Avoid non-ASCII in console output; use ASCII.
- **wikify page names**: Use natural Wikipedia-style titles ("Atomic Layer Deposition", not "concept-atomic-layer-deposition"). The `kind` field distinguishes page types; the `id` IS the title.
- **wikify writer**: Pages must be full Wikipedia-style encyclopedic articles, not stubs. Sections are guidance, not strict requirements. No visible `[[wikilinks]]` in prose.
- **wikify person pages**: Person pages are written by the model like article pages. Author metadata is assembled at ingest/distill time and attached as `author_context`. The "appears in this corpus" phrasing is banned. Must be robust to missing `author_context`.
