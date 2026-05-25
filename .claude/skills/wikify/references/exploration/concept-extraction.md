# Concept Extraction

Concept extraction turns observed corpus text into candidate wiki pages.
It is prompt/reference material, not an exploration strategy.

## Candidate Fields

- `title`: natural Wikipedia-style title.
- `aliases`: abbreviations and alternative names.
- `kind`: `article` or `person`.
- `category`: phenomenon, method, material, device, theory, metric,
  organization, or other.
- `quote`: verbatim evidence span from the observed text.
- `definition`: one-sentence meaning.
- `summary`: what the observed text says about the concept.
- `parameters`: quantitative values with units and conditions.
- `mechanisms`: short mechanism phrases.
- `relationships`: target concept, relation, evidence.
- `equations`: formulas or equations present in supplied context.
- `evidence_figures`: figure ids directly discussed.
- `cited_refs`: citation ordinals directly relevant to the concept.
- `seed_doc_handles`: corpus doc handles (e.g. `doc:abc12345`) the
  extractor saw and judged relevant to this concept. Must be drawn ONLY
  from the doc handles supplied in the sampled bodies; do not invent
  handles. Used downstream as a precision prior for evidence gathering.
  An empty list signals "no high-confidence seeds among the sample".
  This is a typed field on `ExtractedConcept` (not JSONL-only) and is
  persisted on the work card by `wikify work tend`.
- `confidence`: extracted, inferred, or ambiguous.
- `score`: 0.0-1.0.

## Rules

- Reuse a canonical title if the workflow supplies one.
- The quote must be a verbatim substring of the observed text.
- Person candidates use `kind="person"` and omit technical parameters.
  When the candidate resolves to a corpus author handle, include the
  handle as an alias in the exact form `author:<key>`. Do not invent an
  author key; add the alias only after `corpus find --by author` or
  `corpus show author:<key>` has resolved it.
- Prefer fewer high-value concepts over noisy phrase extraction.
- Flag merge/split ambiguity for the workflow rather than inventing a
  final ontology decision.

## Map-reduce orchestration

Concept extraction across multiple sampled documents runs as a
map-reduce. The map step is one extractor Task per sampled document
(haiku tier by default); the reduce step is one Task or the
orchestrator itself that consolidates across docs.

### Map (per-doc, parallel)

Each map Task:

- binds the MCP session
  (`mcp__wikify__context_set(corpus_path=corpus, bundle_path=run)`);
- fetches its assigned doc body via
  `mcp__wikify__corpus_show(handle=doc_handle, include_text=True, mode="full")`
  — body text stays in the subagent's context, never the orchestrator's;
- emits 0-8 concept candidates from THIS DOC only, with the candidate
  fields above plus `doc_handle` provenance;
- does NOT set a corpus-wide score (cannot see the corpus);
- returns ONLY a JSON array of candidates (≤400 tokens) to the parent.

Spawn all map Tasks in parallel. Wall time = max(map-task), not sum.

### Reduce (orchestrator or one Task)

Collect every map Task's JSON array, then:

- dedupe by canonical key (lowercased `title` ∪ any matching alias);
- count `doc_frequency` = number of sampled docs that emitted this
  concept;
- score by `doc_frequency / n_sampled_docs` (a real centrality
  signal, not a per-doc guess);
- drop concepts with `doc_frequency == 1` unless the single map Task
  supplied a strong definition-style quote;
- flag merge ambiguity when near-duplicate titles share aliases or
  quotes;
- trim to the workflow target by descending `doc_frequency`,
  breaking ties on title length (prefer the shorter, more general
  title);
- write to a STAGING path OUTSIDE `work/inbox/`
  (e.g. `bundles/<bundle>/scratch/concepts_staging.jsonl`).

Then the workflow appends staging → inbox via
`wikify work add feedback concept --record <staging-path>`. Writing
directly to `work/inbox/concept_suggestions.jsonl` and then
`--record`-ing the same path doubles records.

### Telemetry

Record both stages via `wikify run record-call --stage extract`:
`--role extractor-map` per map Task, `--role extractor-reducer` once
for the reduce.
