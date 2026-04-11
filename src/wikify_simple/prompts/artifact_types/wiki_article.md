# Wiki Article -- Output Template

Use this template when writing a `kind="article"` page for the wikify_simple
wiki. The page is a full encyclopedic article about ONE subject, written in
neutral Wikipedia voice and grounded entirely in the supplied evidence list.

## Lead Section (no heading)

- Open with the subject title in **bold** in the first sentence.
- The first sentence is a one-sentence IS definition: what the subject IS,
  not what it does or why it matters.
- Follow immediately with one or two context sentences expanding on
  significance or scope.
- No bullet points in the lead. Flowing prose only.
- No heading above the lead -- the bold title IS the section marker.

**Example lead (Atomic Layer Deposition):**

> **Atomic layer deposition** (ALD) is a vapor-phase thin-film deposition
> technique that grows material one atomic monolayer at a time through
> sequential, self-limiting surface reactions.[^e1] ALD achieves conformal
> coverage on high-aspect-ratio structures that conventional chemical vapor
> deposition cannot reach, making it indispensable in semiconductor
> fabrication and memristor research.[^e2]

**Example lead (Photocatalysis):**

> **Photocatalysis** is a chemical process in which light activates a
> catalyst to accelerate a reaction that would otherwise proceed slowly or
> not at all.[^e1] The process underpins applications ranging from water
> splitting for hydrogen production to the degradation of organic
> pollutants in wastewater treatment.[^e2]

## Body Sections (required: at least 2 H2 before the appendix group)

Use `## H2` headings. Consecutive, no skipped levels. Single blank line
between sections.

Choose labels that fit the subject. Common sets:

- `## Background` / `## History` -- prior art, motivation, historical context
- `## Mechanism` / `## Process` / `## Theory` -- how it works
- `## Applications` / `## Uses` -- concrete use cases
- `## Specifications` / `## Types` / `## Variants` / `## Characterization` --
  for equipment, taxonomies, or measurement topics

The rule is **at least 2 topical H2 sections that together explain the
subject**. Topic-specific substitutions are permitted. The only forbidden
choice is having zero or one topical section before References.

Every topical section must contain at least one `[^eN]` citation marker.

## Appendix Group (fixed order, after all topical sections)

1. `## See also` -- optional; cross-links to related pages
2. `## References` -- **required, always last**

## References Format

One `[^eN]:` line per cited evidence entry, in citation order:

```
[^eN]: <full_chunk_id> (<doc_id>) > "<exact_quote>"
```

Copy `chunk_id` and `doc_id` verbatim from the supplied evidence list.
Do not strip the `__c####__hex` suffix from chunk_id.

## Hard Minimums (the validator will reject the response otherwise)

- Total body length >= 1200 characters.
- Lead paragraph present (bold title in first sentence, no heading).
- At least 2 `## H2` headings in the body before `## References`.
- At least 3 paragraphs of prose outside the References section.
- At least one `[^eN]` marker in the prose.
- No `[[wikilinks]]` anywhere in the body.
- Final `## References` section with at least one `[^eN]:` definition.
- Every `[^eN]` marker in the prose has a matching definition in References.

## Banned Phrases

Never write any of the following:

- "in this corpus"
- "in this article"
- "as discussed above"
- first-person references to the work ("we examine", "we show", "our analysis")
