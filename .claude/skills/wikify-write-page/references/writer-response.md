# Writer Response Contract

The writer returns strict JSON matching `WriteResponse`.

Required fields:

- `page_id`
- `body_markdown`
- `used_markers`
- `links`
- `equations`
- token/call metadata when required by the active workflow

`body_markdown` is the committed page body candidate. It must include a
lead, topical sections, and a final `## References` block.

Every in-prose `[^eN]` marker must have exactly one matching definition:

```text
[^eN]: <chunk_id> (<doc_id>) > "<verbatim quote>"
```

The writer must not invent chunk ids, doc ids, quotes, figures,
equations, or links.
