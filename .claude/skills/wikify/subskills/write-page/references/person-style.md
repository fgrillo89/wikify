# Person Page Style

Use for `kind="person"` pages.

## Lead

```text
**Name** is associated with [specific contribution grounded in evidence].[^e1]
```

Do not invent nationality, degrees, affiliations, dates, awards, or
biographical facts.

Do not put a year range in parentheses after the name. The corpus
year-of-first-paper and year-of-last-paper in `author_context.year_range`
are publishing-window dates, not biographical (birth–death) dates;
displaying them parenthetically reads as the latter and is misleading.
If you need to express a working period, do it in a separate sentence
("Active in the field since the early 2010s.") and only when evidence
supports it.

## Sections

- `## Research` or `## Contributions` is normally required.
- `## Publications` may be included only when `author_context` supplies
  primary publications.
- Person pages must have at least two non-appendix `## H2` sections
  before `## References`. If `## Publications` is not available, add a
  second grounded section such as `## Collaborations`,
  `## Research areas`, or `## Influence`, only when evidence supports it.
- Optional sections such as `## Career`, `## Collaborations`, or
  `## Legacy` require direct evidence.

Never write "appears in this corpus" or equivalent corpus
meta-commentary.

## Grounding

Every distinct claim must be backed by nearby evidence. A single
`[^eN]` marker can carry the surrounding sentences in the same
paragraph; anchor specific facts (a named device, a measured
property, a publication, a collaboration) directly. Do not stack
every marker at the end of a paragraph.
