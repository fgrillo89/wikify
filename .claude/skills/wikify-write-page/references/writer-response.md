# Writer Response Contract

The writer returns strict JSON matching `WriteResponse`.

Required fields:

- `page_id`
- `page_kind`
- `body_markdown`
- `used_markers`
- `tokens_in`
- `tokens_out`

Optional fields:

- `extends_page_id`
- `equations`
- `figures`
- `reasoning`

`body_markdown` is the committed page body candidate. It must include a
lead, topical sections, and a final `## References` block.

Every in-prose `[^eN]` marker must have exactly one matching definition:

```text
[^eN]: <chunk_id> (<doc_id>) > "<verbatim quote>"
```

The writer must not invent chunk ids, doc ids, quotes, figures, or
equations.

If the draft includes `figures`, use them sparingly. A selected figure
must come from the draft figure list and must be represented twice:

- in `figures[]` as
  `{figure_id, path, caption, placement_anchor, source_marker}`;
- in `body_markdown` as `{{figure:<placement_anchor>}}` near the prose
  that discusses it.

Do not select decorative, duplicate, or weakly related images. Most
pages should use zero or one figure; use two only when the subject is
visually or structurally clearer with both.

Before writing `response.json`, self-check the JSON against this field
set. Do not include stale fields from older prompts, such as `links`, or
workflow-only commentary outside the JSON object. If uncertain, inspect
the current draft request and this contract; Python is the schema
authority and rejects extra fields.
