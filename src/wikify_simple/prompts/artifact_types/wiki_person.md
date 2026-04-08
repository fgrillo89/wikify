# Wiki Person Page — Output Template

Use this template for `kind="person"` pages in the wikify_simple wiki.

Person pages are produced **deterministically** from document metadata and
parsed citations by `distill/author_pages.py`. The model never writes them.
This template exists so the renderer, validator, and any future model-backed
person writer share the same expected shape.

## Voice And Stance

- Neutral biographical voice. Third person. Past tense for biographical
  facts, present tense for ongoing affiliation when known.
- No invented biographical detail. The page only contains facts that came
  from the corpus (author byline, affiliation when present, paper titles,
  parsed reference lists).
- No `[[wikilinks]]` anywhere in the body. The crosslink pass handles
  outbound links via frontmatter.

## Required Sections (in this order)

### `## Overview`
One short paragraph naming the person and stating, in plain prose, how they
appear in this corpus (for example: "Smith authored three papers in this
corpus on atomic layer deposition."). No citations required; the supporting
evidence is implicit in the paper list below.

### `## Papers in this corpus`
A bullet list of every document in the corpus that lists this person as an
author. One bullet per paper, formatted as:

`- <Year>. <Title>. <Venue>. ([doc_id](../docs/<doc_id>.md))`

Each bullet must correspond to exactly one `Evidence` row on the page so
that downstream M6 grounding still passes.

### `## Cited works`
A bullet list of works this person is cited as having authored, drawn from
the parsed reference lists of OTHER papers in the corpus. One bullet per
unique cited work. Skip this section if no such citations were parsed.

### `## References`
The visible footnote block, one `[^eN]: <chunk_id> (<doc_id>) > "<quote>"`
line per `Evidence` row attached to the page. At least one definition.

## Hard Minimums

- All required sections (Overview, Papers in this corpus, References)
  present. Cited works is optional.
- The Overview is non-empty.
- Papers in this corpus has at least one bullet.
- References has at least one `[^eN]:` definition.
- No `[[wikilinks]]` anywhere in the body.

## Note On Generation

The fixed pipeline (`distill/author_pages.py`) builds these pages directly
from `Document` and parsed-citation records. No model dispatch occurs for
person pages. If a future slice introduces model-backed enrichment of
person pages, that writer call must still produce output matching this
template.
