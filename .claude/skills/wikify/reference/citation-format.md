---
name: wikify/reference/citation-format
description: The [^eN] citation marker and reference-definition format used in all wikify page bodies.
---

# Citation format

Wikify pages cite evidence using numbered footnote markers in the body and
full-chunk-id reference definitions in the References section. This format is
load-bearing: the eval harness (`wikify eval`) resolves every marker to a
corpus chunk by exact string match. Sloppy formatting breaks grounding gates.

## In-prose markers

Cite evidence with `[^eN]` markers, 1-based into the request's evidence list.

```
Atomic layer deposition (ALD) is a self-limiting vapor-phase technique.[^e1]
The growth rate has been measured at 0.9 A/cycle for HfO2 at 250 C.[^e2]
```

Rules:

- `N` is an integer starting at 1.
- `[^eN]` is attached to the end of the sentence it grounds, before the period. `.[^e1]` — note: period THEN marker, or marker THEN period? Convention: marker before the period (`...technique [^e1].`) OR after a comma (`technique,[^e1] ...`). The validator accepts either placement; pick one and stay consistent within a page.
- Every in-prose `[^eN]` must resolve to a matching `[^eN]:` definition in the References section.
- Markers may repeat in prose when the same evidence grounds multiple sentences.

## Reference definitions

The `## References` section at the end of the body MUST use the full-chunk-id
format, and the `chunk_id` MUST be copied VERBATIM from the request's
`evidence[i].chunk_id` field — do not strip the suffix, do not paraphrase.

```
## References

[^e1]: <full_chunk_id> (<doc_id>) > "<exact_quote>"
[^e2]: <full_chunk_id> (<doc_id>) > "<exact_quote>"
```

Example — if `evidence[0]` on the request is:

```json
{
  "chunk_id": "[2008 Strukov] The missing memristor found_b5610c500e6b__c0000__1f0ed598",
  "doc_id": "[2008 Strukov] The missing memristor found_b5610c500e6b",
  "quote": "which he called a memristor"
}
```

Then the reference line is:

```
[^e1]: [2008 Strukov] The missing memristor found_b5610c500e6b__c0000__1f0ed598 ([2008 Strukov] The missing memristor found_b5610c500e6b) > "which he called a memristor"
```

## The `__c####__hex` suffix rule

The chunk_id format is:

```
<doc_id>__c<4-digit-index>__<hex-fingerprint>
```

The `__c####__hex` suffix identifies the exact chunk within the document. The
eval harness (M6 grounding gate) looks up the chunk by full `chunk_id`;
stripping the suffix causes zero resolvable markers and a failing run.

**Common mistake — do not make this one:** writing `[^e1]: <doc_id> > "quote"`
without the chunk index. The reference is then unresolvable.

## Quote substring rule

The quoted text in each reference definition MUST be a verbatim substring of
the source chunk's text. This is enforced by the `QuoteNotInChunkError`
raised during extract, and the equivalent check on writes.

- Copy-paste, do not paraphrase.
- Preserve punctuation and capitalization.
- Do not add ellipses unless they were in the source.
- A substring from 5 to 400 characters is the acceptable range.

## What not to do

- Do not invent markers (`[^e99]`) that the evidence list does not cover.
- Do not leave markers in prose with no matching definition.
- Do not leave definitions with no in-prose reference.
- Do not use bare `[1]`-style citations — the renderer only recognizes `[^eN]`.
- Do not embed citations inside `[[wikilinks]]` or images — wikilinks are banned in body prose (see `write-constraints.md`).

## Validator enforcement

Citation correctness is checked by `src/wikify/schema.py::WriteResponse` via:

- `_body_has_prose_and_evidence` — ensures the References section exists and has at least one definition.
- `_check_wikipedia_structure` — ensures every in-prose marker resolves to a definition and vice versa.

The `QuoteNotInChunkError` path (shared with extract) enforces the substring
rule at promotion time: `wikify validate write` reruns the quote-in-chunk
check before commit.
