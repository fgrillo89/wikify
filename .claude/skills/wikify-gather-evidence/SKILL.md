---
name: wikify-gather-evidence
description: Vetter-driven evidence loop. Use a cheap (haiku-class) agent to assemble a clean, on-topic evidence.jsonl for one concept slug by issuing scoped corpus-find queries, judging each candidate chunk in-context, and committing the accepted ids in one CLI call. Use when a concept needs evidence gathered or refreshed before a writer agent renders the dossier.
allowed-tools: Bash(wikify *)
---

# wikify-gather-evidence

This workflow runs a vetter loop separately from the writer loop. The
vetter knows the corpus tooling and produces a curated
`evidence.jsonl`. The writer never sees the vetting machinery — it
receives the rendered `dossier.md` only. The committed `evidence.jsonl`
is the contract between them.

Run this skill on a haiku-class model. The reasoning is shallow
per-candidate (accept/reject + one-line reason); cost should track
candidates seen, not page complexity.

## Inputs

- `slug` (required) — concept slug under `work/concepts/<slug>/`.
- `run` (required) — bundle path passed to every CLI call.
- `corpus` (required) — corpus path passed to every CLI call.
- `quota` (default 16) — stop after this many records accepted.
- `max_query_rounds` (default 3) — stop after this many candidate
  batches return nothing new.

## Step 1: read the work card and the cluster

```bash
wikify work show <slug> --run <run> --format json
wikify work cluster-concepts --run <run> --format json
```

From the card pull `title` (= `page_id`), `kind`, `aliases`, and
`seed_doc_handles`. From the cluster output find the cluster that
contains `<slug>` and remember the sibling slugs — chunks useful to a
sibling are acceptable too.

If the card has no body and no `seed_doc_handles`, the extractor never
gave the concept a prior; rely on the title + aliases queries below.

## Step 2: build the candidate pool

Issue one `corpus find` per query and one per seed doc. Every call
returns JSON with inline chunk text so the vetter can judge without
follow-up `corpus show` round-trips.

Base flags (apply to every call):

```
--rank all --top-k 25 --with-text
--exclude-kind references --exclude-kind acknowledgments
--exclude-kind figure --exclude-kind table
--exclude-kind caption --exclude-kind boilerplate
--corpus <corpus> --run <run> --format json
```

Queries to issue:

```bash
wikify corpus find "<title>"            <base-flags>
wikify corpus find "<alias-1>"          <base-flags>
wikify corpus find "<alias-2>"          <base-flags>
# ... one per alias, including author:<key> aliases for person pages.

# Per-seed scoped find: pulls the title's best chunks from one paper.
wikify corpus find "<title>" --in-doc <seed-handle>  <base-flags>
```

For technique / process pages, an additional kind-aware query often
surfaces methods sections that the title query misses (only run this
when the title-query batch is light on methods/results coverage):

```bash
wikify corpus find "<title> precursor"  <base-flags>
wikify corpus find "<title> process"    <base-flags>
```

Merge the batches in-context by `chunk_id`. Drop duplicates. Each
candidate has `text`, `section_type`, `doc_handle`, `citation_count`,
`modes`, and `score` available — that is everything the vetter needs.

## Step 3: vet each candidate

For each unique candidate, accept or reject with a one-line reason.
Accept iff **all** of:

- The chunk is on-topic for `<slug>` OR genuinely useful for a cluster
  sibling. "Could a writer covering this slug or any sibling cite this
  chunk?" If no, reject.
- The chunk is not pure metadata: no pure author byline, no editorial
  header, no DOI banner, no citation header, no acknowledgments-style
  text. The CLI already drops boilerplate-flagged and never-cite-match
  chunks at commit; reject the survivors that read as metadata too.
- The chunk is not a near-duplicate of an already-accepted chunk's
  argument. Semantic duplication, not byte duplication — two chunks
  making the same point in different words count as one.
- The chunk does not drown out underused section types. Track accepted
  `section_type` counts; if the pool already has 4 introductions and
  zero results, prefer methods/results candidates over yet another
  introduction. Keep at least one introduction; cap any single
  section_type at roughly half the quota.

Maintain two ledgers in-context: `accepted_ids` (committed once at the
end) and `rejected` (slug + chunk_id + one-line reason; per-run only,
never persisted).

### Section-mix target

Aim for evidence that covers at least introduction + methods + results
across the accepted set — not 14 introductions. After each accept,
scan the accepted-so-far set's `section_type` distribution; when
choosing between two marginal candidates, prefer the one whose
`section_type` is underrepresented. An accepted set with five
introductions and zero methods is a worse dossier than one with three
introductions, one methods, and one results.

### Accept / reject examples

The four examples below are real chunks from the ALD baseline run.
They are calibrated against the same accept rule. Use them to anchor
borderline calls.

**Accept — on-topic, methods-grade ALD discussion** (Goul 2022, e7-equivalent).
Chunk text opens "Continuous device downsizing and circuit complexity
have motivated atomic-scale tuning of memristors. Herein, we report
atomically tunable Pd/M1/M2/Al ultrathin (<2.5 nm M1/M2 bilayer oxide
thickness) memristors using in vacuo atomic layer deposition by
controlled insertion of MgO atomic layers …". Verdict: accept.
Reason: explicit in vacuo ALD process detail, on-topic for the ALD
page.

**Reject — editorial header** (Kumar 2025, e4-equivalent). Chunk text
is "EDITED BY / Carlo Ricciardi, / Polytechnic University of Turin,
Italy / REVIEWED BY / Itir Koymen, …". Verdict: reject. Reason: pure
editorial metadata; survives the boilerplate filter only because the
upstream flag missed it — vetter must catch it.

**Reject — author byline + abstract** (Li 2018, e1-equivalent). Chunk
text is "Can Li 1 , Daniel Belkin1,4, Yunning Li 1 … Abstract—Memristors
with tunable non-volatile resistance states offer the potential for
in-memory computing that mitigates the von-Neumann bottleneck. We build
a large scale memristor array by integrating a transistor array with
Ta/HfO2 memristors …". Verdict: reject. Reason: byline-and-abstract
chunk where the ALD reference is incidental; no concrete ALD claim a
writer could cite. Accept the same paper's later body chunk that
describes the HfO2 ALD step explicitly (e3-equivalent), not this one.

**Marginal accept — device fab chunk that names ALD as one step**
(Gao 2014, e9-equivalent). Chunk discusses 3D oxide-based memristor
fabrication, with ALD referenced as the HfO2 deposition step among
others. Verdict: accept when a hafnium-oxide cluster sibling is in
scope. Reason: the cluster-relevance test — "could a writer covering
a sibling cite this chunk?" — passes; the same chunk would be a
borderline reject if ALD were the only concept in the cluster.

## Step 4: stop conditions

Stop when **any** of:

- `len(accepted_ids) >= quota`.
- The last `max_query_rounds` candidate batches added zero new accepted
  records.
- The query plan is exhausted (every title / alias / seed scoped find
  has been issued and merged) and no new acceptances are landing.

If a particular seed contributes zero accepted chunks, drop it from
further iteration — do not re-query it.

## Step 5: commit

One call at the end. The CLI re-validates each id against the
boilerplate flag, never-cite regex, and min-chunk-chars filter before
appending; ids already in `evidence.jsonl` are skipped with
`rejected_already_committed`:

```bash
wikify work build-evidence <slug> \
  --from-ids <id-1>,<id-2>,...,<id-N> \
  --run <run> --corpus <corpus> --format json
```

The output is `{ok, concept, appended, distinct_docs, stats}`. If
`appended` is materially lower than `len(accepted_ids)`, inspect
`stats` to see which filter rejected what — never re-commit without
addressing the cause.

The vetter does not write `evidence.jsonl` directly. Only
`build-evidence` writes that file.

### Same-slug concurrency

If another agent might be vetting the same slug concurrently, the
caller must hold the concept claim (`wikify work claim <slug>`) or
the orchestrator must serialize per slug. The CLI does not lock
`evidence.jsonl` atomically across multiple writers: `wikify work
build-evidence` reads the ledger, computes a new set, and appends —
rapid concurrent commits could interleave records and double-write a
chunk_id. Single-writer-per-slug is the contract.

## Step 6: sanity-check the dossier

After commit, regenerate and skim the dossier:

```bash
wikify draft build <slug> \
  --task create --corpus <corpus> --run <run> \
  --model-id <writer-model> --tier M --with-adjacent
```

Open the resulting `work/concepts/<slug>/dossier.md`. Verify:

- evidence reads as on-topic;
- methods + results are represented, not buried by introductions;
- no obvious noise (byline / DOI / acknowledgments) survived the
  filters;
- distinct docs and total active records are within the quota window.

If a problem is visible, mark the bad ids as `status="archived"` via a
follow-up `work add evidence` call rather than mutating the JSONL by
hand. Then re-run this skill to top up.

## Step 7: telemetry

The vetter is itself a metered agent invocation. After the vetter's
Agent-tool call returns token usage, record the call:

```bash
wikify run record-call --help        # confirms the current signature
wikify run record-call --run <run> --role vetter \
  --model-id <vetter-model> --tier S \
  --tokens-in <n> --tokens-out <n> --stage evidence
```

If the controlling workflow already records the call on the agent's
behalf, skip this step.

## Hard rules

- Do not edit `work/concepts/<slug>/evidence.jsonl` directly.
- Do not bypass `--with-text` and ask for chunk bodies via
  `corpus show` — the round-trip cost is the reason the vetter loop
  exists.
- Do not accept a chunk whose only relevance is keyword overlap; the
  vetter is the layer that catches "matches the query, is not useful".
- Do not add seeds to the work card. Seeds come from the extractor
  (`cluster-concepts` upstream).
- Do not invent biography for person pages. Person pages still need
  evidence chunks that quote actual contributions — author bylines
  alone do not count.

## Does not do

- Pick the slug to gather for (the controlling workflow chooses).
- Decide whether the page is ready to write (the writer workflow
  judges).
- Refine or rewrite committed pages (use `wikify-refine`).

## References

- `../wikify-search-corpus/SKILL.md` — corpus CLI surface.
- `../wikify-bundle/SKILL.md` — bundle / work state mechanics.
- `../wikify/references/exploration/workflow-contracts.md` — slug,
  card, and evidence contracts.
