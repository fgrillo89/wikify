# MCP Autoresearch Audit -- Wikify Corpus Surface

Corpus: `data/corpora/ald_all_marker` (208 docs, 4985 chunks, jina-v2-small-en
512-dim vectors, sqlite store ok). Field detection: materials_science. Run
date: 2026-05-03. Live MCP calls only; CLI used solely for directory listing.

## Phase 1 Status -- MCP Surface Fixes (commit 9f81293)

The MCP surface gaps flagged below have been wrapped or enriched. The
remaining bottleneck is corpus-quality (citation linking, chunk
artifacts, ranker section-blindness), not interface-shape. Per-section
before/after notes are inlined as `**[After phase 1]**` annotations.

Verified live against this corpus on 2026-05-03 after the patch:

- chunk-find rows now carry non-blank `preview` and `meta.section_path`
  (4/4 hits on a hafnium-oxide query).
- `corpus_similarity_walk` exists; `cross_doc_only=True` produces
  zero same-doc edges (8 chunks / 6 edges on a 2-seed depth-1 walk).
- `corpus_find(in_doc=...)` accepts any handle form; bad handle returns
  `bad_in_doc`.
- `corpus_sample` rows carry `meta.year` and `meta.n_chunks` (4/4 on
  `strategy=diverse`, `max_docs=4`).
- `references` traversal flags graph-only stubs with `meta.is_stub=true`:
  `doc:e05ec999cc43` -> 20/20 flagged stub; `doc:a9837bff462d` ->
  18/20 stub, 2 real corpus docs surfaced unflagged.
- `chunk_citations` traversal still empty on most chunks; the existing
  `WIKIFY_QUIET=0` stderr hint fires (`"chunk has 1 citation marker(s)
  [12] but none resolved to in-corpus refs"`). The wrapper is correct;
  the bib resolver is the bottleneck.

What did **not** move: the parsing, chunking, citation resolution, and
ranker section-blindness items remain. These are the corpus-quality
phase-3 targets.

## Executive Summary

The MCP corpus surface is usable but not yet ready as the *primary*
exploration substrate for writing strategies. Three classes of issues
dominate. (1) `corpus_find` is missing first-class triage fields (preview and
section path are blank for chunk hits, forcing one extra `corpus_show` per
result). (2) Citation traversal is functionally one-sided: chunk-level
`cited-in-corpus` returns empty even when bracketed cite markers are visible
in the chunk text, and doc-level `references` is dominated by `ref_*` stubs
(citation-count = 0, pagerank = 0, no chunks) with no real second-hop
material. (3) Chunking and parsing artifacts contaminate top results --
acknowledgments, "Articles You May Be Interested In" sidebars, multi-section
reference dumps, and identical-text caption duplicates are all surfaced as
plausible evidence chunks. A writing strategy on top of this corpus today
must spend most of its tokens on filtering, not synthesis. Bridge search
(`by=paper` semantic) and title-grep (`field=title`) are the two patterns
that consistently work; everything else needs at least one workaround.

## Best Performing Patterns

Ranked by signal-to-noise on the workflows tested.

1. **Paper-level bridge search** --
   `corpus_find(query="A B", by="paper", top_k=N)`. Returns ranked docs with
   `n_match_chunks`, `best_chunk_handle`, `best_chunk_section`, `score`, and
   citation/pagerank ranks. Use whenever you want to find papers connecting
   two concepts; this is the single best primitive in the surface today.
2. **Title-anchored paper search** --
   `corpus_find(query="HfO2", by="paper", field="title")`. Case-insensitive
   substring, returns `title_match_offset`. Use when you know what should be
   in the title (a material, technique, device class).
3. **Doc-level structure preview** -- `corpus_show(handle="doc:<short>")`
   returns `meta.sections` with section_path, n_chunks, ord_range. Cheaper
   than reading text first; lets the agent decide whether to fetch
   `mode="full"` or filter to `sections=["..."]`.
4. **Chunk full-text fetch** --
   `corpus_show(handle="chunk:<id>", full=true)`. Reliable, returns
   section_path and full text. Use after triage to ground a quote.
5. **Diverse seed sampling** --
   `corpus_sample(strategy="diverse", max_docs=N)` with default
   pagerank_weight=0.7. Returns *most-cited cluster* + a coverage tail.
   Acceptable as a starting point but heavily overlaps top-pagerank docs
   (60%+ overlap on this corpus). Use only with a low pagerank_weight if
   you actually want diversity.

## Recursive Traversal Findings

### Citation traversal

- `corpus_traverse(chunk_handle, to="cited-in-corpus")` returned an empty
  list for every chunk tested, including `chunk:54fce16c` (a paper RESULTS
  section with twelve `[N]` markers in plain text) and `chunk:183d4e08`
  (containing `[124]`, `[134]`, `[135]`). Either the chunk-citation linker
  did not run on these chunks or none of the bracketed targets resolved to
  in-corpus papers. Either way, **chunk-level citation walks are unusable
  as an agent primitive on this corpus.**
- `corpus_traverse(doc_handle, to="references")` does work but is dominated
  by `ref_*` placeholder docs (`citation_count=0`, `pagerank=0`, no chunks).
  For `doc:e05ec999cc43` (the AS-ALD HfO2 RRAM paper), 15/15 returned
  references were stubs -- no second hop available. For
  `doc:a9837bff462d` (the Zhang thesis), only 2/10 were real corpus docs;
  the rest were stubs or `bib:NNNN` strings.
- Doc-handle namespace for stubs is awkward: e.g.
  `doc:[2024 Zhang] Chalcogenide...::bib:0202`. Spaces, brackets, and double
  colons in a handle are hostile to URL/CLI carrying.
- Net: a 2-hop citation walk almost always dead-ends at hop 1 in this
  corpus. The traversal *primitive* is wired correctly, but the corpus's
  citation resolver has insufficient corpus-internal coverage to make
  recursion useful.

### Similarity traversal

- **MCP does not wrap an existing similarity primitive.** The backend has
  `queries.similarity_walk(..., cross_doc_only=True)`
  (`src/wikify/corpus/queries.py:1816`) and the CLI exposes it as
  `corpus similarity-walk` with `--cross-doc-only/--include-same-doc`
  (`src/wikify/cli/corpus.py:1234`). MCP `corpus_schema` lists only
  citation/structural relations and does not surface similarity. The
  fastest fix is to wrap the existing function as a new MCP tool, not to
  add similarity flags onto `corpus_find`.
  **[After phase 1]** Wrapped as `corpus_similarity_walk`. Verified on
  this corpus: 2-seed depth-1 walk with `cross_doc_only=True` produced
  6 edges across 8 chunks with zero same-doc edges. SCHEMA advertises
  the tool under `walks.similarity_walk` with the full param list.
- Single-hop semantic find returns useful neighbours but suffers from
  **same-doc saturation**: the seed doc occupies 3-of-8 to 4-of-8 top
  results. The MCP `corpus_find` schema does not pass through any
  exclude-doc or cross-doc filter, so agents over-fetch and filter
  client-side and waste tokens.
- Cross-doc neighbors at one hop drift moderately (AS-ALD HfO2 RRAM ->
  Al-doped HfO2 memristor -> defect-engineered HfO2 memristor). At two
  hops drift compounds: the chain ended on a "RRAM neuromorphic AI
  outlook" review chunk (`chunk:528592be`) -- broadly related, narrowly
  irrelevant.
- **Caption chunks rank alongside substantive chunks.**
  `chunk:98c135a9ce26/Figure_02__caption` (text:
  `"Fig. 2 | SiO2-based selector, memristor and 1S1R device."` -- 56
  characters) appeared at score 0.798 in a top-8 list. The figure-caption
  embedding is dragging on lexical overlap with high-frequency device
  terms.
- **Hash-collision warning is real.** A single response listed
  `chunk:d127e3bd` twice with two different `doc_handle`s. The handle
  resolves silently; ground-truthing a citation through this handle alone
  would attribute it to whichever doc happens to come first.

## Concept Bridge Findings

Tested pairs: ALD+memristor, GPC+temperature, HfO2+resistive switching,
precursor chemistry+purge time, area-selective+inhibitor.

- `by="paper"` + free-form combined query is the cleanest pattern. Returns
  papers ranked by how strongly the conjunction is represented across
  chunks, with `n_match_chunks` and `best_chunk_section` to triage.
- `text=true` is literal substring grep by design. Single phrases like
  `"purge time"` work; conjunctions like `"area-selective inhibitor"` or
  `"precursor purge time"` return zero because the literal string is not
  present. **It is not a token-AND bridge primitive** and should not be
  used as one. The gap is that no token-AND mode exists -- not that text
  mode is wrong.
- `by="paper"` with `text=true` populates `score=0.0` for every result.
  Ranking is implicitly by `n_match_chunks` but the field is silent. This
  is opaque.
- **MCP does not expose existing scoped within-doc search.** The backend
  `queries.find` accepts an `in_doc` parameter
  (`src/wikify/corpus/queries.py:398, 1693`) and the CLI passes
  `--in-doc` (`src/wikify/cli/corpus.py:456`). MCP `corpus_find` does not
  expose it, so agents resort to traverse-then-show-then-filter. The fix
  is small: add `in_doc` to the MCP tool schema and forward it to
  `queries.find`.
  **[After phase 1]** `corpus_find` now accepts `in_doc` (full id, hex
  short, or `doc:` handle). Bad handle returns `bad_in_doc`. SCHEMA's
  `find_modes` advertises `--in-doc <doc-handle>`.

## Sampling Findings

- Only `strategy="diverse"` is implemented (schema confirms). With default
  `pagerank_weight=0.7`, sampled docs overlap 6/10 with top-pagerank -- the
  sample is essentially "the high-PR cluster + a small tail".
- Sample meta is `null` everywhere -- no abstract preview, no section
  count, no field tag, no year. Agents must do a per-doc `corpus_show` to
  decide whether each seed is worth pursuing. For a 10-doc sample that's
  10 extra round-trips before any real strategy choice.
- Author rankings (`by="author", rank="h_index"`) saturate the cap at
  h_index=2 -- a flat plateau because only in-corpus citations count and the
  corpus has few real cross-doc cites preserved. This metric isn't usable
  as a "find the dominant authors" signal here.

## Query Mode Findings

- **Semantic** (default) is the right mode for concept queries, paraphrases,
  and acronyms. It tolerates "RRAM" vs "resistive random-access memory"
  reasonably (different top hits, but both lists are useful and on-topic).
- **`text=true`** is grep over chunk text. Useful for exact strings (chemical
  formulas with subscripts, exact phrasings, tokens that embeddings garble),
  but:
  - Multi-token strings must be literal substrings (no implicit AND).
  - Returns dominate by reference lists when the query is a common acronym
    (`text=true RRAM` -> top-3 are "REFERENCES AND NOTES" chunks).
  - Returns chunks with `score=null` -- no rank signal at all.
- **`field="title"`** is precise and clean: returns docs with
  `title_match_offset`, case-insensitive. Best for material/device queries.
- **Acronym handling**: there is no synonym/expansion layer. "RRAM" and
  "resistive random-access memory" produce different top-N lists; agents
  who care about recall must run both and union.
- **Chemical formulas**: query-time tokenization mangles `HfO2` vs
  `HfO\n2` (literal newline-2 in some titles) -- see Interface Friction.

## Chunking / Parsing Quality Issues

Prioritized list with handles. Cause column is the most likely subsystem.

| Severity | Handle / class | Symptom | Likely cause |
|---|---|---|---|
| **High** | `chunk:8e06e6c4` (doc:b7b24f6cbafa) | One chunk holds 32 numbered references with section_path `["CONCLUSION", "METHODS", "REFERENCES AND NOTES"]` (three concatenated parents). | Chunker not splitting at section boundaries; section-path concatenates siblings. |
| **High** | `chunk:380eb7a2` (doc:3ce604c2ba54) | "Articles You May Be Interested In" publisher sidebar (URLs + dates + "15 March 2026 12:11:38" download timestamp) ingested as a content chunk under section `**Articles You May Be Interested In**`. | Marker/Docling not stripping AIP article-recommendation widgets. |
| **High** | `chunk:e9873a2a` (doc:89c02a99f1e5) | Section path `["- **401**"]` (a page number promoted to section header). Chunk also embeds Wiley copyright/download notice (`28369106, 2025, 3, Downloaded from https://onlinelibrary.wiley.com/...`) inline with prose, plus a markdown table. | Chunker mistakes page footers for headers; boilerplate stripping incomplete. |
| **High** | `chunk:d127e3bd` collision | Same chunk handle returned twice in one find result with two different `doc_handle`s (boilerplate or caption text identical across docs). | Hash-derived chunk handles collide on duplicate text -- schema documents this but the surface still returns ambiguous results without warning. |
| **Med-High** | `chunk:8f2513fde16a/Figure_10__caption` and `chunk:8f2513fde16a/Figure_10_2__caption` | Two distinct handles for the *same* figure caption text in the same doc. | Caption deduper not running; double-extracted from layout. |
| **Med-High** | `chunk:470eb224` (doc:e05ec999cc43) | Acknowledgments paragraph ranked top hit for "atomic layer deposition definition" because the funding sentence contains the phrase. | No section-aware downweighting (acknowledgments / references / disclosure should not rank for content queries). |
| **Med-High** | `chunk:98c135a9ce26/Figure_02__caption` | 56-char figure caption ("Fig. 2 \| SiO2-based selector...") ranking inside top-8 chunk results at score 0.798. | Captions embedded in the same vector space as body chunks; no length floor or caption tag in ranker. |
| **Med** | doc:0e762154c31c sections | Section path `["...", "I. INTRODUCTION", "II. MEMRISTIVE DEVICE FABRICATION"]` -- sibling sections nested under each other. Every later H1 inherits "I. INTRODUCTION" as parent. | Heading-level parser flat-stacks instead of resolving levels. |
| **Med** | doc:b4ca2c2967a9 sections | Whole doc has one section bucket `"body"`. No abstract / intro / methods detected on a 2010 docx. `corpus_show meta` does not include the promised `abstract` field. | Section detector failing on .docx layout; abstract extraction missing or silently absent. |
| **Med** | Title rendering | Titles contain literal `\n` ("Improving linearity by introducing Al in HfO\n2\nas a memristor synapse device"), HTML tags (`HfO<sup>x</sup>`), `<span id="page-0-24"></span>` anchors, and trailing backslashes (`"Sungjun Kim \"`). | Marker output not normalised before persisting to title field. |
| **Med** | Bracket-cite vs unit confusion | `chunk:1833dba0`: "TaN bottom electrode (BE) bar (width [20] um)" -- `[20]` is a citation marker but reads as a dimension. `2 x [20] um pit` similarly. | Marker output keeps citation markers in flow even when adjacent to numerals/units; downstream regexes will misread. |
| **Med** | Reference-citation linking | `cited-in-corpus` empty on probed chunks despite visible bracketed markers. | Either the chunk-citation linker did not run, or its bracket-marker -> reference-list resolver didn't match. |
| **Low** | Author handles | `author:sungjun_kim` has title `"Sungjun Kim \"` (trailing backslash, probably from ORCID/affiliation marker). | Author normaliser not stripping markup. |

## Interface Friction

These are concrete shape problems. Severity = how often the agent has to
work around them.

1. **`corpus_find(by="chunk")` returns blank `preview` and `title`** for
   every result. The agent cannot triage without a per-result `corpus_show`
   round-trip. The same field is populated when `corpus_show` is called on
   the same handle, so it is not a data availability issue -- the surface is
   just choosing not to populate it. **Highest-impact friction in the
   whole API.**
   **[After phase 1]** Resolved. Chunk-find rows now carry a 240-char
   `preview` populated from chunk text (one batch SQL fetch per call).
2. **`section_path` not in chunk-find results.** `by="paper"` results carry
   `best_chunk_section`; `by="chunk"` results do not. The agent has no idea
   whether a top hit is intro, methods, or references unless they
   `corpus_show`.
   **[After phase 1]** Resolved. `meta.section_path` populated on every
   chunk-find row. Same enrichment also applies to walk results.
3. **MCP does not wrap existing `similarity_walk`.** The function and its
   `cross_doc_only` flag exist in the backend and CLI; only the MCP
   surface is missing.
   **[After phase 1]** Resolved. `corpus_similarity_walk` tool wraps
   the domain function; `cross_doc_only` honoured (verified live).
4. **MCP `corpus_find` does not pass through existing `in_doc`.** Backend
   accepts it, CLI exposes `--in-doc`, MCP schema does not list it.
   **[After phase 1]** Resolved. `in_doc` accepted; bad handles return
   `bad_in_doc`.
5. **Chunk handles collide on identical text** (documented in schema, but
   surface returns the collision silently; no warning, no disambiguation).
   **[After phase 1]** Resolved. `format_chunk_handles` escalates
   colliding short hashes to `chunk:<doc-short>/<chunk-short>` in
   find/walk result rows; `resource_uri` is also rewritten to the full
   chunk id when the slash form fires (so resource dereferencing stays
   unique). Walk seeds and edges share the rewritten map.
6. **`text=true` is literal substring grep with no token-AND.** This is
   working as designed for single phrases, but there is no token-AND mode
   anywhere on the surface, so multi-token bridges fall through.
7. **`text=true` + `by="paper"` returns `score=0.0` for every result.**
   Ranking is by `n_match_chunks` but unstated.
8. **`corpus_sample` rows have `meta=null`** (no year, no chunk count, no
   abstract). `corpus_find(by="paper")` rows do carry `n_match_chunks`,
   `best_chunk_handle`, and `best_chunk_section`, but still lack `year`
   and an abstract preview that `corpus_show` would give. Two distinct
   gaps; sampling is the bigger one.
   **[After phase 1]** Resolved. `corpus_sample` and `corpus_find(by=
   paper)` rows now carry `meta.year`, `meta.n_chunks` (real document
   count, distinct from `n_match_chunks`), and `meta.abstract_preview`
   when available. Verified on this corpus (4/4 sample rows enriched).
9. **`corpus_show(doc, sections=["Abstract"])`** returns
   `"section filter matched no sections; available: body"` without echoing
   whether the doc *has* an abstract. Schema docstring says abstract is
   always carried -- it is not for many docs in this corpus.
10. **`ref_*` stub doc handles** are the majority of `references`
    traversal results. They have `pagerank=0`, `citation_count=0`, no
    chunks. They should be flagged as stubs (e.g. `meta.is_stub=true` or a
    separate edge type) so the agent can decide whether to follow.
    **[After phase 1]** Resolved on the surface. Doc-typed traversal
    rows now set `meta.is_stub=true` when the document has no chunks
    (or has no `corpus/docs/<id>.json` payload). Verified live:
    `doc:e05ec999cc43` references -> 20/20 flagged stub;
    `doc:a9837bff462d` -> 18/20 stub, 2 real corpus docs unflagged.
    The underlying *coverage* gap (most refs are stubs) is the
    citation-link bottleneck and is a phase-3 target, not a surface
    fix.
11. **HTML / markdown / OCR markup leaks** into `title` and `section_path`
    fields. Anchors (`<span id="page-0-24"></span>`), `<sup>`/`<sub>`,
    literal `\n` characters, page-number "headings", and trailing
    backslashes all need normalisation before reaching the agent.
12. **No latency or token cost telemetry** in any response envelope. The
    audit had to estimate. For a strategy-cost study an MCP response
    should include at least `elapsed_ms` and (where applicable) result
    counts before truncation.

## Suggested MCP Additions Or Consolidations

Each is a concrete proposal with user story, gap, and risk.

1. **Populate `preview` and `section_path` on chunk-find results.**
   - User story: "I want to skim 8 chunks for relevance without 8 extra
     calls."
   - Gap: blank fields force per-result `corpus_show`.
   - Risk: low. Same data is already produced for `corpus_show`. Cap the
     preview length; truncate at sentence boundary.

2. **Wrap the existing `similarity_walk` as a new MCP tool.**
   - User story: "Find semantic neighbours of this chunk that are not in
     the same paper, recursively."
   - Gap: backend has `queries.similarity_walk(..., cross_doc_only=True)`
     and CLI has `corpus similarity-walk`; MCP does not wrap them. Adding
     `exclude_doc` to `corpus_find` is a weaker substitute.
   - Risk: low. Forwarding-only wrapper.

3. **Add `in_doc` to MCP `corpus_find`.**
   - User story: "Inside paper X, where is concept Y discussed?"
   - Gap: backend `queries.find` already accepts `in_doc` and the CLI
     accepts `--in-doc`; the MCP tool schema does not list the parameter.
   - Risk: low. Schema field + passthrough.

4. **Expose a `bridge(A, B)` primitive.**
   - User story: "Show me the chunks/papers that simultaneously discuss A
     and B, ranked by joint coverage, with both A and B preview snippets
     side by side."
   - Gap: today the agent embeds "A B" together and trusts the model to
     do the conjunction.
   - Risk: medium. The simplest form (intersect top-K(A) intersect top-K(B) at
     paper level, score by min(score_A, score_B)) is implementable in a
     few lines but you must agree what "joint" means.

5. **Mark stubs vs full docs in traversal results.**
   - User story: "When I follow `references`, hide papers that have no
     chunks unless I explicitly ask for them."
   - Gap: agents waste calls trying to show stub docs.
   - Risk: low. Add `meta.is_stub=true` (or filter via
     `min_chunks=1`).

6. **Filter or tag boilerplate / reference / acknowledgment chunks.**
   - User story: "When I search for content, do not surface
     acknowledgments, journal sidebars, or pure-references chunks."
   - Gap: today these chunks dominate certain queries (e.g. acronym text
     mode) and contaminate top-N.
   - Risk: medium. Heuristic tagging at ingest is preferable to
     query-time filtering. Tag `kind` at chunk level
     (`body|abstract|references|acknowledgments|caption|figure|sidebar`)
     and add an `exclude_kinds=[...]` parameter on `corpus_find`.

7. **Use the existing slash-disambiguating chunk-handle helper in MCP
   result shaping.**
   - User story: "Two papers contain the same boilerplate sentence; I
     need to know which one I'm looking at."
   - Gap: hash-derived handles collide silently. The codebase already has
     `format_chunk_handles` and `resolve_chunk_id` in
     `src/wikify/corpus/handles.py` that emit
     `chunk:<doc-short>/<chunk-short>` when the bare hash is ambiguous;
     MCP result shaping currently uses plain `format_handle`. Switch the
     chunk-row formatter to `format_chunk_handles` so collisions become
     visible without inventing a new grammar.
   - Risk: low. Same helper; same handle resolver.

8. **Normalise `title` and `section_path` at ingest.**
   - User story: every downstream consumer wants clean titles.
   - Gap: HTML tags, anchors, literal `\n`, trailing backslashes leak.
   - Risk: low. Single pass before persist.

9. **Add `elapsed_ms` to MCP envelopes.**
   - User story: "I'm running a cost study; how long does each call take?"
   - Gap: caller must measure externally.
   - Risk: low.

10. **Better find-by-author/find-by-paper meta.** Return `year`,
    `n_chunks`, and (when present) abstract preview in `meta` for paper
    rows in `corpus_find` and `corpus_sample`. Today these arrive only via
    `corpus_show`.

## Strategy Recommendations

For writing strategies on this corpus today:

- **Baseline (scripted) writing**:
  - Use `corpus_sample(strategy="diverse", max_docs=...)` only as a
    *coarse* seed -- follow each seed with `corpus_show` to extract
    abstract-equivalent signal before deciding to expand.
  - For each section of the target page, run `corpus_find(by="paper",
    query=<section concept>)` and pick from `best_chunk_handle` directly;
    skip chunk-mode unless the agent will tolerate per-result `corpus_show`
    triage.
  - Filter out hits whose `best_chunk_section` matches
    `acknowledgments|references|articles you may be interested in` after
    casefolding.
  - Avoid following chunk-level `cited-in-corpus` traversals on this
    corpus; they will return empty.

- **Guided (model-driven) writing**:
  - Start from a `by="paper"` semantic query for the target page title.
  - For two-concept evidence (e.g. "ALD + memristor"), the conjunction
    query at `by="paper"` is the strongest single primitive.
  - When the model wants similarity neighbours, expect roughly 30-50% of
    top-N to come from the seed doc; instruct it to dedupe on
    `doc_handle` and re-pick.
  - Treat any chunk whose section_path contains
    `references|acknowledgments|articles|page` as untrusted prose for
    quotation.

- **Query (Q&A from committed wiki)**:
  - Falling back from wiki to corpus is feasible via paper-level bridge
    search. Any bound corpus carries a topic concentration; "applications
    of X" or "uses of X" queries will return whatever framing the corpus
    over-represents. Strategies should detect concentration (e.g. via
    sample diversity vs. top-pagerank overlap) and either widen the seed
    pool or warn the caller before producing breadth claims.

- **Refine (post-feedback rewrite)**:
  - When refining a page, prefer explicit doc anchors (page-level handles)
    over chunk anchors until chunk-handle collision and section_path
    parsing are fixed. Carry `doc_handle` alongside any `chunk_handle` for
    safe rebinding.

## Appendix -- Tested Queries

Latency was not exposed by the surface; "noted" rows had perceptible delay,
others returned promptly.

| # | Query / call | Tool sequence | Result quality | Notes |
|---|---|---|---|---|
| 1 | "growth per cycle self-limiting surface reaction ALD" | find by=chunk | thesis-dominated; tertiary text top hit | preview blank; section_path absent |
| 2 | show chunk:183d4e08 | show chunk full | clean text, real section_path | from a thesis (paraphrased description) |
| 3 | show doc:a9837bff462d | show doc | TOC sections detected as sections (each H1 became its own bucket) | section index inflated |
| 4 | traverse chunk:183d4e08 cited-in-corpus | traverse | empty | despite bracket markers in text |
| 5 | traverse doc:a9837bff462d references top_k=10 | traverse | 2 real / 8 stub | stub-namespace handles awkward |
| 6 | "growth per cycle saturation half-reaction" by=paper rank=citation_count | find paper | clean ranked papers, useful best_chunk_section | rank=citation_count works |
| 7 | "hafnium oxide memristor resistive switching" | find by=chunk | mix of body chunks and 2 figure-caption chunks | caption noise |
| 8 | show chunk:54fce16c full | show chunk | excellent -- substantive RESULTS section | best evidence found |
| 9 | traverse chunk:54fce16c cited-in-corpus | traverse | empty | second confirmation |
| 10 | traverse doc:e05ec999cc43 references top_k=15 | traverse | 15/15 stubs | no real second hop |
| 11 | "GPC saturates self-limiting precursor exposure HfO2 ALD TDMAH" | find by=chunk | 3/8 same-doc | similarity walk needs cross-doc filter |
| 12 | "SAM inhibitor blocks ALD nucleation selectivity SiO2" | find by=chunk | 4/8 same-doc + caption noise | confirms saturation |
| 13 | show chunk:1833dba0 full | show | clean Al-doped HfO2 process section | good cross-doc neighbour |
| 14 | "TaN TiN Al-doped HfO2 memristor crossbar" | find by=chunk | duplicate handle (`chunk:d127e3bd`) under two doc_handles | collision |
| 15 | show chunk:98c135a9ce26/Figure_02__caption full | show | "Fig. 2 \| SiO2-based selector, memristor and 1S1R device." | 56-char caption ranking high |
| 16 | "atomic layer deposition memristor" by=paper rank=pagerank | find paper | clean | best bridge primitive |
| 17 | "growth per cycle temperature window" by=chunk | find chunk | mostly OK, cross-doc | preview blank again |
| 18 | "hafnium oxide resistive switching filament oxygen vacancy" by=paper | find paper | strong | paper-level bridge confirmed |
| 19 | text=true "area-selective inhibitor" | find chunk text | empty | multi-word grep broken |
| 20 | text=true "purge time" | find chunk text | 3 hits, all peripheral | single-phrase grep works but noisy |
| 21 | text=true "inhibitor" by=paper | find paper text | scores=0.0 | text+paper score opaque |
| 22 | text=true "RRAM" by=chunk | find chunk text | 3/5 reference-list chunks | acronym text-grep dominated by bibs |
| 23 | "RRAM" by=chunk semantic | find chunk | clean | semantic recovers |
| 24 | "HfO2" text=true | find chunk text | 3 hits, mixed (one URL-laden) | unit/formula-token literal works |
| 25 | "resistive random-access memory" by=chunk semantic | find chunk | overlaps RRAM but not identical | no synonym layer |
| 26 | "Al2O3" field=title by=paper | find paper title | precise, with title_match_offset | title search is great |
| 27 | sample diverse max_docs=10 | sample | 6/10 also in top-pagerank | overlap heavy |
| 28 | by=paper rank=citation_count top_k=10 | find paper rank | reproduced top-pagerank list | metrics nearly identical |
| 29 | by=paper rank=pagerank top_k=10 | find paper rank | matches | confirms |
| 30 | by=author rank=h_index top_k=8 | find author rank | all h_index=2 | metric saturated |
| 31 | "atomic layer deposition definition self-limiting binary reaction" by=paper | find paper | top hit chunk:470eb224 = ACKNOWLEDGMENTS | ranking blind to section kind |
| 32 | show chunk:470eb224 full | show | confirmed acknowledgments paragraph | ranker bug class |
| 33 | "<topic> applications <adjacent fields>" by=paper | find paper | top hits collapse onto whichever framing the corpus over-represents (here: memristors), not the breadth implied by the query | breadth-query failure mode under topic concentration; surface needs a "coverage warning" or sample-diversity signal |
| 34 | "<topic> limitations challenges throughput" by=paper | find paper | same collapse -- limitations queries surface only the dominant sub-domain's challenges | same class of failure |
| 35 | show chunk:e9873a2a full | show | section "**401**", embeds Wiley copyright + table inline | chunking and boilerplate failure |
| 36 | show chunk:380eb7a2 full | show | "Articles You May Be Interested In" sidebar with timestamp | publisher boilerplate ingested |
| 37 | show chunk:8e06e6c4 full | show | 32-reference bibliography in one chunk | section-boundary failure |
| 38 | show doc:b4ca2c2967a9 sections=["Abstract"] | show | "section filter matched no sections; available: body" | abstract not detected |
| 39 | show doc:efe5ebf249b6 | show | clean section index, "Keywords" still 3 chunks | mostly correct |
| 40 | show doc:0e762154c31c | show | sibling sections nested under "I. INTRODUCTION" | hierarchy bug |

## Phase 3 -- Corpus-Quality Diagnostics (open)

The MCP surface is no longer a blocker. The next bottleneck is evidence
quality. Single investigation track, three questions:

1. **Why visible bracket citations do not become `cited-in-corpus`.**
   Probe path: chunk text -> `parse_citation_markers` -> `bib_entries`
   join -> `target_doc_id`. Need to know per chunk which step zeroes
   out. Field evidence on this corpus: 1 of 3 probed chunks resolved 1
   in-corpus cite; the others fired the existing `WIKIFY_QUIET=0`
   stderr hint ("markers found, none resolved").
2. **Why `references` traversal is mostly stubs.** Probe path:
   `bib_entries.target_doc_id` for the seed doc -> count resolved-
   in-corpus vs unresolved-or-out-of-corpus. Field evidence:
   `doc:e05ec999cc43` -> 20/20 stubs; `doc:a9837bff462d` -> 18/20
   stubs (2 real). Distinguish "the cited paper is not in the corpus"
   (out-of-corpus) from "the paper is in the corpus but the resolver
   missed it" (resolver miss).
3. **Which chunk artifacts are parser noise vs chunker boundary bugs
   vs ranker missing filters.** Items in the chunking table above
   each fall into exactly one of those buckets. Tag them.

Diagnostic deliverable (Phase 4): a per-chunk explainability helper
that reports markers found, bib entry resolved or missing, target doc
linked or not, and target outside-corpus vs resolver-miss. Surface
decision (CLI vs MCP vs both) deferred to Phase 4.

Phase 1 follow-up logged: assert that the rewritten full-id
`resource_uri` actually dereferences via the MCP chunk resource. Not
blocking; locks down Fix #1's protected workflow.
