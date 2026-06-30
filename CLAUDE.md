# Wikify

Wikify turns a folder of source documents (PDF / DOCX / PPTX / HTML /
Markdown) into an evidence-grounded wiki: encyclopedic pages where every
claim resolves to a verbatim quote in its source. An agent runtime drives
the workflow by reading skill markdown, calling deterministic `wikify`
CLI / MCP tools, and dispatching model-calling subagents; Python never
calls a model SDK directly. The corpus is authoritative evidence;
committed wiki pages are the human-facing output.

## References

- `docs/README.md` — documentation tree, rooted at `docs/overview.md`
  (concepts + the agent loop) and branching to architecture, the on-disk
  bundle contract, rendering, and metrics.
- Entry-point skills under `.claude/skills/` encode workflow strategy
  (loop shape, stopping criteria, budget, model tier):
  - `wikify` — the iterative editor/explorer wiki builder.
  - `query` — answer from the committed wiki, falling back to corpus search.
  - `arxiv` — acquire arXiv papers and stage them for a build.
  - `ingest` — parse local documents into a corpus (owns parser-backend
    choice: docling default, lite).
- `.claude/skills/wikify/subskills/reference/` — agent-facing reference:
  schemas, CLI grammar, citation format, write constraints, and
  exploration patterns. A new strategy is a new workflow skill, not new
  Python.

## Python Tooling

- Package manager: `uv`. Always use `uv add`, not `uv pip install`.
- Lint: `uv run ruff check src/wikify tests/wikify`
- Tests: `uv run pytest tests/wikify -q`

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
- Every changed line should trace directly to the request.

## Blast Radius Discipline

Before shipping ANY non-trivial change:

1. **Enumerate every caller and consumer.** Grep across `src/`, `tests/`, `.claude/skills/`. Do not guess — verify.
2. **Amend every caller in the same commit.** A signature change that leaves callers broken is a bug.
3. **Delete orphans in the same commit.** Remove imports, variables, and functions your change made unused.
4. **Sweep path-dependent leftovers.** After renames or refactors, grep active code, tests, prompts, docs, and skills for old names, transitional `v1`/`v2`/`legacy` wording, and stale schema fields. Do not claim cleanup until the scan is clean or every remaining hit is explicitly justified.
5. **Name the blast radius in the commit body.** "Touches X, Y, Z; no other callers."

## Conventions

- **Data libraries**: Use polars over pandas.
- **Commit messages**: Never include absolute or personal PC paths.
- **Console output on Windows**: Avoid non-ASCII; use ASCII.
- **No meta-references in shipped code or docstrings.** Never write "per `plan.md`", "Phase 1 ships X", "in this phase", "see the MCP plan", or any pointer to plan / todo / temp docs inside source comments, docstrings, or skill docs. Describe what the code IS and DOES. Internal "phase 1/phase 2" labels that describe an algorithm's stages are fine — they describe code, not project planning.
