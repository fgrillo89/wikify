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
- `reasoning`

`body_markdown` is the committed page body candidate. It must include a
lead, topical sections, and a final `## References` block.

Every in-prose `[^eN]` marker must have exactly one matching definition:

```text
[^eN]: <chunk_id> (<doc_id>) > "<verbatim quote>"
```

The writer must not invent chunk ids, doc ids, quotes, figures, or
equations.

Before writing `response.json`, self-check the JSON against this field
set. Do not include stale fields from older prompts, such as `links`, or
workflow-only commentary outside the JSON object. If uncertain, inspect
the current draft request and this contract; Python is the schema
authority and rejects extra fields.
