# Exploration Patterns

Five named, depth-bounded, deduplicated recursive procedures used by
`wikify-investigate-explore`. The editor (`wikify-investigate`)
selects which pattern fires per target based on the dispatch rubric;
the patterns themselves are mechanical.

All patterns share these conventions:

- A `seen_chunks` set, seeded from
  `notebook.provenance.covered_chunks` for the target slug. Every
  candidate is checked against this set before judgement.
- Structural exclusion `excluded_kinds = ["references",
  "acknowledgments", "appendix", "figure", "table", "caption",
  "boilerplate"]` applied to all `corpus_find` calls.
- Candidate chunks go through `wikify-gather-evidence-cluster` for
  final judgement. The patterns produce *candidates only* — they do
  not decide what becomes evidence.
- All MCP / CLI primitives the patterns use already exist; no new
  tooling.

## P1 — hub-anchor expansion

Discover new concepts from high-value seed docs and anchor each one
to a corpus chunk, then expand the neighbourhood through four edge
types in parallel.

**Inputs**: `target_docs` (list of doc handles, typically top-K by
`rank=pagerank` or `rank=citation_count`), `depth=2`,
`budget_chunks=40` per doc.

**Edges per recursion step**:

1. adjacent ord+/-1 chunks in the same doc
   (`corpus_traverse(chunk, to="adjacent", top_k=2)`)
2. semantic neighbours of the chunk's body text
   (`corpus_find(query=chunk_text, rank="semantic", top_k=5)`)
3. exact-string neighbours for the concept's name
   (`corpus_find(query=concept_name, text=True, top_k=5)`)
4. citation hops in both directions
   (`corpus_traverse(chunk, to="cited-by", top_k=3)` +
    `to="references", top_k=3`)

**Stop**: budget reached, depth zero, or no new neighbours.

## P2 — citation-walk

Deepen an existing dossier through its citation graph. The chunks
already cited by the dossier serve as the recursion's seed set; the
walk follows `references` (papers cited by these chunks) and
`cited-by` (papers citing them).

**Inputs**: single `slug` with a notebook on disk, `depth=1`,
`budget_chunks=20`.

**Default depth is 1** — the citation graph branches fast and depth-2
quickly hits diminishing returns. Editor may raise depth explicitly.

## P3 — semantic-boundary expansion

Find what an existing dossier is missing without leaving its topic.
Picks the strongest 3-5 chunks already in the dossier (by evidence
score), uses each as a semantic query, and accepts new neighbours.

**Inputs**: single `slug` OR slug pair (bridge mode union),
`budget_chunks=30`.

In bridge mode, the seed set is the union of the two endpoints'
covered_chunks; P3 then proposes a connector concept rather than
appending to either endpoint.

## P4 — exact-term sweep

Catch what semantic search misses (acronyms used inconsistently,
hyphenation variants). Generates variants of the slug's aliases and
runs exact-string `corpus_find(text=True)` for each.

**Inputs**: single `slug` with stable aliases (3+),
`budget_chunks=20`.

**Looser accept threshold** (`tau_lite` ~0.65 vs the usual 0.75):
exact-string co-occurrence is structurally stronger than a semantic
match.

**Variant generation rules**: plural/singular, hyphen/space,
acronym/expansion (only when both forms are already on the dossier).

## P5 — gap-explorer (the coverage driver)

This is the pattern that makes `wikify-investigate` cover all
chunks if pushed to the limit. P5 computes the residual chunk set
(`corpus.chunks - union(notebook.covered_chunks) -
union(committed_page.cited_chunks)`), samples it by PageRank, and
tries to either attach each residual chunk to a nearby committed
page (via `wiki_find`) or propose a new concept.

**Inputs**: literal target `"global"`, `budget_chunks=20`.

P5 outputs flow through the inbox channels
(`evidence_suggestions.jsonl`, `concept_suggestions.jsonl`); the next
`work tend` consolidates them. P5 never edits notebooks or evidence
ledgers directly.

**Termination guarantee**: with unbounded budget, P5 reduces
`|residual|` by at least one per round (the highest-PageRank residual
chunk is always picked). Coverage asymptotes toward 1.0; the only
gap is the noise floor (chunks no concept wants).

## Pattern selection (editor rubric)

| editor wave | pattern | target shape | when |
|---|---|---|---|
| WRITE | n/a | slug | `band == ready` |
| GROW (citations) | P2 | slug | notebook has citation anchors |
| GROW (aliases) | P4 + P3 chained | slug | aliases >= 3 |
| GROW (otherwise) | P3 | slug | growing band |
| BRIDGE | P3 (pair) | slug pair | M3 > 0.45, weak edge exists |
| SEED | P1 | doc list | low concept count |
| GAP | P5 | "global" | every round |

The editor (`wikify-investigate/SKILL.md`) owns precedence and the
slug-disjoint dispatch invariant. Patterns do not select; the editor
selects.

## What patterns do NOT do

- No notebook frontmatter writes. The Task returns deltas; the editor
  folds them in via `notebook.merge_covered_docs` and
  `notebook.append_exploration_log` between Tasks.
- No final evidence acceptance. Patterns produce candidate chunk sets;
  `wikify-gather-evidence-cluster` is the vetter.
- No model calls outside the judge path. P1's concept-extraction
  haiku call is the only exception; P2-P5 are mechanical until the
  vetter step.
- No `state.json` mutation. Round counter, corpus_fingerprint, and
  budget live in events + state, all written by the editor.
