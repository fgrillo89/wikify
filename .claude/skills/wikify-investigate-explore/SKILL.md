---
name: wikify-investigate-explore
description: Recursive exploration pattern library for wikify-investigate. Five named, depth-bounded procedures (P1 hub-anchor, P2 citation-walk, P3 semantic-boundary, P4 exact-term sweep, P5 gap-explorer) that compose existing corpus_find / corpus_traverse / corpus_citation_walk / wiki_find MCP primitives and append evidence into per-slug notebooks. Editor invokes one pattern per Task with a slug-disjoint target list.
allowed-tools: Bash(wikify *) Task mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__corpus_find mcp__wikify__corpus_show mcp__wikify__corpus_sample mcp__wikify__corpus_traverse mcp__wikify__corpus_citation_walk mcp__wikify__corpus_similarity_walk mcp__wikify__corpus_schema mcp__wikify__wiki_find mcp__wikify__wiki_show mcp__wikify__wiki_traverse
---

# wikify-investigate-explore

Library of five recursive exploration patterns invoked by the
`wikify-investigate` editor. Each pattern is depth-bounded,
deduplicated against `notebook.provenance.covered_chunks`, and
composes existing CLI / MCP primitives — no new tools.

A Task instance of this skill takes ONE pattern + ONE target list +
budget and returns a structured envelope. The editor dispatches
multiple parallel Tasks per round.

## Inputs

- `pattern`: one of `P1`, `P2`, `P3`, `P4`, `P5`.
- `target`: pattern-dependent (see each pattern below).
- `run`: bundle path. `corpus`: corpus path.
- `budget_chunks`: cap on chunks judged this Task (default 30).
- `depth`: recursion depth for P1/P2 (default 2 / 1).
- `current_round`: round number for round_history bookkeeping.

## Return envelope (per target)

```json
{
  "target": "<slug-or-doc-or-pair>",
  "pattern": "P3",
  "appended_chunks": 6,
  "appended_concepts": 1,
  "covered_docs_delta": {"doc:abc": 3, "doc:def": 1},
  "covered_chunks_delta": ["chunk:abc__c0001", "..."],
  "exploration_log_entry": {"round": 4, "pattern": "P3",
                            "target": "memristor", "accepted": 6},
  "stop_reason": "budget_chunks_reached" | "no_new_neighbours" |
                 "depth_zero" | "ok",
  "tokens_in": 14000,
  "tokens_out": 320,
  "model_id": "claude-sonnet-4-6",
  "escalate": null
}
```

`escalate` is `null` unless the Task hit a decision outside its
mandate (concept-vs-evidence routing, kind/stencil choice, merge of
near-duplicate titles, slug create/destroy). In that case set it to
`{"question": ..., "context": ..., "options": [...]}` and stop short
of guessing — the editor adjudicates (see
`wikify-investigate/SKILL.md`, Escalation). Routine accept/reject of a
single chunk is the Task's own job and is never escalated.

The editor folds `covered_*_delta` into the notebook frontmatter via
the `notebook.merge_covered_docs` / `append_exploration_log` helpers
between Tasks; do not write the notebook frontmatter from inside a
Task (avoids serialisation hazards).

## Shared mechanics

- Bind context before the first MCP call:
  `mcp__wikify__context_set(corpus_path="<corpus>", bundle_path="<run>")`.
- Initialise `seen_chunks` from `notebook.provenance.covered_chunks`
  for the target slug (P1 multi-slug: union over all targets).
- Every accepted chunk goes through the existing
  `wikify-gather-evidence-cluster` judgement loop (sonnet supervisor
  + haiku judges). This skill produces *candidate* chunk sets;
  cluster vets them.
- The vetter is invoked at the end, in one batch per target slug:
  ```bash
  wikify work add evidence <slug> --records <path> --run <bundle>
  ```
  where `<path>` is a JSONL of vetter-accepted EvidenceRecords for
  this round. Each record's `chunk_id` MUST be the corpus CANONICAL id
  (the long `id` field returned by `corpus_find` / `corpus_show`, e.g.
  `<title>_<dochex>__c0007_<hex>`), never the short `chunk:<hex>`
  handle. `work add evidence` resolves handles back to canonical when
  the bundle's corpus is reachable and rejects unresolvable ids, but
  storing the canonical id directly is the contract; handles silently
  zero out coverage and citation grounding when the corpus is not
  reachable.
- `excluded_kinds = ["references", "acknowledgments", "appendix",
  "figure", "table", "caption", "boilerplate"]` is the standard
  structural exclusion.

## P1 — hub-anchor expansion

**Target**: list of corpus doc handles to seed from (typically top-K
by PageRank or citation_count).

**Default**: `depth = 2`, `budget_chunks = 40` per doc.

```
P1(target_docs, depth=2, budget_chunks=40):
  for doc in target_docs:
    # 1. Extract candidate concepts from this doc (one haiku call).
    candidates = haiku_extract_concepts(corpus_show(doc, full=True),
                                        max_candidates=8)
    for concept in candidates:
      slug = canonicalise(concept.title)
      # 2. Anchor the concept to a chunk in THIS doc.
      anchor = corpus_find(query=concept.title, in_doc=doc,
                            text=True, top_k=1) \
            or corpus_find(query=concept.title, in_doc=doc,
                            rank="semantic", top_k=1)
      if anchor is None: continue
      candidate_chunks[slug].add(anchor)
      # 3. Recursively expand the anchor.
      expand(anchor, slug, depth)

expand(chunk, slug, depth):
  if depth == 0 or budget exhausted: return
  if chunk.id in seen_chunks: return
  seen_chunks.add(chunk.id)
  neighbours = corpus_traverse(chunk, to="cited-by", top_k=3) \
             | corpus_traverse(chunk, to="references", top_k=3) \
             | corpus_find(query=chunk_text(chunk),
                           rank="semantic", top_k=5,
                           exclude_kinds=excluded_kinds) \
             | corpus_find(query=concept_name(slug),
                           text=True, top_k=5)
  for n in neighbours:
    candidate_chunks[slug].add(n)
    expand(n, slug, depth - 1)
```

After the recursion, send `candidate_chunks[slug]` to the cluster
vetter; vetter accepts a subset; that subset is what `appended_chunks`
counts.

Per slug created or extended, also call:

```bash
wikify work add concept "<Title>" --kind article \
  --aliases '[...]' --run <bundle>
wikify work notebook-init <slug> --seed-docs '["doc:X"]' \
  --stencil article-method --run <bundle>
```

**Stop reasons**: `budget_chunks_reached`, `depth_zero`,
`no_new_neighbours`, `ok`.

## P2 — citation-walk

**Target**: ONE existing slug (with notebook on disk).

**Default**: `depth = 1`, `budget_chunks = 20`.

```
P2(slug, depth=1, budget_chunks=20):
  notebook = read notebook(slug)
  seen_chunks = set(notebook.provenance.covered_chunks)
  for chunk_id in notebook.provenance.covered_chunks:
    # Outgoing references (papers this chunk cites).
    refs = corpus_traverse(chunk_id, to="references", top_k=5)
    # Incoming citations (later papers citing this chunk's doc).
    cites = corpus_traverse(chunk_id, to="cited-by", top_k=5)
    for h in refs | cites:
      if h.id in seen_chunks: continue
      seen_chunks.add(h.id)
      candidate_chunks.add(h)
      if depth > 0: P2_recurse(h, depth - 1)

P2_recurse(chunk, depth):
  if depth == 0 or budget exhausted: return
  for h in corpus_traverse(chunk, to="references", top_k=3) \
         | corpus_traverse(chunk, to="cited-by", top_k=3):
    if h.id in seen_chunks: continue
    seen_chunks.add(h.id)
    candidate_chunks.add(h)
```

Send `candidate_chunks` through the cluster vetter. Citation graph
explodes fast — keep `depth = 1` unless the editor explicitly raises.

**Stop reasons**: `budget_chunks_reached`, `depth_zero`,
`no_new_neighbours`, `ok`.

## P3 — semantic-boundary expansion

**Target**: ONE existing slug (notebook on disk) OR a slug pair
(bridge mode; union both notebooks' chunk sets).

**Default**: `budget_chunks = 30`.

```
P3(slug_or_pair, budget_chunks=30):
  if pair:
    seed_chunks = notebook(a).covered_chunks
                | notebook(b).covered_chunks
  else:
    seed_chunks = notebook(slug).covered_chunks
  seen_chunks = set(seed_chunks)
  # Use the strongest 3-5 seed chunks as semantic anchors.
  anchors = top_k_by_evidence_score(seed_chunks, k=5)
  for anchor in anchors:
    hits = corpus_find(query=chunk_text(anchor),
                       rank="semantic", top_k=10,
                       exclude_kinds=excluded_kinds)
    for h in hits:
      if h.id in seen_chunks: continue
      seen_chunks.add(h.id)
      candidate_chunks.add(h)
```

Bridge mode emits a `concept_suggestion` (for a new connector concept)
rather than appending to either endpoint's evidence.

**Stop reasons**: `budget_chunks_reached`, `no_new_neighbours`, `ok`.

## P4 — exact-term sweep

**Target**: ONE existing slug with stable aliases (3+).

**Default**: `budget_chunks = 20`. Looser accept threshold than other
patterns (exact-string co-occurrence is its own structural signal).

```
P4(slug, budget_chunks=20):
  card = work_card(slug)
  variants = list(card.aliases) \
           | generate_variants(card.page_id) \
           | canonical_acronyms(notebook(slug))
  seen_chunks = set(notebook(slug).provenance.covered_chunks)
  for v in variants:
    hits = corpus_find(query=v, text=True, top_k=10,
                       exclude_kinds=excluded_kinds)
    for h in hits:
      if h.id in seen_chunks: continue
      seen_chunks.add(h.id)
      candidate_chunks.add(h)
```

`generate_variants` covers: plural <-> singular (rule-based),
hyphenated <-> spaced ("atomic layer deposition" /
"atomic-layer-deposition"), acronym expansion ("ALD" <-> "atomic layer
deposition" only when both already appear in the dossier body or
aliases).

Send through cluster vetter with `accept_threshold = tau_lite`
(the cluster skill exposes this as a config knob; default = 0.65 vs
the usual 0.75).

**Stop reasons**: `budget_chunks_reached`, `no_new_neighbours`, `ok`.

## P5 — gap-explorer (the coverage driver)

**Target**: literal "global". Operates on the bundle-wide residual.

**Default**: `budget_chunks = 20`.

```
P5(budget_chunks=20):
  # 1. Compute residual via the CLI.
  # (the CLI walks committed pages + in-flight notebooks)
  residual = wikify_work_coverage("--corpus <corpus>",
                                   "--run <bundle>") \
              .residual_chunk_ids   # or call wikify work coverage twice
  # 2. Rank residual chunks by PageRank.
  ranked = corpus_find(query="", by="chunk", rank="pagerank",
                       top_k=budget_chunks)
  # 3. For each top-ranked chunk, try to attach OR seed.
  for chunk in ranked:
    if chunk.id not in residual: continue
    nearest = wiki_find(query=corpus_show(chunk, full=True).text,
                        mode="semantic", top_k=3)
    attached = False
    for page in nearest:
      if relevance(chunk, page.slug) >= tau:
        # Route as an evidence_suggestion to the existing page.
        emit_evidence_suggestion(page.slug, chunk)
        attached = True
        break
    if not attached:
      # New concept proposal -> next round's SEED wave may pick it up.
      emit_concept_suggestion(chunk)
```

`emit_evidence_suggestion` / `emit_concept_suggestion` write JSONL
records to `work/inbox/evidence_suggestions.jsonl` and
`work/inbox/concept_suggestions.jsonl`. The editor's `work tend`
consumes them next round.

P5 fires every round. Its small budget keeps the cost low while the
deterministic residual sampling guarantees that, given enough rounds,
the loop reaches `chunk_coverage_ratio` close to 1.0 (modulo noise
chunks that no concept wants).

**Stop reasons**: `residual_empty`, `budget_chunks_reached`,
`no_new_proposals`, `ok`.

## Pattern selection (editor side)

Patterns themselves do not choose. The editor's precedence rubric
(see `wikify-investigate/SKILL.md`) picks the pattern per target.

| editor wave | pattern | target shape |
|---|---|---|
| WRITE | n/a (writer skill) | slug |
| GROW (citations exist) | P2 | slug |
| GROW (aliases exist) | P4 then P3 (chained) | slug |
| GROW (otherwise) | P3 | slug |
| BRIDGE | P3 | slug pair |
| SEED | P1 | doc list |
| GAP | P5 | "global" |

## Hard rules

- **No notebook frontmatter writes from inside the Task.** Return the
  delta in the envelope; the editor folds it in. Avoids races and
  serialisation hazards.
- **One slug per Task** (except BRIDGE, which uses a slug pair).
  The editor's dispatch plan is slug-disjoint.
- **All accepted chunks go through `wikify-gather-evidence-cluster`**
  (or its `--from-ids` CLI path) for the actual evidence append.
  This skill produces candidates only.
- **Respect `seen_chunks`.** Re-judging the same chunk wastes budget
  and produces nothing.
- **Escalate, don't guess.** On a decision outside this Task's mandate
  (concept-vs-evidence routing, kind/stencil, merge, slug
  create/destroy), return an `escalate` block and stop short of the
  call; the top-tier editor resolves it. Do not silently invent a
  concept or re-route evidence on an ambiguous signal.

## References

- `../wikify/references/exploration/patterns.md` — formal definitions
- `../wikify-gather-evidence-cluster/SKILL.md` — the vetter
- `../wikify-bundle/SKILL.md` — CLI mechanics
- `../wikify-search-corpus/SKILL.md` — MCP cheatsheet
