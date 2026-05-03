# Citation resolution — validate and improve the chunk → reference → corpus-doc plumbing

Investigation plan for the low in-corpus citation resolution rate
observed on `data/corpora/ald_all_marker`. Scoped strictly to the
mechanics of linking chunks to references and references to corpus
documents — not graph metrics, not new edge kinds.

## Symptom

On `ald_all_marker` (208 papers, 4985 chunks, 9719 bib entries):

| metric | value |
|---|---|
| bib entries total | 9719 |
| with DOI parsed   | 1086 (11.2%) |
| with title >15 chars | 5973 (61.5%) |
| with year | 9652 (99.3%) |
| resolved to in-corpus doc | 165 (1.7%) |
| resolved via exact_doi | 43 |
| resolved via title_year | 85 |
| resolved via other (e.g. openalex) | 37 |
| corpus docs that have a DOI | 198 / 208 |
| **unresolved bibs whose DOI matches any corpus doc's DOI** | **0** |

The last row is the smoking gun. We extract DOIs from bib raw_text
in 1086 entries, and we have DOIs on 198 corpus papers, but the
two sets have **zero overlap**. Possible causes (need to
investigate):

1. **Citation parser misses DOIs that exist in the raw_text**.
   Regex scope, formatting variants — `doi:`, `https://doi.org/`,
   `DOI:`, trailing punctuation, line wraps, percent-encoding,
   PDF text-extraction artefacts.
2. **Document-side DOI extraction stores a different canonical
   form than the bib side**. Different normalisation for case,
   scheme prefix, trailing slash, version suffix (`.v1`, `.v2`),
   `arxiv:` vs `10.48550/arXiv.xxx`.
3. **Title-based fallback is coarse**. The first-50-character-
   lowercase match is brittle to leading "Article:" / "Original
   article:" prefixes, abbreviated journal names accidentally
   matching, capitalisation differences post-normalisation.
4. **OpenAlex enrichment may be skipping or failing silently**.
   Wave C of the refresh DAG enriches bib metadata with OpenAlex
   data when DOIs are present; if it's broken or disabled by
   default, we're losing a potential source of canonical DOIs.

## Investigation steps

1. **Audit the DOI normalisers.** Compare the document-side path
   (`corpus.store.documents._norm_doi` in the dual-write) against
   the citation-extraction path (`ingest.cite_parse._extract_doi`
   or similar). Both must canonicalise to the same form. Capture
   the normalisation rules in a shared helper if they don't
   already share one. Add a regression test that exercises the
   ten or so ugly forms (URL prefix, percent-encoded, version
   suffix, mixed case, trailing punctuation, ArXiv aliases).

2. **Sample 50 unresolved bib entries with `length(raw_text) >
   200`** from the ALD corpus and grep their raw_text for the
   `10.\d{4}/\S+` pattern by hand. Tally:
   - DOI present and parser extracted it correctly → not the gap
   - DOI present but parser missed it → regex / parser gap
   - DOI present in non-standard form → normalisation gap
   - DOI absent → genuinely unresolved by DOI; falls to title

3. **Sample 50 in-corpus documents and search for their DOI
   string in `bib_entries.raw_text` via SQL LIKE**. If the DOI
   appears but isn't in `bib_entries.doi`, that's a parser gap.
   If it doesn't appear at all, the bib is using a different
   alias (preprint, arXiv, etc.) — check whether OpenAlex
   enrichment would have backfilled the canonical form.

4. **Title-based-fallback audit.** Pull the 5973 bibs with a
   title and the 208 corpus titles, run the current
   `first-50-chars-lowercase` match, and inspect:
   - false negatives (semantically the same paper, different
     character-50 prefix)
   - false positives (different paper accidentally matching;
     should be near-zero given the 50-char window but worth
     checking)
   - normalisation lossiness (Greek letters, em-dashes,
     non-ASCII removed without a replacement strategy)

5. **OpenAlex enrichment health.** Verify Wave C is running on
   real corpus builds, not skipped silently. Add a counter to
   the wave's stats that reports "bibs enriched by openalex /
   bibs attempted". If the wave is gated on a missing API key
   or rate limit, surface it in the corpus check probe.

6. **Author-key fallback is missing.** The current resolver
   uses (DOI) and (title-year). For unresolved bibs with
   author + year, we could try matching on
   (lower(first-author-last-name), year) against
   `documents.authors_json` + `documents.year`. Bound to be
   noisy but a useful tertiary signal; gate on a confidence
   threshold.

## Acceptance metric

Doubling the in-corpus resolution rate from **1.7% to ≥5%** on
`data/corpora/ald_all_marker` without introducing false positives.
Every change to the resolver gated by:
- A regression test on `tests/fixtures/tiny` for the new ugly-DOI
  forms.
- A re-run of the resolver against the existing `ald_all_marker`
  corpus, with before/after counts and a manual spot-check of
  10 newly-resolved bibs to confirm no false positives.

## Why this matters

`citation-walk` and `traverse <chunk> --to cited-in-corpus` are
both gated on in-corpus resolution. Today most ALD corpus chunks
report `0` in-corpus citations because the bibs they reference
exist in the corpus but the resolver doesn't see the DOI overlap.
Better resolution → richer walks → more useful citation lineage
when the agent reads a paragraph and asks "what is this building
on that I can read directly?"

The cosine `similarity-walk` complement is now in place
(`tasks/similarity-walk-spec.md`), so the agent has a fallback
exploration mode that doesn't depend on citation density. But
fixing the resolver is still the single highest-leverage change
for the citation-side path.
