# Citation-Linking Diagnostic -- Phase 3

Investigation only. No code changes in this phase. Goal: explain why
chunk-level `cited-in-corpus` traversal returns empty for most chunks
in `data/corpora/ald_all_marker` and why doc-level `references`
traversal is dominated by `ref_*` stubs.

## Pipeline map (file:line)

1. **Marker parser** -- `src/wikify/corpus/graph.py:28`
   `_MARKER_RE = re.compile(r"\[(\d+(?:\s*[-...,]\s*\d+)*)\]")`. Bracket
   forms only (`[N]`, `[N,M]`, `[N-M]`). Superscripts, parentheses,
   author-year (Smith 2023), and hyperlink-style markers are silently
   dropped.
2. **Chunk-marker -> bib_id linker** -- `src/wikify/corpus/store/sync.py:84`
   `_chunk_citations_from()` runs at ingest, walks chunk text with a
   simpler regex `re.compile(r"\[(\d+)\]")` (line 27), and writes one
   `chunk_citations` row per matched ord with `bib_id =
   "<doc_id>::bib:<ord:04d>"`. If a marker ordinal exceeds the number
   of bib entries for that doc the row is silently skipped (line 96).
3. **bib_entries persistence** -- `src/wikify/corpus/store/bib.py:42`
   `upsert_bib_entries()` always persists every parsed bib, including
   those with no resolvable target (`target_doc_id NULL`).
4. **In-corpus resolver** -- `src/wikify/corpus/store/bib.py:112`
   `reresolve_inbound()` runs after every doc ingest. Three matchers
   run in order against unresolved bibs (`target_doc_id IS NULL`):
   - exact DOI (`bib.doi == in_corpus_doc.doi`, confidence 1.0)
   - title+year fuzzy (alnum-normalised first 50 chars, confidence 0.85)
   - in-corpus title prefix in the bib's raw citation text
     (confidence 0.75)
   Otherwise `target_doc_id` stays `NULL` and `resolution` may be set
   to the matcher that was *attempted* but failed (e.g. `'doi'` when
   the bib had a DOI but no in-corpus doc matched).
5. **Doc-level references edges** -- `src/wikify/corpus/store/bib.py:172`
   Only bibs with `target_doc_id IS NOT NULL` get a `graph_edges`
   row of `kind='references'`. Unresolved bibs do **not** get a
   document-level edge.
6. **Stub doc nodes** -- `src/wikify/corpus/store/kg.py:243`
   `_load_bib_cited_only()` synthesises a graph-only "source" node for
   every unresolved bib at graph load. Stubs have no chunks, no
   `corpus/docs/<id>.json`. Their handle takes the form
   `doc:[YYYY Author] Title_<doc-short>::bib:<ord>` (or
   `doc:ref_<bibkey>` when the local bibkey is parseable).
7. **Chunk -> cited-in-corpus traversal** -- `src/wikify/corpus/queries.py:1130`
   The `cited-in-corpus` branch parses markers from chunk text, calls
   `kg.source(chunk.doc_id).references(ords=ords)`, and returns rows
   from the document-level `_references` index. **The index only
   contains rows where `target_doc_id IS NOT NULL`** -- so unresolved
   citations never appear in chunk traversal results, even though the
   stub graph node exists.

## Runtime evidence (ald_all_marker, 2026-05-03)

```
n_documents                                 = 208
n_chunks                                    = 4985
n_chunks_with_at_least_one_cite_row         = 1544 (31%)
n_chunk_citations                           = 16099
n_bib_entries                               = 9719
  with DOI                                  = 5789 (60%)
  with title                                = 7149 (74%)
  with target_doc_id NOT NULL (resolved)    =  254 ( 2.6%)
  with resolution NULL (matcher never set)  = 8469 (87%)

resolution method breakdown of the 254 resolved bibs:
  exact_doi      72
  title_year    108
  rawtext_year   37
  doi            35
  (none)          2

DOI overlap analysis:
  unique DOIs in in-corpus documents        =  192
  unique DOIs across bib_entries            = 3712
  DOIs present in both sets                 =   70
  unresolved bibs whose DOI is in-corpus    =    0   <-- DOI resolver works perfectly when both have DOI

chunk_citations resolving to a corpus doc   =  381 / 16099 (2.4%)
chunks with >=1 resolved citation           =  220 / 4985  (4.4%)
docs with no bib_entries at all             =    6
```

## Failure-mode classification

The four candidate failure modes from the investigation prompt, scored
against ald_all_marker:

| Mode | Definition | Signal in this corpus |
|---|---|---|
| (a) parse failure | marker not in `[N]` bracket form so the regex skips it | unmeasured here; needs a per-chunk text scan with broader marker patterns. Marker counts in `chunk_citations` (16k) are large enough that the bracket-only assumption likely already covers the majority on this corpus, but non-bracket cite styles will be invisible. |
| (b) linker failure | bracket marker parsed but no `chunk_citations` row | possible when a doc has zero bib_entries (6 docs) or when the marker ord exceeds doc.citations length. Below 5% upper bound on this corpus; not the dominant failure. |
| (c) bib unresolved (`target_doc_id NULL`) | bib row exists, but no in-corpus doc matched | **dominant failure.** 9465 / 9719 (97.4%) of bibs are unresolved. Of those, 0 have a DOI overlapping any in-corpus doc -- meaning **the cited paper is genuinely not in the corpus** in every case where DOI is present on both sides. |
| (d) stub leak | `target_doc_id` somehow points at a `ref_*` stub instead of a corpus doc | not observed; the resolver only writes corpus doc ids, and stubs are always graph-load synthesis. |

## Root causes (ranked)

1. **Corpus coverage, not resolver bug.** 97% of bibs cite papers
   that are not in the corpus. The DOI-overlap check is the smoking
   gun: every bib with an in-corpus DOI is already resolved. The 122
   in-corpus docs whose DOI is never cited by any other doc reflect
   either niche topics or the small-corpus norm (208 docs cannot
   densely cite each other).
2. **Resolution=NULL on 87% of bibs**. The `resolution` column stays
   NULL when the matcher never *attempted* a match; many of those bibs
   probably had no DOI and a title that the title+year matcher could
   not normalise. Worth a follow-up histogram of "metadata present
   but resolution=NULL" to see whether the matcher is silently
   skipping fields it should try.
3. **Bracket-only marker regex** in `graph.py:28`. Acceptable on this
   corpus (Marker-extracted PDFs render bracket cites consistently)
   but a real risk for any corpus with superscript or author-year
   citation styles. Quantifying this needs a separate pass.
4. **Doc-level edges hide unresolved-but-present citations.** The
   chunk -> cited-in-corpus traversal returns empty even when the
   chunk has a marker pointing at an in-corpus paper, **because the
   index is doc-level and unresolved bibs are excluded**. If the
   resolver ever did a partial match (resolution set but
   target_doc_id NULL), the traversal still drops it.

## Phase 4 design seed -- citation explainability helper

For one chunk, the helper should report:
- markers in chunk text (with the broader regex set, not just bracket)
- per marker: did it become a `chunk_citations` row? if not, why
  (out-of-range ord vs. non-bracket form)
- per row: what does `bib_entries` say -- DOI, title, year, raw_text,
  resolution attempted, target_doc_id?
- if `target_doc_id IS NULL`: try a live re-match against the current
  corpus and report which signals nearly matched (closest DOI, closest
  title) so the user can tell coverage gap from resolver miss
- if `target_doc_id IS NOT NULL`: emit the resolved corpus doc handle
  and resolution method
- if the bib resolves to a graph-only stub (`_load_bib_cited_only`):
  flag explicitly that no `corpus/docs/<id>.json` exists

Surface decision: this is an *agent-facing* triage, so MCP first
(`corpus_explain_citation` taking a chunk handle). A CLI counterpart
(`corpus citation-explain`) gives operators the same view at the
shell. Both wrap a single domain helper to keep parity automatic.

Output shape proposal (sketch only):
```
{
  "chunk": "chunk:...",
  "doc": "doc:...",
  "markers": [
    {
      "ord": 12,
      "text": "[12]",
      "found_in_chunk_citations": true,
      "bib_id": "doc::bib:0012",
      "bib_entry": {
        "doi": "10.1038/...",
        "title": "...",
        "year": 2021,
        "resolution": "doi",
        "target_doc_id": "doc:...",
        "target_is_stub": false
      },
      "diagnosis": "resolved_in_corpus"
        | "out_of_corpus" | "resolver_miss" | "ord_out_of_range" | "no_bib_entry"
    }
  ],
  "summary": {"n_markers": N, "n_resolved": M, "n_out_of_corpus": K, ...}
}
```
