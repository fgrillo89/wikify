# Wiki Concept Article — Output Template

Use this template when writing a `kind="concept"` page for the wikify_simple
wiki. The page is an encyclopedic article about ONE concept, written in
neutral Wikipedia voice and grounded entirely in the supplied evidence list.

## Voice And Stance

- Wikipedia voice: neutral, declarative, third person.
- Connected prose paragraphs. One concept per sentence.
- Short sentences mixed with longer ones. No em-dashes as parenthetical
  separators (use commas or parentheses).
- No meta-commentary ("this article covers...", "as discussed above...",
  "in this corpus we observe..."). Begin with content.
- Do not invent claims that the supplied evidence does not support. If the
  evidence does not say it, do not write it.
- Do NOT use `[[wikilinks]]` anywhere in the body. A separate crosslink pass
  populates the page frontmatter and the renderer underlines matched terms
  post-render. The markdown body itself stays clean.

## Citation Style

- Cite evidence using `[^eN]` markers, where `N` is the 1-based index into
  the supplied evidence list.
- Background, Mechanism / Process, and Applications each require at least
  one `[^eN]` marker.
- Definition and Open Questions do not require citations.
- The visible footnote definitions live in the trailing References section,
  one line per evidence entry the prose actually cited.

## Figure Placement

- When `figures` are supplied, mention each figure you use by its label in
  prose ("as shown in Figure 3", "see Figure 1") inside the Mechanism /
  Process or Applications section.
- On the line IMMEDIATELY after the sentence that references it, embed the
  figure as `![Figure N](<figure.path>)` using the supplied `path` field.
- Never group figures at the top of the page. You may skip figures that do
  not fit the prose.

## Sections (guidance, not strict requirements)

Different concepts need different shapes. A page about a piece of equipment
might have `## Specifications`. A page about a phenomenon might have
`## Characterization`. A person page might have `## Biography`. Pick the
sections that fit the concept.

### Recommended sections for a typical concept page

- `## Definition` — one or two sentences stating what the title IS.
- `## Background` — historical context, prior art, and motivation.
- `## Mechanism / Process` — how the concept works and manifests. A natural
  place to embed figures when they illustrate the mechanism.
- `## Applications` — concrete use cases tied to the corpus. Bullet lists
  are allowed when distinct use cases are listed.
- `## Open Questions` — what remains unresolved or unanswered.

Equipment pages typically drop Mechanism in favour of Specifications and
Operation. Phenomenon pages may drop Applications entirely. Use your
judgement.

### `## References` (required, must be last)

The visible numbered citation list. One `[^eN]: <chunk_id> (<doc_id>) >
"<quote>"` line per cited evidence entry. At least one definition.

## Hard Minimums (the validator will reject the response otherwise)

- Total body length >= 1200 characters.
- At least one `## H2` heading in the body.
- At least three paragraphs of prose outside the References section.
- At least one `[^eN]` marker in the prose.
- No `[[wikilinks]]` anywhere in the body.
- Final `## References` section with at least one `[^eN]:` definition.
- Every `[^eN]` marker in the prose has a matching definition in
  References.
