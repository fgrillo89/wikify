---
name: wikify/workflows/run-baseline
description: Produce a wiki bundle using the abstract-first source-grounded baseline strategy over an explicit session and CLI-backed deterministic tools.
---

# run-baseline

## Purpose

Produce a wiki bundle from an ingested corpus using the deterministic
abstract-first baseline strategy: PageRank + submodular seed selection,
tier-S extract over seed chunks, deterministic evidence retrieval,
tier-M write per page. Lowest autonomy, highest determinism — the
canonical reference workflow for the skill / CLI / session
architecture.

## Inputs

- `<bundle>`: wiki bundle root (will be created if absent)
- `<corpus>`: ingested corpus root (produced by `wikify ingest`)
- `--budget-target N`: haiku-equivalent token ceiling (optional)

## Required session state

A v1 `SessionV1` at `<bundle>/_session/session.json` with:

- `strategy: "baseline"`
- `stages: {seed_selection, extract, write}` — workflow updates these
  transitions
- `pages: [...]` — appended after canonicalisation of extracted candidates
- `config: {baseline_write_fraction, abstract_fraction, top_k,
  default_tiers}` — defaults from `BaselineConfig`
- `budget.haiku_eq_target` — enforced as a soft ceiling

Schema source of truth: `src/wikify/session.py::SessionV1`.

## Commands

All CLI subcommands documented in `reference/cli-tool-surface.md`. Per-atom
mapping in `reference/atoms.md`.

## Model steps

Two model-calling phases, invoked via Claude Code Task subagents:

1. **Seed extract** (tier S) — one Task per seed chunk (or per batch via
   `extract_many`). Input: chunk text + canonical-titles context + the
   `ExtractResponse` schema. Output: concepts/persons JSON validated
   against `src/wikify/schema.py::ExtractResponse`.

2. **Page write** (tier M) — one Task per canonicalised page. Input:
   `WriteRequest` from `wikify draft write-request`. Output:
   `WriteResponse` per `src/wikify/schema.py::WriteResponse`.

Constraints the subagent prompt must embed:

- Write constraints — `reference/write-constraints.md`
- Citation format — `reference/citation-format.md`
- Tier mapping — `reference/tiers.md`

## Loop

```
wikify session init --bundle <b> --corpus <c> --strategy baseline [--budget-target N]

# seed selection — --persist writes seed_doc_ids and seed_chunk_ids
# onto the session so they land in the final _run.json snapshot.
wikify kg seeds --session <s> --persist
wikify session update --session <s> --patch '{"stages":{"seed_selection":{"status":"done"}}}'

# extract phase — one Task subagent per seed chunk, tier S. After each
# subagent returns its ExtractResponse, the skill records the call:
for each seed_chunk_id:
    Task subagent (tier S) -> <bundle>/_scratch/extract-<chunk_id>.json
    wikify meter record --session <s> \
        --role extractor --tier S \
        --input-tokens <from response.tokens_in> \
        --output-tokens <from response.tokens_out>
    # If projected spend would exceed 1.05x budget_target, meter record
    # exits 3 with budget_exceeded — stop the loop; do not retry.

# canonicalize — dedup extracted concepts and append session.pages
# entries (status=planned). Carries kind (article|person) and aliases
# so downstream draft / commit don't have to thread them as flags.
wikify extract canonicalize \
    --session <s> \
    --responses '<JSON array of all extract-*.json paths>'

# extract phase
for each seed chunk_id:
    Task subagent (tier S) -> extract-<chunk_id>.json
apply canonicalisation (skill or CLI helper) -> session.pages (status=planned)

# write phase — iterate over every status=planned page. The session
# entry carries kind (article|person) and aliases from canonicalize.
for each planned page in session:
    kind=$(wikify session show --session <s> | jq -r '.pages[] | select(.page_id == "<id>") | .kind')
    chunk_ids=$(wikify kg evidence --session <s> --page-id <id> --top-k 8 | jq -c .chunk_ids)
    wikify draft write-request --session <s> --page-id <id> \
        --page-kind "$kind" --chunk-ids "$chunk_ids"
        # emits <bundle>/_scratch/draft-<id>.json and records draft_path on
        # session.pages[<id>]. The skill reads the draft, spawns the Task
        # subagent with it, and writes the subagent's WriteResponse JSON
        # to <bundle>/_scratch/response-<id>.json. The subagent's response
        # MUST set page_kind to the same value or commit-page rejects.
    wikify validate write \
        --draft <bundle>/_scratch/draft-<id>.json \
        --response <bundle>/_scratch/response-<id>.json \
        --session <s>
        # --session is REQUIRED to transition the page entry from
        # drafted to validated. Without it, bundle commit-page will
        # reject the page with precondition_not_met.
        # on ok=false: retry once; then escalate per reference/escalation.md; then mark failed
    wikify bundle commit-page \
        --session <s> \
        --response <bundle>/_scratch/response-<id>.json \
        --validation <bundle>/_scratch/validation-<id>.json
        # --validation is REQUIRED. commit-page verifies ok=true AND
        # session.pages[<id>].status=="validated" before writing the
        # page file and rebuilding _index.json / _wiki_graph.json under
        # the session lock. commit-page AUTO-RECORDS the write call to
        # _calls.jsonl using response.tokens_in / response.tokens_out —
        # DO NOT also call `wikify meter record --role writer` for the
        # same response (double-accounting). Extractor calls go through
        # `wikify meter record` explicitly in the extract phase above.
    wikify session checkpoint --session <s> --label "after-<id>"

wikify session close --session <s>
wikify html <bundle>
```

Baseline's page set is finite — the loop terminates when every candidate
page reaches `status ∈ {committed, failed}`. Budget is a soft ceiling,
not a stopping criterion (scripted and guided treat it as one).

## Artifacts

- `<bundle>/_session/session.json` — updated after each stage and page commit
- `<bundle>/_session/checkpoints/<label>.json` — snapshot after seed
  selection and after each committed page
- `<bundle>/_scratch/draft-<page_id>.json` — `WriteRequest` for the subagent
- `<bundle>/_scratch/response-<page_id>.json` — raw subagent output
- `<bundle>/_scratch/validation-<page_id>.json` — validator verdict
- `<bundle>/pages/<id>.md` — committed encyclopedic article
- `<bundle>/_index.json`, `<bundle>/_wiki_graph.json` — rebuilt per commit
- `<bundle>/_run.json`, `<bundle>/_calls.jsonl` — runtime telemetry
  consumed by `wikify html` and `wikify eval`

## Validation

Every subagent output is written to scratch and passed through
`wikify validate write` before promotion. On validation failure:

1. Retry once at the same tier with a stricter prompt that names the
   specific constraint that failed.
2. On the second failure, escalate per `reference/escalation.md` (tier L
   Task subagent with the original request + escalation reason).
3. On the third failure, mark the page as `failed` in session state and
   move on. Do not loop further.

## Completion

The workflow is complete when all pages are `committed` or `failed` and
`wikify session close` returns. `wikify html <bundle>` renders the static
site; `wikify eval <bundle>` computes metrics.

## Failure / resume

If an earlier run of this workflow left a session at `status=active`, the
next invocation resumes by:

1. `wikify session show --session <s>` to inspect `stages.*.status` and
   the `pages[*]` vector.
2. Skip any stage whose status is `done`.
3. For each page whose status is `planned` or `drafted`, re-run the
   draft/validate/commit cycle; for `validated` pages, re-run
   `commit-page` only; for `committed` pages, skip.

The session lock (`_session/session.lock`) prevents two workflows from
mutating the same session concurrently.
