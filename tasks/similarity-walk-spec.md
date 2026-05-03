# `corpus similarity-walk` — exploration via cosine neighbours

## Why

`citation-walk` traces author intent (in-text [N] markers); on the
ALD corpus it dead-ends at hop 0 for ~98% of seeds because
in-corpus citation resolution is sparse (1.7%; see
`citation-and-chunk-edges-followup.md` Thread 1). A complementary
walker that follows **semantic neighbours via cosine on the
existing vector matrix** covers what citation-walk misses, with no
schema changes.

The vector matrix is already loaded once per process. Chunk-to-chunk
cosine is `matrix @ matrix[i]` — sub-millisecond per source chunk.
Zero new tables, zero new edges in `graph_edges`.

## Shape

```
corpus similarity-walk "<concept>" \
    --depth 2 --top-k 5 --neighbors 3 --threshold 0.65 \
    --rank all --cross-doc-only --format compact
```

Returns the same `{seeds, edges, chunks}` shape as `citation-walk`,
but edges carry `kind="similar"` and a `score=<cosine>` field.

## Algorithm

```
hop 0:
    frontier = search_chunks(query, top_k=K, rank=rank)

hop n (n in 1..depth):
    next_frontier = []
    for each chunk c in frontier:
        vec = vector_index.vector(c.id)             # already in RAM
        sims = matrix @ vec                         # one matmul
        for cid, score in argsort_top(neighbors + visited + 1):
            if score < threshold: skip
            if cid in chunks: skip                  # dedup
            if cross_doc_only and doc_of(cid) == doc_of(c): skip
            emit edge {src=c.id, dst=cid, kind="similar", score}
            chunks[cid] = {hop: n, doc_id: ..., score: score}
            next_frontier.append(cid)
    frontier = next_frontier
    if not frontier: break
```

Cycle-safe via the dedup; depth-bounded; per-hop cost is
`|frontier| * (matrix-matmul + threshold scan)`.

## Knobs (and defaults)

| flag | default | meaning |
|---|---|---|
| `--top-k` | 5 | seed count at hop 0 |
| `--depth` | 2 | walk hop limit |
| `--neighbors` | 3 | per-chunk fanout per hop |
| `--threshold` | 0.65 | cosine cut; below = drop the edge |
| `--cross-doc-only` | true | exclude same-doc neighbours by default; trivially-similar adjacent paragraphs add noise |
| `--include-same-doc` | — | opt in to same-doc edges |
| `--rank` | `all` | how to pick hop-0 seeds (semantic/bm25/hybrid/all) |
| `--from chunk:<handle>` | — | optional: start from an explicit chunk instead of a query (skips hop 0 search) |

Threshold default of 0.65 is calibrated for jina-v2-small in our
ALD corpus: ~0.85 is near-paraphrase, ~0.65 is topical neighbour,
<0.5 is unrelated. Configurable.

## Output (compact format)

```
hop=0  via=sb-  chunk:abc  doc:foo  
hop=1  via=---  chunk:def  doc:bar  similar=0.81 <- chunk:abc
hop=2  via=---  chunk:ghi  doc:baz  similar=0.74 <- chunk:def
```

`via=---` for non-seed chunks because they weren't found via the
seed-time search modes — they came in through similarity. The
`similar=<score> <- chunk:<src>` annotation gives the cosine and
parent.

## Cost on 4985-chunk corpus

| call | wall-clock |
|---|---|
| hop 0 (`find --rank all`) | ~165 ms (matches current `find`) |
| hop 1 (5 seeds × matmul) | ~3 ms |
| hop 2 (≤15 frontier × matmul) | ~10 ms |
| total at depth=2 | **~180 ms** |

The vector matrix loads once on first `find --rank semantic |
hybrid | all` per process; subsequent walks pay only per-frontier
matmul. `similarity-walk` is essentially free given any prior
vector-aware call in the same process.

## Where it fits

| | `citation-walk` | `similarity-walk` |
|---|---|---|
| edges | in-text [N] markers | cosine ≥ threshold |
| graph density | sparse (1.7% on ALD) | dense (every chunk has neighbours) |
| dead-end rate | high | low |
| use case | trace argument to primary source | explore conceptual neighbourhood |
| hop semantics | directed (A cites B) | undirected (A ~ B) |
| cost | one find + chunk_citations join per hop | one matmul per frontier chunk |

The two are complementary. The cheatsheet entry should make this
explicit: pick `citation-walk` when you want author-asserted
relationships, `similarity-walk` when you want semantic proximity.

## Use cases

- **"Where else does this idea show up?"** — `depth=1 neighbors=10
  --cross-doc-only`. Surfaces the corpus-wide echo of a concept.
- **"Cluster around this paragraph"** — `--from chunk:<short>
  --depth 2 --neighbors 4 --threshold 0.75`. Skips the query
  step and explores from a specific chunk.
- **"Bridge query → corpus structure"** — concept query → top seeds
  → expand 2 hops → emit the chunk neighbourhood graph for the
  caller to read.

## Implementation footprint

```
src/wikify/corpus/queries.py
    + similarity_walk(corpus, *, query=None, from_chunk=None, depth,
                      top_k, neighbors, threshold, rank,
                      cross_doc_only) -> dict
                                                          # ~60 lines

src/wikify/cli/corpus.py
    + cmd_similarity_walk(...)                            # ~35 lines

tests/wikify/store/test_similarity_walk.py
    + 5–6 tests:
        seed shape, dedup, threshold drops low-score, depth
        cap, cross-doc filter, --from chunk path
                                                          # ~100 lines

.claude/skills/wikify-search-corpus/SKILL.md
    + cheatsheet row, 4-line recipe blurb                 # ~6 lines
```

No schema changes. No `graph_edges` rows. No new metrics. The
`VectorIndex.vector(chunk_id)` and `.matrix` accessors already
exist; the spec just composes them into a walk.

## Open design questions

1. Mutually-exclusive seed source: `query` xor `from_chunk`. If both
   are passed, error or prefer `from_chunk`?
2. Should the walker carry over the seed-time `rank` to subsequent
   hops if it's expensive (`all`)? Likely no — once we have the
   chunk, hops are pure cosine.
3. Multi-source seeds: should `--top-k` produce parallel walks (one
   per seed) and merge, or stay as a single shared frontier? The
   spec above is shared frontier; parallel walks would be useful
   when seeds are conceptually distant from each other.

## Acceptance criteria for v1

- `similarity-walk "growth per cycle" --depth 2 --top-k 5` returns at
  least 5 seeds and at least 2 edges on `data/corpora/ald_all_marker`.
- `similarity-walk --from chunk:<short>` works without a query.
- `--threshold 0.99` returns 0 edges (no false positives).
- p99 wall-clock < 300 ms on the 5k-chunk corpus.
- No new files in `wikify.db` schema; the projection lives entirely
  in RAM during the call.

## Status

Spec only. Implementation queued; awaiting confirmation on the open
design questions.
