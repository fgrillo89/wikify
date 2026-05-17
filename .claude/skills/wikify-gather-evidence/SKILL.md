---
name: wikify-gather-evidence
description: Vetter-driven evidence loop. Use a sonnet-class agent to assemble a clean, on-topic evidence.jsonl for one concept slug by issuing scoped corpus-find queries, judging each candidate chunk in-context, and committing the accepted ids in one CLI call. Use when a concept needs evidence gathered or refreshed before a writer agent renders the dossier.
allowed-tools: Bash(wikify *)
---

# wikify-gather-evidence

You assemble `evidence.jsonl` for one concept slug. Read every
candidate chunk, judge it on what it says, and commit the accepted
ids in one CLI call.

Run this skill on a sonnet-class model. Haiku partially follows the
score / quote / no-false-positive discipline at small accept sets but
cuts corners around quota 16 — empirically observed on the ALD smoke
slug: haiku skipped `quote` and `score` fields in the JSON envelope
and admitted byline / references-list chunks. Sonnet reliably honors
all three rules. Drop to haiku only when the run is genuinely
cost-bound and the orchestrator can audit the resulting envelope.

## Inputs

- `slug` (required) — concept slug under `work/concepts/<slug>/`.
- `run` (required) — bundle path passed to every CLI call.
- `corpus` (required) — corpus path passed to every CLI call.
- `quota` (default 16) — stop after this many records accepted. Land
  fewer if you cannot find more on-topic chunks; never pad with weak
  acceptances.
- `max_query_rounds` (default 3) — max gap-driven query iterations
  (Step 4) after the initial plan.

## Non-negotiable rules

1. **Read every chunk.** Every accept and every reject decision row
   MUST include a verbatim quote of one sentence from the chunk's
   `text`. No quote means you did not read it.
2. **Do not rank by metadata.** `citation_count`, `score`,
   `semantic_score`, `bm25_score` are diagnostic. Judge on text.
3. **Iterate queries.** After the first vetting round, identify
   sub-topics an encyclopedia article on `<slug>` needs but your
   accepted set lacks. Craft 2–4 fresh queries to fill those gaps.
   Repeat until quota or `max_query_rounds` exhausts.
4. **Score = topic role.** Set per-chunk `score` to reflect its
   narrative role in the encyclopedia article so the dossier orders
   chunks the way a writer should read them:
   - 1.00 — definition chunk ("<title> is …")
   - 0.95 — core mechanism / principle
   - 0.85 — materials systems / process variants
   - 0.75 — applications / device-level examples
   - 0.60 — marginal but cluster-useful chunks (sibling-relevant)
   Definitions land first in the dossier; applications last. Do not
   set 1.0 for everything — that re-creates the alphabetical tie-break
   problem.
5. **Definition chunks are gold.** A chunk that opens with
   "`<title>` is …" / "`<title>` refers to …" / "`<title>` (`<acronym>`)
   is a …" is the highest-value evidence for an encyclopedia page.
   Hunt for at least one such chunk in Step 4 if Step 2 didn't return
   one.

## Step 1: read the work card and the cluster

```bash
wikify work show <slug> --run <run> --format json
wikify work cluster-concepts --run <run> --format json
```

Pull `title`, `kind`, `aliases`, and `seed_doc_handles` from the card.
From the cluster output, find the cluster containing `<slug>` and
remember the sibling slugs — chunks useful to a sibling are
acceptable too.

## Step 2: initial candidate pool

Issue one `corpus find` per query and one per seed. Base flags
(apply to every call):

```
--rank all --top-k 25 --with-text
--exclude-kind references --exclude-kind acknowledgments
--exclude-kind figure --exclude-kind table
--exclude-kind caption --exclude-kind boilerplate
--corpus <corpus> --run <run> --format json
```

Queries:

```bash
wikify corpus find "<title>"                 <base-flags>
wikify corpus find "<alias-1>"               <base-flags>
wikify corpus find "<alias-2>"               <base-flags>
# ... one per alias

wikify corpus find "<title>" --in-doc <seed-handle>  <base-flags>
# ... one per seed_doc_handle
```

Merge batches in-context by `chunk_id`. Drop duplicates.

### Beyond title + aliases + per-seed

The full `wikify corpus find` surface — use the right primitive for the gap:

| primitive | when to use |
|---|---|
| `corpus find --text "<exact phrase>"` | Literal substring grep, no semantic dilution. Best for definition hunting: `--text "atomic layer deposition is"`, `--text "refers to"`, `--text "defined as"`. The semantic ranker often dilutes these exact phrasings. |
| `corpus find --rank pagerank --by paper "<title>"` | Rank PAPERS by in-corpus PageRank. Surfaces foundational works on the topic that aren't necessarily the highest semantic match. Use to find which papers a writer should cite as anchors. |
| `corpus find --rank citation_count --by paper "<title>"` | Rank papers by external citation count. Use to surface seminal references. |
| `corpus sample --strategy diverse --max <N> --corpus <c>` | When `seed_doc_handles` is thin or absent, get a diverse seed set across the corpus by topical coverage. |
| `corpus citation-walk <doc_handle>` | Given an accepted seminal paper, walk to what it cites and what cites it — surfaces the surrounding scholarly conversation, often containing the technique's origin papers. |
| `corpus similarity-walk <chunk_handle>` | Given an accepted chunk, surface other chunks semantically nearby — the "find more like this" pattern after a strong accept. |

Mix these into Step 4 when the title + alias + seed plan leaves gaps.

## Step 3: vet each candidate

For every unique candidate emit one decision row:

```
[accept|reject] chunk_id=<id> doc=<doc_handle> section=<section_type>
  quote="<one verbatim sentence from the chunk text>"
  reason=<short phrase>
```

The quote MUST appear literally in the chunk's `text`. This is the
discipline that enforces reading.

Accept iff ALL of these hold (verify each in the quote and reason):

- **Substantive claim about `<slug>` or a cluster sibling.** The
  quote contains a concrete fact, definition, mechanism, parameter,
  process step, or result tied to the concept. Passing keyword
  mentions don't count: "deposited by ALD at 250 °C" IS on-topic for
  ALD; "this work is in neuromorphic computing where techniques like
  ALD are common" is NOT.
- **Not pure metadata.** No author bylines, editorial headers, DOI
  banners, citation headers, acknowledgments paragraphs, copyright
  notices.
- **Not a references list disguised as body.** If the chunk text
  contains 3+ numbered citation entries (`(N) Author, Initial.`) or
  opens with one, reject as `looks like references list`. The
  section classifier sometimes misses these.
- **Not a near-duplicate of an already-accepted chunk's argument.**
  Two chunks making the same point in different words count as one.
- **Helps the section mix.** Track accepted `section_type` counts.
  Cap any single section_type at roughly half the quota. Keep at
  least one introduction. Methods and results should be represented
  if available.

When choosing between two on-topic candidates, prefer the one that:
- contains a definition-style sentence ("`<title>` is …"),
- covers an underrepresented sub-topic (Step 4 list),
- or covers an underused `section_type`.

Maintain two ledgers in-context: `accepted` (decision rows for
commits) and `rejected` (per-run only, never persisted).

### Calibration examples (well-formed decision rows)

Real chunks from the ALD baseline. Copy this format.

**Accept — definition chunk** (Gou 2024):
```
accept chunk_id=...Gou...aa932d61 doc=doc:... section=introduction
  quote="Atomic layer deposition (ALD) is a technique used to
         manufacture ultra-thin films that deposit material layer by
         layer by chemical reaction by alternately introducing
         different chemical vapor phase precursors on the substrate
         surface."
  reason=textbook ALD definition; gold for encyclopedia opening
```

**Accept — process detail** (Goul 2022):
```
accept chunk_id=...Goul...c0001_bf52b61a doc=doc:0b46c4a097e4
  section=body
  quote="Herein, we report atomically tunable Pd/M1/M2/Al ultrathin
         memristors using in vacuo atomic layer deposition by
         controlled insertion of MgO atomic layers into pristine
         Al2O3 atomic layer stacks."
  reason=concrete in-vacuo ALD process used to tune memristor stack
```

**Reject — editorial header** (Kumar 2025):
```
reject chunk_id=...Kumar...c0000_bce549e2 doc=doc:507844b996c7
  section=body
  quote="EDITED BY Carlo Ricciardi, Polytechnic University of Turin,
         Italy REVIEWED BY Itir Koymen, TOBB University of Economics
         and Technology, Türkiye"
  reason=Frontiers editorial board metadata, zero ALD content
```

**Reject — byline plus abstract, no ALD claim** (Li 2018):
```
reject chunk_id=...Li...c0000_238acd1a doc=doc:88ba30b3ca12
  section=body
  quote="We build a large scale memristor array by integrating a
         transistor array with Ta/HfO2 memristors that have stable
         multilevel resistance states and linear IV characteristic."
  reason=abstract about memristor arrays; ALD never mentioned in
         this chunk even though paper uses ALD elsewhere
```

**Anti-pattern — shortcut by ranking, not reading:**
```
accept chunk_id=... doc=... section=body
  quote=N/A
  reason=high citation count (200), keep
```
Invalid. No quote means no reading. Reject this shape of decision
from yourself.

## Step 4: iterative query crafting

After the initial batch is vetted, **do not commit yet**. Inspect
your `accepted` ledger:

- What sub-topics are now covered? Make a short list from the quotes.
- What sub-topics that an encyclopedia article on `<slug>` would
  obviously need are **missing**?
- Is there a definition chunk? If not, prioritise finding one.

Craft 2–4 fresh queries in your own words to close those gaps.
Issue them with the same base flags as Step 2 and merge results
into the candidate pool. Vet the new candidates (Step 3 format).

### Sample query plans

**Definition-hunting queries (always try one if you don't have a
definition chunk yet)**:

```
"<title> is"
"<title> refers to"
"<title> definition"
"<acronym> stands for"
```

**Process / technique concepts** (ALD, CVD, sputtering, photolithography):

| missing sub-topic | sample query |
|---|---|
| precursor chemistry | `"<title> precursor"`, e.g. `"TMA water"` |
| half-cycle / self-limiting growth | `"<title> half cycle"`, `"self-limiting reaction"` |
| growth-per-cycle / temperature window | `"<title> growth per cycle"`, `"<title> temperature window"` |
| plasma vs thermal variants | `"plasma-enhanced <title>"`, `"thermal vs plasma <title>"` |
| materials systems | `"<title> HfO2"`, `"<title> Al2O3"` |
| conformality / aspect ratio | `"<title> conformality"`, `"high aspect ratio <title>"` |
| nucleation / interface | `"<title> nucleation"`, `"incubation layer <title>"` |
| applications | `"<title> memristor"`, `"<title> gate dielectric"` |

For other concept kinds (device, material, person) construct
analogous gap-driven queries. The point: do not stop at title +
aliases. Craft queries from the holes in what you've accepted.

## Step 5: stop conditions

Stop when ANY of:

- `len(accepted) >= quota`.
- You have run `max_query_rounds` gap-driven query iterations and the
  latest round added zero accepts.
- Every reasonable query has been issued and no new ids appear after
  dedup.

If a seed contributes zero accepted chunks across the loop, drop it
from further iteration.

**Land fewer than quota if necessary.** Ten high-quality on-topic
chunks beats sixteen padded with marginal ones.

## Step 6: commit

The vetter commits via `--from-ids @-` reading JSON from stdin, so it
can attach per-chunk `score` (topic role) and `quote` (the on-topic
sentence selected during vetting):

```bash
wikify work build-evidence <slug> \
  --from-ids @- \
  --run <run> --corpus <corpus> --format json <<'EOF'
[
  {"chunk_id": "...Gou...aa932d61", "score": 1.00, "quote": "Atomic layer deposition (ALD) is a technique used to manufacture ultra-thin films..."},
  {"chunk_id": "...Zhang...81a0f480", "score": 0.95, "quote": "When all possible reaction ligands are occupied, the reactions will not take place anymore..."},
  {"chunk_id": "...Porro...66e71bc3", "score": 0.85, "quote": "iron oxide (Fe2O3) thin films grown by atomic layer deposition (ALD) using ferrocene as iron precursor..."}
]
EOF
```

The heredoc redirection (`<<'EOF'`) is part of the `wikify` invocation, so
this command stays inside the skill's `allowed-tools: Bash(wikify *)`
allowlist. Do not pipe via `cat <<EOF | wikify ...` — that introduces
`cat` as a separate shell command outside the allowlist and the commit
step will be blocked.

The supplied `quote` replaces the default `text[:400]` truncation so
the dossier displays the vetter's chosen on-topic sentence instead of
the chunk head (which is often a byline). The CLI verifies each quote
appears literally in the chunk text; missing or fabricated quotes get
rejected with `rejected_quote_not_in_chunk` in the stats.

The CSV form (`--from-ids "id1,id2,id3"`) still works as a fallback;
all records get the default score=1.0 and the text[:400] quote.

Output: `{ok, concept, appended, distinct_docs, stats}`. The CLI
re-validates each id against boilerplate / never-cite / min-chars /
structural-kind filters before appending; ids already in
`evidence.jsonl` are skipped with `rejected_already_committed`. If
`appended` is materially lower than `len(accepted)`, inspect `stats`
to see which filter rejected what — never re-commit without
addressing the cause.

### Same-slug concurrency

If another agent might be vetting the same slug concurrently, the
caller must hold the concept claim (`wikify work claim <slug>`) or
the orchestrator must serialize per slug. `evidence.jsonl` is not
locked atomically across writers; concurrent commits can interleave.

## Step 7: return a terse summary to the caller

**Your final response MUST be ONLY this JSON object, ≤300 tokens
total.** Do not include decision rows, candidate dumps, full quotes,
or narrative report in the final response. They belong in your
internal reasoning, not in the message that returns to the parent
agent. Returning long reports to the parent destroys its context
budget and is the reason this contract exists.

```json
{
  "slug": "<slug>",
  "appended": <int>,
  "distinct_docs": <int>,
  "iterations": <int 1..max_query_rounds>,
  "stop_reason": "quota_met" | "max_rounds" | "pool_exhausted" | "error",
  "definition_chunk": true | false,
  "score_tiers": <int distinct score values across the committed set>,
  "errors": []
}
```

If a step errored (commit rejected ids, malformed JSON, CLI failure),
populate `errors` with one-line strings and set `stop_reason` to
`"error"`. The caller decides whether to retry.

Sanity-check and telemetry are the orchestrator's job, not yours. The
orchestrator regenerates the dossier with `wikify draft build` and
records the vetter call via `wikify run record-call` after you return.

## Hard rules

- Read every chunk. Decision row without a verbatim quote is
  invalid; discard and re-vet.
- Do not rank candidates by `citation_count`, `score`,
  `semantic_score`, or `bm25_score`. Judge on text.
- Set per-chunk score to topic role (1.0 definition, 0.95 mechanism,
  ...). Uniform 1.0 scores defeat the dossier ordering.
- Do not stop at title + alias queries. Step 4 gap-driven querying
  is mandatory unless quota is met on the first round.
- Hunt for one definition-style chunk per slug.
- Do not edit `work/concepts/<slug>/evidence.jsonl` directly.
- Do not bypass `--with-text` and use `corpus show` per candidate —
  the round-trip cost defeats the loop.
- Do not accept a chunk whose only relevance is keyword overlap.
- Do not pad to quota with weak acceptances; land fewer if the
  corpus does not offer more on-topic chunks.
- Do not add seeds to the work card; seeds come from the extractor.
- Person pages need evidence chunks that quote actual contributions;
  author bylines alone do not count.
- Final response is the Step 7 JSON, ≤300 tokens. Returning a long
  report to the parent agent inflates its context budget and breaks
  baseline at scale.
