---
name: wikify-write-page
description: Produce a Wikify WriteResponse from supplied page context and evidence. Use when a workflow has selected a concept/page and needs encyclopedic article, person-page, comparison, or refinement prose grounded in evidence. Does not commit pages or decide readiness.
allowed-tools: Bash(wikify draft show *)
---

# wikify-write-page

Use this skill for the model-writing step. The workflow supplies the
page context and decides why the page should be written. This skill
produces `response.json` content that must later pass validation.

## Inputs

- `draft.json` or equivalent workflow-provided `WriteRequest` context.
- Page title, aliases, and page kind.
- Evidence entries with chunk id, doc id, quote, chunk text, and any
  figure/equation context.
- Requested writing mode: article, person, comparison, or refinement.
- Field guide selection from the workflow or detected corpus field.

## Required Style Layers

Always consult:

1. `references/style-guide.md`
2. `../wikify/references/writing/field-guides/generic.md`
3. The detected field guide when the workflow or corpus state identifies
   one with confidence.
4. The page-kind template: `article-style.md`, `person-style.md`, or
   `refinement-style.md`.
5. `references/writer-response.md`

Do not browse all field guides. Use `generic.md` by default and load at
most one additional matching field guide when the field is clear.

## Output

Write strict JSON matching `WriteResponse`. The usual target path is:

```text
work/concepts/<slug>/response.json
```

## Hard Rules

- Ground factual claims in supplied evidence.
- Use `[^eN]` markers and matching `[^eN]:` reference definitions.
- Reference quotes must be verbatim substrings of source chunks.
- No visible `[[wikilinks]]`.
- No corpus meta-commentary.
- No page commit. Validation and commit are `wikify-bundle` operations.

## Optional Substeps

Workflows may choose direct writing or a staged path:

```text
evidence -> write
evidence -> compaction -> write
evidence -> compaction -> editor brief -> write
```

Use `compaction.md` and `editor-brief.md` only when the workflow asks
for those stages.

## References

- `references/writer-response.md`
- `references/style-guide.md`
- `references/article-style.md`
- `references/person-style.md`
- `references/refinement-style.md`
- `references/editor-brief.md`
- `references/compaction.md`
- `../wikify/references/writing/citation-format.md`
- `../wikify/references/writing/write-constraints.md`
