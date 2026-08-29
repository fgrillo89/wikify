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

## Markers Are Positional — Re-derive Them

`[^eN]` resolves to the Nth (1-based) entry of the CURRENT
`draft.json` evidence array, not to whatever `eN` meant on the
committed page. Growing evidence before a refine can drop or reorder
records, which repoints every later marker.

`draft build --task refine` reports `marker_remap`
(`{"moved": {...}, "dropped": [...], "stable": true|false}`). When
`stable` is false, re-derive every marker from the fresh dossier's
**Marker index** table; never copy markers across from the committed
page. `wikify draft check` rejects a marker whose footnote names a
different entry's `chunk_id` as `marker_evidence_mismatch`.

## Output

Return a complete replacement `WriteResponse`, not a diff. The commit
gate promotes whole pages.
