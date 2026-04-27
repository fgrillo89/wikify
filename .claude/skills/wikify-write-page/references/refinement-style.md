# Refinement Style

Use for rewriting an existing committed page from new evidence.

## Goals

- Preserve valid existing coverage.
- Add new supported claims.
- Resolve contradictions explicitly when evidence conflicts.
- Remove stale or unsupported phrasing.
- Keep the final page coherent, not patch-like.

## Inputs To Inspect

- Current committed page.
- New evidence entries.
- Existing evidence markers and references.
- Coverage gaps recorded in work state or query feedback.

## Output

Return a complete replacement `WriteResponse`, not a diff. The commit
gate promotes whole pages.
