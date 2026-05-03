# Citation parsing + chunk-edge extensions — follow-up plan

Two related threads to investigate after the SQLite store cutover.
The first is a defect (low resolution); the second is a feature design
question (more edge types + chunk-level metrics). Open as a notebook to
think with, not yet a task list.

---

## Thread 1 — validate and improve in-text citation detection + mapping

### Symptom

On `data/corpora/ald_all_marker` (208 papers, 4985 chunks, 9719 bib
entries):

| metric | value |
|---|---|
| bib entries total | 9719 |
| with DOI parsed   | 1086 (11.2%) |
| with title >15 chars | 5973 (61.5%) |
| with year | 9652 (99.3%) |
| resolved to in-corpus doc | 165 (1.7%) |
| resolved via exact_doi | 43 |
| resolved via title_year | 85 |
| corpus docs that have a DOI | 198 / 208 |
| **unresolved bibs whose DOI matches any corpus doc's DOI** | **0** |

The last row is the smoking gun. We extract DOIs from bib raw_text in
1086 entries, and we have DOIs on 198 corpus papers, but the two sets
have **zero overlap**. Possible causes (need to investigate):

1. Citation parser is missing DOIs that exist in the raw_text (regex
   scope, formatting variants — "doi:", "https://doi.org/", "DOI:",
   trailing punctuation).
2. Document-side DOI extraction stores a different DOI per paper
   (different normalisation: case, prefix, trailing slash, version
   suffix like `.v1`).
3. DOIs in the corpus subset genuinely don't appear in any bib —
   unlikely for a thematic 200-paper sample.

### Investigation plan

1. **Audit the DOI normalisers.** Compare
   `corpus.store.documents._norm_doi` against
   `ingest.cite_parse._extract_doi` (or wherever the citation parser
   lives). Check: case, scheme prefix, trailing slash, percent-
   encoding, version suffix. Both sides need to land on the same
   canonical form.
2. **Sample 50 unresolved bibs with `len(raw_text) > 200`** and look
   at the raw_text for missed DOIs. Quick `rg "10\.\d{4}/\S+"
   raw_texts.txt` should expose the regex gap.
3. **Sample 50 in-corpus docs and search for their DOI in
   `bib_entries.raw_text`** (LIKE). If the DOI string appears but
   wasn't extracted, that's a parser gap. If it doesn't appear,
   check whether the bib entry uses a different DOI form (alias,
   pre-print version).
4. **Title-based fallback audit**. The first-50-char-lower match is
   coarse. Check edge cases: title with leading "Article:",
   abbreviated journal title accidentally matched, capitalisation
   differences post-normalisation.
5. **OpenAlex enrichment is in the pipeline** (Wave C). Verify it's
   actually backfilling DOIs into bib_entries. The `openalex` flag
   should turn on by default in the test corpus build. If it's
   skipped or failing silently, that alone could explain a lot of
   the gap.

### Ideal acceptance metric

Doubling the in-corpus resolution rate from 1.7% to ~5% on the same
corpus, no false positives (every change to resolution logic gated
by a regression test on `tests/fixtures/tiny`).

### Why this matters for the citation-walk

`citation-walk` is gated on in-corpus resolution. Today most ALD
corpus chunks have `0` in-corpus citations because the bibs they
reference exist in the corpus but the resolver doesn't see the DOI
overlap. Better resolution → more edges → richer walks.

---

## Thread 2 — should we add other chunk-edge types?

### Cosine-similarity edges between chunks

Locked default #6 says: *"no persisted `similar_to` edges; vector
search at query time is the similarity layer"*. The reason was that
cosine similarity is O(n²) and the matmul already covers the use
case. Two new use cases reopen the question:

- **Centrality / PageRank on chunks**: needs an adjacency, can't do
  it on the fly without paying the matmul cost per query.
- **Similarity-graph traversal**: "find chunks that paraphrase X
  AND are similar to Y AND in paper Z". Currently you can do two
  of those three but not all three in one call.

#### Cost envelope

| corpus | chunks | dim | full matmul | thresholded edges (>0.7) |
|---|---|---|---|---|
| tiny | ~30 | 384 | <1ms | <50 |
| ald_all_marker | 4985 | 512 | ~50 ms | ~30k–80k |
| 5k papers, 150k chunks | 150k | 384 | ~10 s | ~5M edges |
| 50k papers, ~1.5M chunks | 1.5M | 384 | ~600 s | infeasible |

So thresholded similarity edges are storable up to ~5k papers (low
hundreds of MB in `graph_edges`); past that we need ANN.

#### Recommendation: don't persist edges; do persist *projected metrics*

Keep the locked default — `graph_edges` doesn't store similarity
pairs. But add a graph **view** in `metrics_global` that constructs
the similarity adjacency *transiently* from the vector matrix,
computes PageRank / centrality, writes the per-chunk scores to
`node_metrics`. The graph is computed on demand by `metrics
refresh --view chunk_similarity`; the matrix never leaves RAM.

This is consistent with how `corpus_citation` PageRank already
works (matrix is transient, scores persist).

```python
# in metrics_global.VIEWS:
"chunk_similarity": GraphView(
    name="chunk_similarity",
    description="chunk -- chunk cosine >= threshold; undirected",
    node_types=("chunk",),
    edge_kinds=(),  # virtual; no graph_edges row
    directed=False,
    seed_sql="SELECT chunk_id FROM chunks",
    metrics=("pagerank", "degree_centrality"),
)
```

The custom builder reads the embedding matrix, thresholds, builds a
networkx Graph, hands it to nx.pagerank, writes scores out. No
graph_edges modification.

### Other chunk edge kinds worth considering

1. **`chunk -> chunk cites_evidence`** (across papers) — chunk A in
   paper X cites paper Y at marker [N]; the chunk in Y that A is
   pointing at = the most-relevant chunk of Y to the *concept that A
   discusses*. We computed this on the fly in `citation-walk`; we
   could persist it (per the seed concept). Probably overkill —
   needs a concept axis.

2. **`chunk -> chunk co_section`** — chunks in the same section_path
   of the same doc. Already implicit via `chunks_of_section` index;
   no need to materialise unless we want fast traversal.

3. **`chunk -> chunk follows`** — adjacent chunks (ord = N+1). Same
   story as co_section; cheap to derive on demand.

The only one that's clearly worth its weight is similarity, and
even that should stay as a transient projection, not edges.

---

## Thread 3 — chunk-level PageRank: meaning and uses

### Three candidate graphs to PageRank over

#### (a) Similarity graph

- Edges: cosine(A, B) >= threshold
- Undirected, weighted by similarity
- **PageRank meaning**: how central is a chunk in the corpus's
  semantic neighbourhood. High score = "this chunk's content has
  many semantic neighbours" = canonical explanation of an idea
  multiple papers discuss.
- **Use**: dedup target ("this is the textbook version, others
  echo it"); canonical-snippet picker for wiki pages; scope-finder
  for which sub-topics in the corpus have the densest coverage.

#### (b) Citation-flow graph (chunk → chunk via document)

- Edges: chunk A in paper X has marker [N] → bib resolves to paper
  Y → chunk Y_j is the most-relevant chunk of Y for A's section.
  This is exactly what `citation-walk` synthesises.
- Directed, edge weight = relevance score.
- **PageRank meaning**: "this chunk's ideas get cited and re-stated
  widely". High score = primary-source paragraph that the corpus
  builds on.
- **Use**: pick the canonical primary source paragraph per topic;
  weight evidence selection toward "this paragraph everybody points
  at"; prioritise reading order for a literature review.

#### (c) Co-cited graph (chunks that cite the same papers)

- Edges: chunk A and chunk B both cite paper Y → A — B with weight
  proportional to overlap.
- **PageRank meaning**: "this chunk discusses a body of work that
  many other paragraphs also discuss" (bibliographic coupling at
  the chunk level).
- **Use**: detect the "introduction-style" paragraphs that most
  papers' background sections share — useful for finding the
  current consensus framing of a topic.

### Recommended order

1. **(a) chunk-similarity PageRank** — easiest to build, most
   directly useful, no dependency on citation resolution being fixed.
2. **(b) citation-flow PageRank** — best signal but needs Thread 1
   to land first; with current 1.7% resolution, the graph is too
   sparse to be meaningful.
3. **(c) co-citation PageRank** — interesting but lower-priority;
   can be derived from (b) once resolution is good.

### How to leverage the score

A new `node_metrics` row per chunk (or per (chunk, view)) gives:

- `corpus find "X" --rerank chunk_centrality` — re-order semantic
  top-K so the canonical paragraph wins ties.
- `corpus sample --strategy diverse` — already does PageRank+coverage
  at the doc level. Add a `--by chunk` mode that does the same at
  chunk level, picking K canonical chunks across the corpus.
- `corpus find --rank chunk_pagerank --top-k 10` — list the most-
  central paragraphs in the corpus, no query. Useful for
  bootstrapping a wiki ("what does this corpus actually talk
  about?").
- `wiki commit` could prefer to ground evidence on high-PageRank
  chunks when the writer has multiple candidates with similar
  semantic scores — picks the canonical version.

### Cost

Per refresh:

| step | tiny | ald (5k chunks) | 150k chunks |
|---|---|---|---|
| matmul N×N cosine | <1 ms | ~50 ms | ~10 s |
| threshold to sparse | <1 ms | ~20 ms | ~5 s |
| nx.pagerank | <10 ms | ~200 ms | ~30 s |
| write node_metrics | <1 ms | ~50 ms | ~1 s |

Tractable for the working corpus size. Past 50k chunks we'd want a
sparse-only path (skip materialising the full N×N matrix).

---

## Suggested next steps

If we tackle this:

1. **Spend a session on Thread 1** — instrument the citation parser,
   audit DOI normalisation, sample unresolved bibs, fix the parser
   gaps. Goal: 5%+ resolution rate on the ALD corpus.
2. **Then Thread 3 (a)** — chunk-similarity PageRank as a new graph
   view in `metrics_global`. Add a `--rerank chunk_pagerank` option
   to `corpus find`. Add a `corpus sample --by chunk` mode.
3. **Then Thread 3 (b)** — citation-flow PageRank, gated on Thread 1
   landing. Reuse the citation-walk builder to construct the graph.
4. **Skip Thread 2** for now — keep the "no persisted similarity
   edges" default. Computation is on demand; only metrics persist.
