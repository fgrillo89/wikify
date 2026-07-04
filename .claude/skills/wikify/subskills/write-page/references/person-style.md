# Person Page Style

Use for `kind="person"` pages.

A person page is about the PERSON, not a concept. It leads with who they
are, and organizes the body around what they did and how their work
developed. Do NOT write a concept exposition with the person's name
sprinkled in.

## Lead

Lead with WHO. The first sentence establishes the person, their
role/field, their affiliation when grounded, and their specific
contribution:

```text
**Name** is a [grounded role/field, e.g. "materials scientist"] [at <affiliation> if grounded] known for [their specific contribution].[^e1]
```

When NO grounded role or affiliation exists, still lead person-first:

```text
**Name** is a researcher whose work [established/developed] [contribution].[^e1]
```

Never open with a concept definition. The person page does NOT use the
article "X is a/an" definition-lead.

Do not put a year range in parentheses after the name. The corpus
year-of-first-paper and year-of-last-paper in `author_context.year_range`
are publishing-window dates, not biographical (birth-death) dates;
displaying them parenthetically reads as the latter and is misleading.
Express a working period as a separate grounded sentence ("Active in the
field since the early 2010s.") and only when evidence supports it, never
as a parenthetical year range.

## Sections

Organize the body around the PERSON, not concepts. Sections describe what
the person did and how their work developed over time. Every paragraph
foregrounds the person's role: what they did, when, and with whom. The
concept is context for the contribution, not the subject.

Prefer person-centric section titles:

- `## Key contributions` -- what the person did.
- `## Research trajectory` -- chronological development of their work.
- `## Collaborations` -- who they worked with.
- `## Affiliations and career` -- only when grounded.

Do NOT title sections as bare concept expositions (e.g.
`## Platinum ALD Reaction Mechanisms`, `## Room-Temperature Platinum
ALD`). Such a heading turns the page into a concept dump.

- `## Publications` may be included only when `author_context` supplies
  primary publications.
- Person pages must have at least two non-appendix `## H2` sections
  before `## References`. If `## Publications` is not available, add a
  second grounded person-centric section, only when evidence supports it.

Never write "appears in this corpus" or equivalent corpus
meta-commentary.

## Biographical facts: allow when grounded, never invent

Affiliation, working period, collaborators, career, and education MAY be
stated WHEN a dossier chunk grounds them, each with an `[^eN]` marker.
NEVER invent them. Do not pull affiliation or history from non-citable
author metadata.

Keep: no parenthetical birth-death-looking year range; surface a working
period only as a separate grounded sentence.

## Contribution-only fallback

When the dossier has contribution evidence but NO grounded
identity/affiliation/career chunk, write a clearly person-centric
CONTRIBUTION-ONLY page: a person-first lead plus sections such as
`## Key contributions` and `## Collaborations`, and simply OMIT
affiliation and history. Do NOT invent them and do NOT pull them from
non-citable author metadata. Still at least two non-appendix `## H2`
sections; still person-framed, never a concept dump.

Person pages are figure-free.

## Grounding

Every distinct claim must be backed by nearby evidence. Quote ACTUAL
contributions by the author; author bylines alone do not count. A single
`[^eN]` marker can carry the surrounding sentences in the same
paragraph; anchor specific facts (a named device, a measured
property, a publication, a collaboration) directly. Do not stack
every marker at the end of a paragraph.
