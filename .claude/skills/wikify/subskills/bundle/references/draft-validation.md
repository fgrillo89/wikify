# Draft And Validation

Drafts are per-attempt writer inputs. Responses are writer outputs.
Validation is the promotion gate.

```bash
wikify draft build <slug> --task create|refine --corpus <corpus> --model-id <id> --tier S|M|L [--with-adjacent]
wikify draft show <slug> [--full]
wikify draft check <slug> [--dry-run]
```

`draft build` compiles `draft.json`. `write-page` writes
`response.json`. `draft check` writes `validation.json`.

`draft check` exits non-zero when schema, structure, or quote grounding
fails. Workflows own retry and escalation policy.

## What `draft build` reports

Beyond `draft_path` / `evidence_count`, the JSON envelope carries two
fields a caller must act on:

- `promotion_candidates` (key always present; non-empty only under
  `--with-adjacent`): evidence records
  whose ADJACENT window holds an equation or table the cited chunk merely
  refers to, as `{marker, chunk_id, assets, promote_chunk_ids}`. `chunk_id`
  is the ALREADY-CITED chunk; `promote_chunk_ids` are the neighbours to feed
  `work build-evidence --from-ids`. Retrieval ranks the prose
  that DESCRIBES a result above the formula that STATES it, so the
  headline number routinely lands one chunk over — readable, but the
  citation rule forbids quoting it. Promote those neighbours to
  first-class evidence and rebuild BEFORE dispatching a writer, or the
  page ships with the theory and without the numbers. The dossier prints
  a **NOT CITABLE — promote first** notice on the same records.
- `marker_remap` (only on `--task refine`): `{moved, dropped, stable,
  source}`. `[^eN]` is positional, so a rebuild that reorders or drops
  evidence repoints every later marker. `source` is `prior_draft` when an
  uncommitted attempt was still on disk, `committed_page` when the
  baseline came from the committed page's marker bindings (the usual
  refine case — `wiki commit` garbage-collects `draft.json`), or `none`
  when there was no baseline at all. When `stable` is false, re-derive
  the page's markers from the fresh dossier rather than carrying them
  over.

## What `draft check` reports

`errors` gate the commit; `warnings` do not, but they are still the
writer's to fix. `structural_checks` carries a per-check boolean.

- `marker_evidence_mismatch` (error): a `[^eN]:` definition names a chunk
  belonging to a different evidence entry than the marker's index selects.
  `draft normalize-references` raises on the same condition, because it
  rewrites definitions FROM the marker index and would otherwise launder a
  mis-numbered marker into a well-formed citation to the wrong paper.
- `undelimited_math` (warning, `structural_checks.math_delimited`): counts
  mathematical symbols left outside a `$...$` / `\(...\)` region.
- `evidence_underuse` (warning, `structural_checks.evidence_coverage`): the
  page cites only a narrow slice of the documents it was handed.

Normal repair path after a validation failure:

1. Inspect `validation.json` and the failed `response.json`.
2. If evidence is missing, add or replace evidence through `wikify work`
   and rebuild the draft.
3. If the writer shape is wrong, rewrite `response.json` through
   `write-page` after its self-check.
4. Re-run `wikify draft check`, then `wikify wiki commit`.

Do not patch committed wiki markdown as the repair path. The gate must
pass from work evidence, draft input, and writer response.

## Finalize (composite commit chain)

`draft finalize` runs the per-page commit chain in order:
normalize-references -> check -> `wiki commit` -> release claim. It
short-circuits on the first failure and names the failing step in the
JSON envelope, so a caller can resume from that step.

```bash
wikify draft finalize <slug> --run <bundle> [--owner <o>] [--dry-run]
```

Finalize is a one-shot. A successful commit garbage-collects
`draft.json`, `response.json`, and `validation.json`. A second
`finalize` on the same slug fails the step-0 existence check and returns
`draft_not_found` at the `normalize-references` step. On a re-run that
error means the page was already committed, not that the draft was never
built; confirm with `wiki show <slug>` before rebuilding the draft.
