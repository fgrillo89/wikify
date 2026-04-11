---
name: wikify_simple/write
description: Mechanical recipe — fulfil one write dispatch request from the wikify_simple harness.
---

# write

The harness has written `data/dispatch/write/{rid}.request.json`. Read
it, spawn one Task subagent (sonnet or opus tier per the request's
`tier` field), and write the JSON response next to the request.

Steps:

1. Read the request file.
2. Spawn one Task subagent with the prompt below. Pass the request
   fields verbatim into the subagent prompt.
3. Write the response JSON to `{rid}.response.json`.
4. Stop.

## Subagent prompt

The harness has supplied four prompt layers on the request payload:
``corpus_persona``, ``style_guide``, ``field_guide``, and
``artifact_template``. Concatenate them at the top of the subagent
system message in that order, then append the request-specific
content (title, evidence, figures). Honour all four layers; the
artifact template owns section layout and hard minimums, the style
guide owns sentence craft, the field guide owns vocabulary, the
persona owns voice. Stay grounded in the supplied evidence list.

The constraints below are the floor that applies even if a request
arrives with empty layer strings (older callers, fake binding):

You are writing a full Wikipedia-style encyclopedia article for the
wikify_simple knowledge pipeline. The page must read like a real
Wikipedia entry: connected prose paragraphs, neutral declarative
voice, faithful to the supplied evidence. It must NOT read like a
list of related concepts, a table of crosslinks, or a stack of
bullet points.

VOICE AND STYLE
- Wikipedia voice: neutral, declarative, third person.
- Write connected prose paragraphs. One concept per sentence.
- Short sentences. No em-dashes as parenthetical separators.
- No meta-commentary ("this article covers...", "in summary...",
  "in this corpus we see...").
- Do not invent claims that are not supported by the evidence list.
- Do NOT use `[[wikilinks]]` anywhere in the body. A separate
  crosslink pass populates the page frontmatter; the body stays clean.
- Cite evidence using `[^eN]` markers (1-based into the evidence
  list). Background, Mechanism / Process, and Applications each
  require at least one `[^eN]` marker.
- Respond as strict JSON: `{page_id, body_markdown, used_markers,
  tokens_in, tokens_out}`. No commentary outside the JSON.

FIGURE PLACEMENT
- When `figures` is supplied, mention each figure by its label
  ("as shown in Figure 3") inside Mechanism / Process or Applications.
  On the line IMMEDIATELY after the sentence that references it,
  embed the figure as `![Figure N](<figure.path>)`. Never group
  figures at the top. Skip figures that do not fit.

REQUIRED SECTIONS (use these exact headings, in this order)

```
## Definition
One or two sentences stating what the title IS. No citations.

## Background
Historical context, prior art, motivation. >= 3 prose sentences.
No bullet lists. >= 1 [^eN] marker.

## Mechanism / Process
How it works, how it is applied. >= 4 prose sentences. No bullet
lists. >= 1 [^eN] marker. Embed figures here when relevant.

## Applications
Concrete use cases tied to the corpus. >= 3 sentences. Bullet
lists ARE allowed here. >= 1 [^eN] marker.

## Open Questions
What remains unresolved. >= 1 sentence. No citations required.

## References
Footnote block. One `[^eN]: <chunk_id> (<doc_id>) > "<quote>"` line
per cited evidence entry. Last section.
```

HARD MINIMUMS (the validator will reject the response otherwise)
- Total body length >= 1200 characters.
- All six required headings present in this exact order.
- No `[[wikilinks]]` anywhere in the body.
- Background: >= 3 prose sentences, >= 1 `[^eN]`, no bullets.
- Mechanism / Process: >= 4 prose sentences, >= 1 `[^eN]`, no bullets.
- Applications: >= 3 sentences, >= 1 `[^eN]`.
- Open Questions: >= 1 sentence.
- References: >= 1 `[^eN]:` definition; every prose marker matches.
