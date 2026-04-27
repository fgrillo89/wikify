# Citation Format

Use `[^eN]` markers in prose. Every marker must have exactly one
matching definition in the final `## References` section:

```text
[^eN]: <chunk_id> (<doc_id>) > "<quote>"
```

`<quote>` must be a verbatim substring of the cited source chunk.
Validation fails if the quote is fabricated or edited.
