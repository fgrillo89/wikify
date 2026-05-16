# Phase B — Interactive evidence-gathering skill (2026-05-17)

Supersedes `dossier_noise_audit_2026_05_16.md`. Phase A′ (presentation +
boilerplate filter cleanup) lands on `wikify-pr72-followups`; this is
the larger Phase B work that turns evidence gathering into an LLM-vetted
loop.

## Goal

Replace the deterministic one-shot `wikify work build-evidence` with a
vetter-driven loop run by a cheap (haiku-class) agent. The vetter
assembles a clean `evidence.jsonl` per slug; the writer never sees the
vetting machinery — only the rendered dossier.

## Two-role separation

- **Vetter** (haiku-class) — runs the loop. Knows the corpus tooling.
  Outputs a committed `evidence.jsonl`. That's all.
- **Writer** (sonnet/opus-class) — receives `dossier.md` only. Doesn't
  know vetting happened. No "ignore irrelevant chunks" caveat in the
  prompt because there shouldn't be irrelevant chunks left.

The committed `evidence.jsonl` is the contract between them.
`wikify draft build` renders the dossier from it.

## CLI additions — two flags, justified by round-trip reduction

1. **`wikify corpus find … --with-text`** — inline chunk text in the
   JSON output. Without it the vetter needs N follow-up `corpus show`
   calls per batch; with it, one call returns everything the vetter
   needs to judge.

2. **`wikify work build-evidence <slug> --from-ids <a,b,c,…>`** —
   commit-only mode. Skips the proposing phases, takes a curated
   chunk-id list, validates via `EvidenceRecord.model_validate`, and
   appends to `evidence.jsonl`. The skill builds the list in-context;
   one commit call at the end.

No new commands. Everything else (`--in-doc`, `--rank all`,
`--exclude-kind`, work card reads, cluster.json reads) already exists.

## Skill

Location: `.claude/skills/wikify-gather-evidence/SKILL.md`. Frontmatter
declares haiku-class. Inputs: slug, run, corpus, quota (default 16),
max query rounds (default 3).

Loop (in skill prose, not Python):

1. Read `work/concepts/<slug>/work.md` → title, aliases, seeds, kind.
2. Read `work/clusters.json` → sibling slugs in the same cluster.
3. Build candidate pool over queries:
   - `title` (verbatim)
   - each alias
   - per-seed scoped find: `corpus find <title> --in-doc <handle> --rank all`
   - optionally, page-kind-aware variant queries (e.g. for a technique
     concept: `<title> process`, `<title> precursor`)
   - all calls go through
     `wikify corpus find <q> --rank all --top-k 25 --with-text
      --exclude-kind references,acknowledgments,figure,table,caption,boilerplate
      --format json`
4. Vet each candidate (in context):
   - relevant to slug OR a cluster sibling?
   - not pure author byline / editorial / DOI / citation header /
     acknowledgments-style?
   - not a near-duplicate of an already-accepted chunk's argument
     (semantic, not byte)?
   - prefer underused sections so methods/results aren't drowned out by
     intros from every seed
5. If quota met OR max iterations reached → stop.
6. Commit:
   `wikify work build-evidence <slug> --from-ids <accepted_ids>`.
7. Optional sanity: re-render dossier via existing `wikify draft build`
   and spot-check the section types covered.

Telemetry: the vetter's own Agent-tool call gets recorded via
`wikify run record-call` (closes the companion
`writer_call_telemetry_2026_05_16.md` ticket — the vetter IS the kind
of agent invocation that needs metering).

## Acceptance

A regenerated ALD dossier where, against the 14-record baseline that
had ~5 high-signal chunks:

- ≥10 of ≤16 records are concretely about ALD-as-a-process OR about a
  cluster sibling (oxygen-vacancy / conductive-filament / HfO2 /
  artificial-synapse)
- 0 editorial / citation-header / pure-author-byline chunks
- methods + results sections represented, not dominated by introductions
- vetter telemetry recorded; reject reasons traceable in the trace

## Open design questions

- **Vetter prompt strictness on cluster siblings.** Probably "accept if
  a writer covering the slug *or* any sibling would cite this chunk".
- **Quota — fixed 16 vs page-kind-dependent.** Technique pages may need
  more methods coverage; person pages may need fewer.
- **Reject memory.** Per-run only — once `evidence.jsonl` is committed
  the loop is done; the rejects don't need to outlive the loop.
- **Zero on-topic seed.** If a seed contributes nothing after vetting,
  drop it from the iteration plan rather than re-querying.
- **Can the vetter add seeds?** No for Phase B. Seeds come from the
  extractor (`cluster-concepts` upstream).
- **Vetter vs writer model choice.** Vetter on haiku unless the page is
  a stub concept with very thin evidence — TBD.

## Implementation order

1. CLI flag 1: `corpus find … --with-text`. Test inline text in JSON.
2. CLI flag 2: `build-evidence --from-ids …`. Test atomic append.
3. Skill skeleton + prose for the vetter loop.
4. Dry run on `atomic-layer-deposition` against the smoke corpus until
   acceptance criteria pass.
5. Run across all article slugs in the smoke bundle; compare
   pre/post-vetting record counts and noise rates.
6. Reviewer agent; commit; PR; merge.

## Out of scope (this phase)

- Better chunking (e.g. splitting author-byline from abstract). Lives
  upstream in the parser. Separate ticket if needed.
- Vetter-driven seed expansion (vetter adding to `seed_doc_handles`).
- Cross-bundle reject memory.
- Section-type uplift/downweight at retrieval time (rejected — intros
  should appear, just naturally via `ORDER BY ord`).
