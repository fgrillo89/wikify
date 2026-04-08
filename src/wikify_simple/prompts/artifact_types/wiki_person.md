# Wiki Person Page — Output Template

Use this template for `kind="person"` pages in the wikify_simple wiki.
Person pages are produced **deterministically** from document metadata and
parsed citations by `distill/author_pages.py`. The model never writes them.
This file documents the expected shape so the renderer, validator, and any
future model-backed writer share the same target. All sections are **soft
guidance**: the deterministic builder omits any section whose data source
is empty.

## Voice And Stance

- Neutral biographical voice, third person, Wikipedia-style. Past tense for
  biographical facts; present tense for ongoing affiliation when known.
- No invented biographical detail. Only facts derivable from the corpus
  (author bylines, paper titles, parsed reference lists, co-authorship).
- `[[wikilinks]]` ARE allowed here (unlike concept pages): person pages are
  index/hub pages whose job is to point at papers and collaborators.

## Frontmatter

Standard wiki frontmatter plus `tags: [author]` (legacy Obsidian convention
carried over so old vault queries keep working).

## Sections (soft, emitted when data exists)

### Lead paragraph
One short paragraph. Opens with the full name in bold, states the primary
field hint inferred from the author's paper titles (most-common technical
noun phrase), the number of papers they contributed to the corpus, the
year range, and an anchor paper (most notable / earliest).

Example: "**Leon Chua** is associated with *memristive systems* in this
corpus, contributing 1 paper from 1971, notably *Memristor — The Missing
Circuit Element*."

### `## Notable contributions`
Bullet list, one per primary-metadata paper. Each bullet is a wikilink to
the paper page with a one-line summary drawn deterministically from the
paper's abstract/tldr/title when available.

### `## Publications in this corpus`
Bullet list of primary-metadata papers, formatted as:
`- {Year}. [[<page_id>]]`

### `## Cited works in this corpus`
Bullet list of works authored by this person that were parsed from the
reference lists of OTHER corpus papers, formatted as:
`- {Year}. *{title}* (cited in: [[<citing_page_id>]])`

### `## Collaborators`
Bullet list of other authors who appear on at least one shared
primary-metadata paper with this person. Each bullet is a wikilink to the
collaborator's author page: `- [[<Collaborator Name>]]`. Omit this section
entirely if the author appears alone on every paper.

### `## References`
Obsidian renders automatic backlinks, so no manual section is needed here.
The page's own `Evidence` footnotes are written below by the page renderer.

## Hard Minimums (enforced)

- Body length >= 200 characters.
- Lead paragraph contains the author's name.
- At least one of the sections {Notable contributions, Publications in
  this corpus, Cited works in this corpus} is present.

## Note On Generation

The fixed pipeline (`distill/author_pages.py`) builds these pages directly
from `Document` metadata and parsed citations. No model dispatch occurs.
When the builder runs on an existing vault, it merges the new paper links
with those already present in the on-disk author file so re-runs are
append-only across corpus ingests.
