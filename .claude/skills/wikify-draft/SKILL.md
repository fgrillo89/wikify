---
name: wikify-draft
description: Per-attempt draft IO and validation gate for a Wikify bundle. Use to compile a WriteRequest from a concept's work + evidence (`draft build`), inspect the draft (`draft show`), or validate a writer's WriteResponse (`draft check`).
allowed-tools: Bash(wikify draft *)
---

# wikify-draft

The draft layer carries the per-attempt artifacts the writer agent
produces and the validator inspects: `draft.json` (the
WriteRequest), `response.json` (the writer's WriteResponse),
`validation.json` (the verdict). All three are transient — garbage
collected after a successful `wiki commit`.

## Commands

```
wikify draft build <concept> --task create|refine
                             --corpus <c>
                             --model-id <id>
                             --tier S|M|L
                             [--run <b>] [--format text|json]
wikify draft show  <concept> [--run <b>] [--full] [--format text|json]
wikify draft check <concept> [--run <b>] [--format text|json]
```

`--model-id` and `--tier` are required. Strategy lives in skills,
not Python defaults.

## Inputs

- `<concept>` — concept slug as known by `wikify work`.
- `--corpus <c>` — corpus path (for evidence chunk lookups during
  `build`).
- `--task create|refine` — controls prompt assembly.
- `--model-id` / `--tier` — recorded in the WriteRequest envelope and
  used by cost telemetry.

## Outputs

- `work/concepts/<slug>/draft.json` — WriteRequest with `evidence`
  populated (each entry carries chunk_id, doc_id, quote, full
  chunk_text, and section_type for grounding).
- `work/concepts/<slug>/response.json` — written by the writer
  subagent; `draft check` reads it.
- `work/concepts/<slug>/validation.json` — verdict from the
  Validator: schema check + structural check + verbatim quote
  grounding.
- `draft_created` and `validation_completed` events.

## When to use

- After taking a `work claim` and gathering evidence: build the draft
  for the writer.
- Before invoking `wiki commit`: run `draft check` to confirm
  `validation.json.ok == true`.
- After a writer fails: inspect with `draft show --full` to see the
  request the writer was working from.

## Exit codes

`draft check` exits 1 if the response fails any check (schema,
structural, or quote-grounding). The validator's verdict is recorded
in `validation.json` either way; the exit code is the gate signal.

## Retry / escalation policy

The skill caller decides retry policy. The recommended pattern is in
[escalation.md](../wikify/references/escalation.md): one same-tier
retry, then one escalation to tier L, then mark `failed`.

## References

- [atoms.md](../wikify/references/atoms.md) — `draft build` and
  `draft check` pre/post-conditions.
- [schemas.md](../wikify/references/schemas.md) — WriteRequest /
  WriteResponse envelopes.
- [write-constraints.md](../wikify/references/write-constraints.md) —
  what the structural validators check.
- [citation-format.md](../wikify/references/citation-format.md) — the
  `[^eN]` marker grammar enforced by the grounding check.
- [tiers.md](../wikify/references/tiers.md) — choosing `--tier`.
- [escalation.md](../wikify/references/escalation.md) — when to
  escalate.
