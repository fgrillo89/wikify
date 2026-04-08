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

## Required Sections (use these exact headings, in this order)

### `## Definition`
One or two sentences stating what the title IS. No citations. No `[^eN]`
markers.

### `## Background`
Historical context, prior art, and motivation. At least three sentences as
connected prose paragraphs. No bullet lists. At least one `[^eN]` marker.

### `## Mechanism / Process`
How the concept works, how it is applied, and how it manifests. At least
four sentences as connected prose paragraphs. No bullet lists. At least one
`[^eN]` marker. Embed figures here when they illustrate the mechanism.

### `## Applications`
Concrete use cases tied to the corpus. At least three sentences. Bullet
lists ARE allowed in this section when distinct use cases are listed. At
least one `[^eN]` marker.

### `## Open Questions`
What remains unresolved or unanswered. At least one sentence. No citations
required.

### `## References`
The visible numbered citation list. One `[^eN]: <chunk_id> (<doc_id>) >
"<quote>"` line per cited evidence entry. At least one definition. This
section must be last.

## Hard Minimums (the validator will reject the response otherwise)

- Total body length >= 1200 characters.
- All six required headings present in this exact order.
- No `[[wikilinks]]` anywhere in the body.
- Background: >= 3 prose sentences, >= 1 `[^eN]` marker, no bullets.
- Mechanism / Process: >= 4 prose sentences, >= 1 `[^eN]` marker, no bullets.
- Applications: >= 3 sentences, >= 1 `[^eN]` marker.
- Open Questions: >= 1 sentence.
- References: >= 1 `[^eN]:` definition. Every `[^eN]` marker in the prose
  has a matching definition in this block.
