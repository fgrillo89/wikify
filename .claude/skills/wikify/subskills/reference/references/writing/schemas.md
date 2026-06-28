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

Schemas are strict. Skills may explain them, but Python validates them.
When prompt or skill examples mention response fields, keep them aligned
with the executable schema. Extra fields are rejected; missing required
fields are rejected. Treat this file and `write-page` references
as guidance, not a copy of the source of truth.
