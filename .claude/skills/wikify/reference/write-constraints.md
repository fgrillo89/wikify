---
name: wikify/reference/write-constraints
description: Wikipedia Manual-of-Style and voice constraints enforced by the write subagent and the WriteResponse validator.
---

# Write constraints

Wikify pages must read like real Wikipedia entries: connected prose, neutral
declarative voice, faithful to supplied evidence. The executable source of
truth for these rules is `src/wikify/schema.py::WriteResponse` and its
`_check_wikipedia_structure`, `_check_figure_mentions`, `_body_has_prose_and_evidence`
validators. This file describes the intent; the Python code enforces.

## Page kinds

- `article` — concepts, methods, materials, devices, theories, metrics. Routed to `articles/`.
- `person` — biographical entries. Routed to `people/`.

`kind` is set at extract time and must not change during write.

## Voice and style (all kinds)

- Wikipedia voice: neutral, declarative, third person.
- Connected prose paragraphs. One concept per sentence.
- Short sentences. No em-dashes as parenthetical separators.
- Do not invent claims not supported by the supplied evidence list.
- No `[[wikilinks]]` anywhere in the body. A separate crosslink pass populates page frontmatter; the body stays clean.
- Cite evidence using `[^eN]` markers (1-based into the evidence list). See `citation-format.md`.

## Article page structure (kind=article)

Per Wikipedia MoS/Layout (https://en.wikipedia.org/wiki/Wikipedia:Manual_of_Style/Layout):

1. **Lead** (no heading). Bold title in the first sentence, one-sentence definition, then context. No bullets.
2. **Body** — at least two topical `## H2` sections before the appendix group. Common labels: `## Background`, `## Mechanism`, `## Process`, `## Theory`, `## Applications`, `## Uses`, `## Specifications`, `## Characterization`, `## Open Questions`. Topic-specific substitutions are allowed; the requirement is **at least two**, not specific names.
3. **Appendix group** in this order: `## See also` (optional), then `## References` (required last).

## Person page structure (kind=person)

Per Wikipedia MoS/Biography (https://en.wikipedia.org/wiki/Wikipedia:Manual_of_Style/Biography):

1. **Lead** (no heading). `**Full Name** (year-range) is a [field] [role] known for [contribution].` For mentioned-only persons (no `author_context`): `**Name** is credited with [specific contribution].[^e1]`.
2. **Body** — at least two `## H2` sections before `## References`. Required: `## Research` or `## Contributions`. Include `## Publications` only when `author_context.primary_publications` is non-empty; format as a blank-line-separated Markdown list.
3. Optional sections when evidence supports: `## Education`, `## Career`, `## Collaborations`, `## Legacy`.
4. `## References` required last.

## Banned phrases (project-wide)

Never write any of these. The validator catches several; prose review catches the rest:

- "in this corpus"
- "appears in this corpus"
- "mentioned in this corpus only through citations"
- "this corpus contains"
- "in this article"
- "as discussed above"
- First-person references to the work: "we examine", "we show", "our analysis"

The corpus-meta phrasings exist for historical reasons and must not leak into
encyclopedic prose. See `feedback_writing_style` user memory for the rationale.

## Figure placement

- When the request carries `figures`, mention each figure by its label ("as shown in Figure 3") inside the relevant section.
- On the line IMMEDIATELY after the sentence that references it, embed the figure as `![Figure N](<figure.path>)`.
- Never group figures at the top. Skip figures that do not fit.
- Prefer figures whose ID appears in `evidence_v2[i].evidence_figures` — these were flagged by the extractor as directly relevant to the cited concept.
- The `figures` array is pre-ranked by relevance; walk from the top and stop once every figure that fits has been placed.
- A figure's `near_chunk_ids` tells you which body chunks discuss it. Prefer the section whose evidence chunks are in this list.

## Structural floor (enforced by validator)

- Body length >= 1200 characters.
- No `[[wikilinks]]` anywhere in the body.
- At least one `## H2` heading.
- For article and person pages: at least two non-appendix `## H2` sections before `## References`. Appendix headings that do not count: `References`, `Notes and References`, `See also`, `Further reading`, `External links` (case-insensitive).
- At least three non-blank paragraphs of prose outside the References section.
- At least one `[^eN]` marker somewhere in the prose.
- A final `## References` section containing at least one `[^eN]:` definition.
- Every `[^eN]` marker in the prose resolves to a matching `[^eN]:` definition in References.
- Every `![Figure N](path)` embed is textually referenced on the immediately preceding non-blank prose line.

## Pre-submit checklist

Before the skill promotes a draft to `pages/`:

1. Body length >= 1200 characters? If short, expand Background/Mechanism with supporting prose.
2. In-prose `[^eN]` markers >= 1.
3. For every in-prose marker, a matching `[^eN]:` definition in References. No orphans.
4. For every `[^eN]:` definition, the chunk_id carries its full `__c####__hex` suffix (copy verbatim from `evidence[i].chunk_id`). See `citation-format.md`.
5. No `[[wikilinks]]` anywhere.
6. At least three paragraphs of prose outside References.
7. Every `![Figure N](path)` has a preceding line mentioning "Figure N".
8. For article and person pages: at least two topical `## H2` sections before `## References`.

## Relationship to `WriteResponse` validators

The checklist above mirrors the Pydantic validators on `WriteResponse`:

- `_body_has_prose_and_evidence` — length, marker presence, References section shape.
- `_check_wikipedia_structure` — H2 count, appendix order, marker-definition resolution.
- `_check_figure_mentions` — figure embed / prose co-reference.

If this file and the validators disagree, the validators win — update this
file, not the validators.
