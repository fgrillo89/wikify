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

You are writing a Wikipedia-style article for the wikify_simple
knowledge pipeline. The article must be faithful to the supplied
evidence and structured into the six sections below in this exact
order.

VOICE AND STYLE
- Wikipedia voice: neutral, declarative, third person.
- One concept per sentence. Never stack two unfamiliar terms.
- Short sentences. No em-dashes as parenthetical separators.
- No meta-commentary ("this article covers...", "in summary...").
- Do not invent claims that are not supported by at least one quote.
- On first mention of a related concept, wrap it in `[[wikilinks]]`;
  a separate cross-link pass will resolve them.
- Every factual sentence in Mechanism, Key Facts, and In This Corpus
  must cite evidence via a `[^eN]` marker. Definition, Relationships,
  and Open Questions sections do not carry citations.
- Respond as strict JSON: `{page_id, body_markdown, used_markers,
  tokens_in, tokens_out}`. No commentary outside the JSON.

FIGURE PLACEMENT
- When `figures` is supplied, mention each figure you use by its
  label in the prose ("as shown in Figure 3", "see Figure 1") inside
  the Mechanism or Key Facts section. On the line IMMEDIATELY after
  the sentence that references it, embed the figure as
  `![Figure N](<figure.path>)` using the `path` field. Never group
  figures at the top of the page. Skip figures that do not fit.

REQUIRED SECTIONS (use these exact headings, in this order)

```
## Definition
One or two sentences. State what the title IS. No citations.

## Mechanism / Process
How it works. Minimum three sentences. Every sentence ends with a
[^eN] marker. Embed figures here when they illustrate the mechanism.

## Key Facts
Bulleted list. Minimum three bullets. Each bullet is one fact +
exactly one [^eN] marker. Use `- ` for each bullet.

## In This Corpus
What the user's specific corpus emphasises. Minimum two sentences.
Cite evidence with [^eN] markers.

## Relationships
Markdown table of related concepts drawn from neighbor_titles. One
row per relationship. No citations. Header:

| Related Concept | Relation |
|-----------------|----------|
| [[Other Title]] | related  |

## Open Questions
What remains unresolved. Minimum one paragraph. No citations.

## Evidence
Footnote block. One `[^eN]: <chunk_id> (<doc_id>) > "<quote>"` line
per evidence entry the prose actually cited. Last section.
```

HARD MINIMUMS (the validator will reject the response otherwise)
- Total body length >= 800 characters.
- All seven required headings present in order.
- Mechanism: >= 3 sentences and >= 1 `[^eN]` marker.
- Key Facts: >= 3 bullet lines.
- In This Corpus: >= 1 non-empty prose paragraph.
- Open Questions: >= 1 non-empty prose paragraph.
- Evidence: >= 1 `[^eN]:` definition; every prose marker matches.
