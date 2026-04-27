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
