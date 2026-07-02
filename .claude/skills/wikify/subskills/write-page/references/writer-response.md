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

Every field is required (no defaults). `figure_id` and `path` come
verbatim from the draft figure candidate. `placement_anchor` is the
short token used by the body placeholder. `caption` is the figure
caption you want shown. `source_marker` is the `[^eN]` marker
(without the `[^` `]` syntax, just `eN`) whose evidence chunk the
figure comes from; the validator rejects empty values and the
renderer appends a citation link to the caption pointing at that
footnote so a reader can jump from the figure to the source quote.

Worked example:

```json
"figures": [
  {
    "figure_id": "doc_abc123/fig_002",
    "path": "images/2024_Author_Title/fig_002.png",
    "caption": "Schematic of the Pt/HfO2/TiN stack with oxygen-vacancy filament.",
    "placement_anchor": "stack-schematic",
    "source_marker": "e4"
  }
]
```

and in `body_markdown`:

```text
The filament forms inside the HfO2 switching layer between the inert
top electrode and the oxygen-getting bottom electrode.[^e4]

{{figure:stack-schematic}}
```

Do not select decorative, duplicate, or weakly related images. An
ARTICLE page SHOULD include figures where the draft's figure candidates
support them, up to `max_article_figures = 4`, at most ONE figure per
distinct source document, and each figure tied to a distinct cited
source/section (its `source_marker` in `used_markers`). Skip when no
candidate is genuinely relevant; never invent one. Person pages stay
figure-free.

Before writing `response.json`, self-check the JSON against this field
set. Do not include stale fields such as `links`, or workflow-only
commentary outside the JSON object. If uncertain, inspect
the current draft request and this contract; Python is the schema
authority and rejects extra fields.

Emit unicode characters directly in all prose fields; JSON output is
UTF-8. Do not emit `\uXXXX` escapes (e.g. write `–` not `–`).
`wikify draft check` rejects any prose containing literal escape
sequences.
