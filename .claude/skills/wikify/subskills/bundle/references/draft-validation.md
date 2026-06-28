# Draft And Validation

Drafts are per-attempt writer inputs. Responses are writer outputs.
Validation is the promotion gate.

```bash
wikify draft build <slug> --task create|refine --corpus <corpus> --model-id <id> --tier S|M|L
wikify draft show <slug> [--full]
wikify draft check <slug>
```

`draft build` compiles `draft.json`. `write-page` writes
`response.json`. `draft check` writes `validation.json`.

`draft check` exits non-zero when schema, structure, or quote grounding
fails. Workflows own retry and escalation policy.

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
