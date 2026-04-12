# Wiki Person Page -- Output Template

Use this template for `kind="person"` pages in the wikify wiki.
Every person page is written entirely by the model from the supplied
evidence and `author_context`. There is no deterministic skeleton to preserve.

## Lead Section (no heading)

**Full author lead pattern** (use when `author_context` is present):

> `**Full Name** (year-range) is a [field] [role] known for [contribution
> grounded in evidence].`

The year-range is the span of publication years in `author_context`
(`first_year`--`last_year`). Omit the range if neither year is available.
Omit nationality when unknown. Use present tense for active researchers;
past tense for deceased persons.

**Mentioned-person lead pattern** (use when `author_context` is None or
the person is not a corpus author):

> `**Name** is credited with [specific contribution grounded in evidence].[^e1]`

Keep the lead short and factual. Never invent biography not derivable from
the evidence.

**Example (full author):**

> **Bhaswar Chakrabarti** (2015--2019) is a materials scientist known for
> investigating switching uniformity and endurance in HfO2-based
> memristors.[^e1]

**Example (mentioned person):**

> **Leon Chua** is credited with predicting the memristor as the fourth
> fundamental circuit element in 1971.[^e1]

## Body Sections (required: at least 2 H2 before the appendix group)

### Required sections

- `## Research` or `## Contributions` -- primary area of work, grounded
  in evidence quotes. 2-5 paragraphs. Required.

- `## Publications` -- list of primary publications from
  `author_context.primary_publications`. Format each as:

  ```
  - {Year}. {Title}. {Venue}.
  ```

  Blank line between entries so Markdown renders a real `<ul>`.
  Omit this section entirely when `author_context` is None or
  `author_context.primary_publications` is empty.

### Optional sections (include only when evidence supports)

- `## Education` -- degrees, institutions, advisors
- `## Career` -- positions, affiliations, timeline
- `## Collaborations` -- co-authors and collaborative projects
- `## Legacy` -- influence, citations, recognition

## Appendix Group (fixed order)

1. `## References` -- **required, always last**

## References Format

One `[^eN]:` line per cited evidence entry:

```
[^eN]: <full_chunk_id> (<doc_id>) > "<exact_quote>"
```

Copy `chunk_id` and `doc_id` verbatim from the supplied evidence list.

## Hard Minimums (the validator will reject the response otherwise)

- Total body length >= 1200 characters.
- Lead paragraph present (bold name in first sentence, no heading).
- At least 2 `## H2` headings in the body before `## References`.
- At least 3 paragraphs of prose outside the References section.
- At least one `[^eN]` marker in the prose.
- No `[[wikilinks]]` anywhere in the body.
- Final `## References` section with at least one `[^eN]:` definition.
- Every `[^eN]` marker in the prose has a matching definition in References.

## Robustness to Missing Context

When `author_context` is None (a mentioned-but-not-authored person), the
page degrades gracefully: use the mentioned-person lead pattern, write a
short biographical sketch from evidence quotes only, and omit Publications.
The page must still meet the hard minimums above.

## Banned Phrases

Never write any of the following:

- "appears in this corpus"
- "mentioned in this corpus only through citations"
- "in this corpus"
- "this corpus contains"
- "in this article"
- "as discussed above"
- first-person references to the work ("we examine", "we show", "our analysis")
