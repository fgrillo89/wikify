# Explorer role brief

Lossless role brief. Read this instead of the full file set; consult the
named source only if this brief is ambiguous or you hit an out-of-brief
case.

`allowed-tools: Bash(wikify *) Task mcp__wikify__context_set mcp__wikify__context_show mcp__wikify__corpus_find mcp__wikify__corpus_show mcp__wikify__corpus_sample mcp__wikify__corpus_traverse mcp__wikify__corpus_citation_walk mcp__wikify__corpus_similarity_walk mcp__wikify__corpus_schema mcp__wikify__wiki_find mcp__wikify__wiki_show mcp__wikify__wiki_traverse`.

You are one explore Task: ONE pattern (P1-P5) + ONE target list + budget.
You walk the corpus with existing `corpus_*` / `wiki_*` MCP primitives
(no new tools), produce CANDIDATE chunk sets, and return a structured
envelope. Patterns are mechanical; they do not decide what becomes
evidence. `gather-evidence` (or its `--from-ids` CLI path) is the vetter.

## Inputs

- `pattern`: one of `P1`, `P2`, `P3`, `P4`, `P5`.
- `target`: pattern-dependent (see each pattern).
- `run`: bundle path. `corpus`: corpus path.
- `budget_chunks`: cap on chunks judged this Task (default 30).
- `depth`: recursion depth for P1/P2 (default 2 / 1).
- `current_round`: round number for round_history bookkeeping.

## Shared mechanics

- Bind context before the first MCP call:
  `mcp__wikify__context_set(corpus_path="<corpus>", bundle_path="<run>")`.
- Initialise `seen_chunks` from BOTH
  `notebook.provenance.covered_chunks` AND the canonical `chunk_id`s
  already judged for the target slug(s). Fetch the latter in one call
  (pass every target slug):
  `wikify work seen-chunks <slug> [<slug> ...] --run <bundle>` -> returns
  `{"seen_chunk_ids": [...], "n_seen": N}` (union of active evidence
  chunk_ids). Skip any candidate whose canonical id is in `seen_chunks`
  before spending a judge on it. Re-judging wastes budget.
- **Structural exclusion (standard):** apply
  `excluded_kinds = ["references", "acknowledgments", "appendix",
  "figure", "table", "caption", "boilerplate"]` to all `corpus_find`
  calls.
- Per-chunk judging runs the `gather-evidence` loop on the **haiku (S)
  tier**; the sonnet (M) tier is the supervisor's synthesis. This skill
  is the model-judged path (haiku telemetry). The editor's `wikify work
  build-evidence` is the cheap deterministic alternative (seed-doc chunks
  + `corpus find --rank all` under the same `excluded_kinds`, NO per-chunk
  model calls, editor/M telemetry). Both paths terminate in the same
  per-slug evidence ledger; a round driven by `build-evidence` shows
  ~zero haiku usage (expected, not a failure).
- **Explore direct-accept commit (this skill's own path).** The vetter is
  invoked at the end, one batch per target slug:
  `wikify work add evidence <slug> --records <path> --run <bundle>`,
  where `<path>` is JSONL of accepted EvidenceRecords for this round.
  (The gather-evidence judge-routed path commits differently, via
  `build-evidence --from-ids`; see the gather-evidence vetter section.)
  Each record's `chunk_id` MUST be the corpus **CANONICAL** id, read from
  the `canonical_id` field on every chunk row returned by `corpus_find` /
  `corpus_show` / `corpus_traverse` (e.g.
  `<title>_<dochex>__c0007_<hex>`), NEVER the short `chunk:<hex>` handle.
  `work add evidence` resolves handles back to canonical only when the
  bundle's corpus is reachable and rejects unresolvable ids; storing
  `canonical_id` directly is the contract (handles silently zero out
  coverage and citation grounding when the corpus is not reachable).

### Evidence-record fields

Each EvidenceRecord: `chunk_id` (canonical), `doc_id`, `quote`
(verbatim substring of the chunk text), `kind`, `score`, `note`.

### kind=definition capture (mandatory)

A judge MUST mark a chunk `kind=definition` (`def_for: [<slug>]`) when it
opens with `<title> is ...` / `<title> refers to ...` / `<acronym> stands
for ...`. Definition chunks are gold: the writer's lead needs one, and
the maturity gate `has_definition_evidence` requires at least one quote
matching the definition regex (`is a`, `refers to`, `defined as`, ...).
Score ladder (topic role, per slug): 1.00 definition, 0.95 mechanism,
0.85 materials/process, 0.75 application, 0.60 sibling-relevant.

## P1 - hub-anchor expansion

**Target**: list of corpus doc handles (typically top-K by PageRank or
citation_count). **Default**: `depth = 2`, `budget_chunks = 40` per doc.

Discover new concepts from high-value seed docs, anchor each to a corpus
chunk, then expand the neighbourhood through four edge types.

```
P1(target_docs, depth=2, budget_chunks=40):
  for doc in target_docs:
    candidates = haiku_extract_concepts(corpus_show(doc, full=True),
                                        max_candidates=8)   # one haiku call
    for concept in candidates:
      slug = canonicalise(concept.title)
      anchor = corpus_find(query=concept.title, in_doc=doc, text=True, top_k=1) \
            or corpus_find(query=concept.title, in_doc=doc, rank="semantic", top_k=1)
      if anchor is None: continue
      candidate_chunks[slug].add(anchor)
      expand(anchor, slug, depth)

expand(chunk, slug, depth):
  if depth == 0 or budget exhausted: return
  if chunk.id in seen_chunks: return
  seen_chunks.add(chunk.id)
  neighbours = corpus_traverse(chunk, to="cited-by", top_k=3) \
             | corpus_traverse(chunk, to="references", top_k=3) \
             | corpus_find(query=chunk_text(chunk), rank="semantic", top_k=5,
                           exclude_kinds=excluded_kinds) \
             | corpus_find(query=concept_name(slug), text=True, top_k=5)
  for n in neighbours:
    candidate_chunks[slug].add(n); expand(n, slug, depth - 1)
```

Four edge types per recursion step: semantic neighbours of the chunk
body; exact-string neighbours of the concept name; citation hops both
directions. After recursion, send
`candidate_chunks[slug]` to the vetter. Per slug created or extended,
also call:

```bash
wikify work add concept "<Title>" --kind article --aliases '[...]' --run <bundle>
wikify work notebook-init <slug> --seed-docs '["doc:X"]' --stencil article-method --run <bundle>
```

**Stop reasons**: `budget_chunks_reached`, `depth_zero`,
`no_new_neighbours`, `ok`.

## P2 - citation-walk

**Target**: ONE existing slug (notebook on disk). **Default**:
`depth = 1`, `budget_chunks = 20`.

Deepen a dossier through its citation graph. Chunks already cited by the
dossier are the seed set; walk `references` (papers these chunks cite)
and `cited-by` (papers citing them).

```
P2(slug, depth=1, budget_chunks=20):
  seen_chunks = set(notebook(slug).provenance.covered_chunks)
  for chunk_id in notebook.provenance.covered_chunks:
    for h in corpus_traverse(chunk_id, to="references", top_k=5) \
           | corpus_traverse(chunk_id, to="cited-by", top_k=5):
      if h.id in seen_chunks: continue
      seen_chunks.add(h.id); candidate_chunks.add(h)
      if depth > 0: P2_recurse(h, depth - 1)

P2_recurse(chunk, depth):
  if depth == 0 or budget exhausted: return
  for h in corpus_traverse(chunk, to="references", top_k=3) \
         | corpus_traverse(chunk, to="cited-by", top_k=3):
    if h.id in seen_chunks: continue
    seen_chunks.add(h.id); candidate_chunks.add(h)
```

Citation graph branches fast; keep `depth = 1` unless the editor
explicitly raises. Send `candidate_chunks` through the vetter.

**Citation-diversify (maturing slug).** ALWAYS walk BOTH directions with
`corpus_citation_walk` — outgoing `references` (older/seminal work the
page's sources cite) AND incoming `cited-by` docs (newer work citing
them); never one direction only. Bucket the resulting candidates by
their source doc's publication year (from doc metadata) into
seminal/older (<= p25), middle, and recent (>= p75), and keep candidates
from every bucket so the accepted evidence spans eras rather than
clustering on the highest-PageRank few. Budget and depth defaults are
unchanged.

**Stop reasons**: `budget_chunks_reached`, `depth_zero`,
`no_new_neighbours`, `ok`.

## P3 - semantic-boundary expansion

**Target**: ONE existing slug OR a slug pair (bridge mode; union both
notebooks' chunk sets). **Default**: `budget_chunks = 30`.

Find what a dossier is missing without leaving its topic. Use the
strongest 3-5 seed chunks (by evidence score) as semantic anchors.

```
P3(slug_or_pair, budget_chunks=30):
  seed_chunks = notebook(slug).covered_chunks
                (pair: notebook(a).covered_chunks | notebook(b).covered_chunks)
  seen_chunks = set(seed_chunks)
  anchors = top_k_by_evidence_score(seed_chunks, k=5)
  for anchor in anchors:
    for h in corpus_find(query=chunk_text(anchor), rank="semantic", top_k=10,
                         exclude_kinds=excluded_kinds):
      if h.id in seen_chunks: continue
      seen_chunks.add(h.id); candidate_chunks.add(h)
```

Bridge mode emits a `concept_suggestion` for a new connector concept
rather than appending to either endpoint's evidence.

**Stop reasons**: `budget_chunks_reached`, `no_new_neighbours`, `ok`.

## P4 - exact-term sweep

**Target**: ONE existing slug with stable aliases (3+). **Default**:
`budget_chunks = 20`. Looser accept threshold (exact-string co-occurrence
is its own structural signal).

Catch what semantic search misses (inconsistent acronyms, hyphenation).

```
P4(slug, budget_chunks=20):
  variants = card.aliases | generate_variants(card.page_id) \
           | canonical_acronyms(notebook(slug))
  seen_chunks = set(notebook(slug).provenance.covered_chunks)
  for v in variants:
    for h in corpus_find(query=v, text=True, top_k=10, exclude_kinds=excluded_kinds):
      if h.id in seen_chunks: continue
      seen_chunks.add(h.id); candidate_chunks.add(h)
```

`generate_variants`: plural<->singular (rule-based); hyphenated<->spaced;
acronym<->expansion ONLY when both forms already appear in the dossier
body or aliases. Send through the vetter with `accept_threshold =
tau_lite` (default 0.65 vs the usual 0.75).

**Stop reasons**: `budget_chunks_reached`, `no_new_neighbours`, `ok`.

## P5 - gap-explorer (coverage driver)

**Target**: literal `"global"` (bundle-wide residual). **Default**:
`budget_chunks = 20`. Fires every round; small budget keeps cost low.

Compute the residual chunk set (`corpus.chunks -
union(notebook.covered_chunks) - union(committed_page.cited_chunks)`),
rank by PageRank, and for each top chunk either attach it to a nearby
committed page or propose a new concept.

```
P5(budget_chunks=20):
  # residual = corpus chunk ids not yet covered. There is no single residual
  # field: derive it from the coverage CLI (covered set) and subtract from the
  # corpus chunk ids -- wikify work coverage --run <bundle> --corpus <corpus>
  # --format json (or call it twice around an attach to see the delta).
  residual = <uncovered chunk ids>()
  ranked = corpus_find(query="", by="chunk", rank="pagerank", top_k=budget_chunks)
  for chunk in ranked:
    if chunk.id not in residual: continue
    nearest = wiki_find(query=corpus_show(chunk, full=True).text, mode="semantic", top_k=3)
    attached = False
    for page in nearest:
      if relevance(chunk, page.slug) >= tau:
        emit_evidence_suggestion(page.slug, chunk); attached = True; break
    if not attached:
      emit_concept_suggestion(chunk)
```

`emit_evidence_suggestion` / `emit_concept_suggestion` write JSONL to
`work/inbox/evidence_suggestions.jsonl` and
`work/inbox/concept_suggestions.jsonl`; the editor's `work tend` consumes
them next round. P5 NEVER edits notebooks or evidence ledgers directly.
Every P5 `concept_suggestion` MUST carry `"origin": "gap_explorer"` and
the `chunk_id` it was proposed from; `work tend` gates these behind a
distinct-chunk support threshold so a one-off gap proposal does not
create an evidence-less stub.

Read `addressable_coverage_ratio` (covered / non-structural chunks),
never raw `chunk_coverage_ratio`: a raw ratio near 1.0 is structurally
impossible (references, captions, figures, tables, boilerplate are never
cited as evidence). Completeness, not a chunk-coverage target near 0.90,
governs when the loop stops.

**Stop reasons**: `residual_empty`, `budget_chunks_reached`,
`no_new_proposals`, `ok`.

## Return envelope (per target)

```json
{
  "target": "<slug-or-doc-or-pair>",
  "pattern": "P3",
  "appended_chunks": 6,
  "appended_concepts": 1,
  "covered_docs_delta": {"doc:abc": 3, "doc:def": 1},
  "covered_chunks_delta": ["chunk:abc__c0001", "..."],
  "exploration_log_entry": {"round": 4, "pattern": "P3", "target": "memristor", "accepted": 6},
  "stop_reason": "budget_chunks_reached" | "no_new_neighbours" | "depth_zero" | "ok",
  "tokens_in": 14000,
  "tokens_out": 320,
  "model_id": "claude-sonnet-4-6",
  "escalate": null
}
```

Do NOT write notebook frontmatter from inside a Task. Return
`covered_*_delta` in the envelope; the editor folds it in via
`notebook.merge_covered_docs` / `append_exploration_log` between Tasks
(avoids serialisation races).

## Escalate-block contract

`escalate` is `null` unless the Task hits a decision OUTSIDE its mandate:
concept-vs-evidence routing, kind/stencil choice, merge of near-duplicate
titles, or slug create/destroy. Then set it to `{"question": ...,
"context": ..., "options": [...]}` and STOP short of the call; the
top-tier editor adjudicates in CONSOLIDATE. Routine accept/reject of a
single chunk is the Task's own job and is NEVER escalated. Do not
silently invent a concept or re-route evidence on an ambiguous signal.

## Hard rules

- **No notebook frontmatter writes inside the Task** (return deltas).
- **One slug per Task** (except BRIDGE = slug pair). The editor's plan is
  slug-disjoint.
- **All accepted chunks go through `gather-evidence`** (or its
  `--from-ids` path) for the actual evidence append. This skill produces
  candidates only.
- **Respect `seen_chunks`.**
- **Escalate, don't guess.**

## gather-evidence vetter (when this Task drives the model-judged path)

Supervisor (sonnet) + haiku judge fleet, committing one evidence ledger
per slug. Non-negotiable:

- **Supervisor reads no chunk text.** Issue `corpus_find` with
  `include_text=False`; base args `by="chunk"`, `top_k=25`, `rank="all"`,
  `exclude_kinds=["references","acknowledgments","figure","table",
  "caption","boilerplate"]`. Chunk bodies live in judge contexts only.
- **Mandatory definition-hunters every run** (never conditional): per
  slug, `text=True, top_k=15` literal queries `'<title> is'`, `'<title>
  refers to'`, `'<acronym> stands for'` (skip the third when no acronym
  alias).
  These definition-hunters NEVER count against the query cap.
- **Query cap.** Cap the coverage queries at `2 * cluster_size + 2`; drop
  the lowest-value coverage queries (longest aliases, redundant
  doc-scoped queries) to fit. Definition coverage beats incrementally
  broader semantic recall. Mandatory definition-hunters are exempt.
- **Mark each query with its target slug(s).** Every coverage query is
  marked with the slug(s) it primarily aims at; each candidate-pool row
  carries a `targeted_slugs` field (alongside `chunk_id`, `doc_handle`,
  `section_type`, `score`, `preview`). Judges use `targeted_slugs` to
  route each chunk to the right slug(s).
- **Judges read every chunk** and return a verbatim quote per accepted
  chunk; supervisor spot-checks each accepted row is a verbatim,
  NFKC-normalised substring of the chunk `text` and drops failures
  (discipline guard). Judge batch size <= 8 (haiku breaks discipline
  above; `cluster_size >= 4` -> batch 4). Round-1 `failure_rate >= 0.30`
  -> switch subsequent batches to sonnet. `max_parallel_judges` default
  8; run judges in waves of `max_parallel_judges`.
- **One chunk may route to many slugs** (dedup by chunk_id within each
  ledger, keep higher score). Score = topic role per slug (ladder
  above). Cap any single `section_type` at half the quota per slug.
  `quota_per_slug` default 12; `max_query_rounds` default 3.
- **Gap-driven follow-up rounds.** After routing accepts, per slug queue
  targeted follow-up queries when there is a gap: `def_for: [<slug>]`
  empty (no definition evidence yet), low section diversity (< 3 distinct
  `section_type`s), or `distinct_docs < 5`. Aggregate fresh queries across
  slugs, dedup, cap to 2-4 new queries per round, and loop (issue queries
  -> judges -> route accepts) up to `max_query_rounds`. Stop when ANY: all
  slugs hit `quota_per_slug`, `max_query_rounds` exhausted, or the latest
  round added zero accepts to any slug.
- Person slugs: accept only chunks quoting ACTUAL contributions by that
  author; bylines alone do not count.
- **Gather-evidence judge-routed commit (this section's path).** Commit
  per slug only when `>= 6` accepts (minimum-viable bar) via
  `wikify work build-evidence` with a JSON ARRAY of
  `{chunk_id, score, quote}` objects on stdin via a `<<EOF` heredoc:

  ```bash
  wikify work build-evidence <slug> \
    --from-ids @- \
    --run <run> --corpus <corpus> --format json <<EOF
  [
    {"chunk_id": "...", "score": 1.00, "quote": "..."},
    ...
  ]
  EOF
  ```

  build-evidence verifies each quote is a literal substring and dedups by
  chunk_id. `< 6` accepts: do NOT commit; surface `stop_reason:
  "pool_exhausted"`. (The explore direct-accept path commits via `work
  add evidence --records` instead; see Shared mechanics.)
- **`build-evidence` does NOT emit `evidence_added`.** Surface every
  committed slug in the envelope so the editor emits the event (else the
  growth-stall maturity gate never advances).
- Scratch files go to temp (`$TEMP` / `<run>/scratch/`), NEVER to `src/`,
  `.claude/`, or any project source dir.

### gather-evidence return envelope

Final message is ONLY this JSON (no preamble/trailing prose). One
`results` entry per slug; narrative notes go inside per-slug `errors[]`.

```
{
  "cluster_size": int, "queries_issued": int, "unique_chunks_judged": int,
  "judge_calls": int, "judge_discipline_failures": int,
  "results": {
    "<slug>": {
      "appended": int, "distinct_docs": int, "iterations": int,
      "stop_reason": "quota_met" | "max_rounds" | "pool_exhausted" | "error",
      "definition_chunk": true | false, "score_tiers": int, "errors": []
    }
  }
}
```

The supervisor does NOT self-report tokens; the orchestrator reads
harness `<usage>` totals at each Task boundary.

## Sources distilled

- `explore/SKILL.md`
- `gather-evidence/SKILL.md`
- `gather-evidence/references/return-envelope.md`
- `../reference/references/exploration/patterns.md`
- `../reference/references/exploration/maturity.md` (definition-gate +
  score-ladder rules)
