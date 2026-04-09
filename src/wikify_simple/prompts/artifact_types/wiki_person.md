# Wiki Person Page -- Output Template

Use this template for `kind="person"` pages in the wikify_simple wiki.
Person pages start as deterministic skeletons built from document metadata
and parsed citations by `distill/author_pages.py`. When enough evidence
accumulates from chunk extraction (the extractor finds this person discussed
substantively in the corpus), the writer enriches the page with model-generated
prose sections.

This person may be a researcher, inventor, executive, historical figure, or
any named individual who is discussed substantively in the corpus. Do not
assume they are an academic.

## Voice And Stance

- Neutral biographical voice, third person, Wikipedia-style. Past tense for
  biographical facts; present tense for ongoing affiliation when known.
- No invented biographical detail. Only facts derivable from the supplied
  evidence list and the deterministic skeleton.
- Do NOT use `[[wikilinks]]` in the model-written prose sections. The
  deterministic sections (Publications, Collaborators) use wikilinks; the
  model-enriched sections must stay clean for the crosslink pass.

## Two-Tier Structure

### Tier 1 -- Deterministic (always present, never rewritten by the model)

These sections are produced by `distill/author_pages.py` from metadata.
When the writer receives a person page, these sections appear in the
`skeleton` field. The writer MUST preserve them verbatim in the output.

- **Publications in this corpus**: bullet list of primary-metadata papers
  formatted as `- {Year}. [[Title]]`
- **Cited works in this corpus**: bullet list of works by this person found
  in reference lists of other corpus papers
- **Collaborators**: bullet list of co-authors as `- [[Name]]`

### Tier 2 -- Model-enriched (only when evidence exists, written by the model)

These sections are written by the model based on the supplied evidence.
They appear BEFORE the Tier 1 sections in the final page.

- **Lead paragraph**: opens with the person's full name in bold. Expands
  beyond the deterministic stub to describe what the corpus says about
  this person's contributions, methodology, and role. Grounded in evidence.
- **Research focus** (or "Professional focus" for non-academics): 2-4
  sentences on the person's primary area of work as described in the
  evidence. Do not fabricate biography.
- **Significance**: what makes this person notable in the context of this
  corpus. 2-3 sentences grounded in evidence.

### Final page layout

```
{Tier 2 lead paragraph}

## Research focus
{Tier 2 content}

## Significance
{Tier 2 content}

## Notable contributions
{from skeleton, if present}

## Publications in this corpus
{from skeleton, if present}

## Cited works in this corpus
{from skeleton, if present}

## Collaborators
{from skeleton, if present}

## References
[^e1]: ...
```

## Evidence Markers

Use `[^eN]` markers in Tier 2 prose to cite the supplied evidence list.
Every factual claim must be grounded. The `## References` section at the
end contains one `[^eN]: <chunk_id> (<doc_id>) > "<quote>"` line per
evidence entry the prose actually cited.

## Hard Minimums (enforced by the validator)

- Total body length >= 1200 characters.
- At least one `## H2` heading in the body.
- At least three paragraphs of prose outside the References section.
- At least one `[^eN]` marker somewhere in the prose.
- No `[[wikilinks]]` in model-written prose.
- Final `## References` section with >= 1 `[^eN]:` definition.
- Every `[^eN]` marker in the prose has a matching definition.
