# Writing Schemas

Canonical executable schemas live in Python:

- `src/wikify/schema.py`
- `src/wikify/bundle/draft/schema.py`

Important model-facing artifacts:

- `WriteRequest`: writer input compiled by `wikify draft build`.
- `WriteResponse`: writer output written to `response.json`.
- `ImageRef` / `SelectedFigure`: figure candidates and writer-selected
  figures. Writers may only select figures supplied in `WriteRequest`.
- `EvidenceRecord`: evidence ledger record appended under work state.
- Inbox records: suggestions applied by `work tend`.

Three `WriteEvidenceRef` fields are populated only when `draft build
--with-adjacent` ran: `context_window` carries the flanking chunks as
synthesis context, `context_window_assets` lists the asset kinds
(`equation`, `table`) present there but ABSENT from the cited chunk, and
`promotable_chunk_ids` gives the ids of the neighbours carrying them —
the argument for `work build-evidence --from-ids`, surfaced by `draft
build` as `promotion_candidates[].promote_chunk_ids`.
A non-empty `context_window_assets` means the neighbouring chunk should
be promoted to first-class evidence before writing; it is never a licence
to quote across the boundary. The dossier renders it as a
**NOT CITABLE — promote first** notice.

Schemas are strict. Skills may explain them, but Python validates them.
When prompt or skill examples mention response fields, keep them aligned
with the executable schema. Extra fields are rejected; missing required
fields are rejected. Treat this file and `write-page` references
as guidance, not a copy of the source of truth.
