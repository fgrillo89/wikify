# Draft And Validation

Drafts are per-attempt writer inputs. Responses are writer outputs.
Validation is the promotion gate.

```bash
wikify draft build <slug> --task create|refine --corpus <corpus> --model-id <id> --tier S|M|L
wikify draft show <slug> [--full]
wikify draft check <slug>
```

`draft build` compiles `draft.json`. `wikify-write-page` writes
`response.json`. `draft check` writes `validation.json`.

`draft check` exits non-zero when schema, structure, or quote grounding
fails. Workflows own retry and escalation policy.

Normal repair path after a validation failure:

1. Inspect `validation.json` and the failed `response.json`.
2. If evidence is missing, add or replace evidence through `wikify work`
   and rebuild the draft.
3. If the writer shape is wrong, rewrite `response.json` through
   `wikify-write-page` after its self-check.
4. Re-run `wikify draft check`, then `wikify wiki commit`.

Do not patch committed wiki markdown as the repair path. The gate must
pass from work evidence, draft input, and writer response.
