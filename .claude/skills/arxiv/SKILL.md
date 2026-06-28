---
name: arxiv
description: Acquire arXiv papers on a topic and stage them for `corpus build`. Use when the user wants to download, harvest, fetch, or bulk-collect arXiv papers about a subject, field, or category, or seed a Wikify corpus from arXiv. Composes `arxiv scout` (discover categories), `arxiv identify` (harvest metadata), `arxiv download` (PDFs), then `corpus build`, owning category selection, coverage threshold, resumability, and partial-failure policy.
allowed-tools: Bash(wikify *)
---

# arxiv

Acquire arXiv papers for a topic and stage them for `corpus build`. This
workflow owns category selection, the scout coverage threshold, the
one-staging-dir-per-request rule, partial-failure policy, and the handoff
to ingest. It composes the `wikify arxiv` CLI.

arXiv harvest is **set-based** (categories), not free-text. The only real
decision is *which categories cover the topic*; scout answers that, and
everything after is mechanical and resumable.

Set `WIKIFY_CONTACT_EMAIL` before harvesting; arXiv requests a contact in
the User-Agent for programmatic use.

## Inputs

- `topic` - natural-language subject (e.g. "machine learning").
- `out` - staging directory, one per category set
  (e.g. `data/staging/<topic-slug>`).
- `corpus` - target corpus directory for the final `corpus build`.

## Workflow (5 phases)

### P1 - Scout

Run scout for the topic across 2-3 phrasings and pool the
primary-category histograms.

```bash
wikify arxiv scout "all:<topic>" --max 200 --format json
wikify arxiv scout "ti:<topic>"  --max 200 --format json
```

Field prefixes `all:` / `ti:` / `abs:` / `cat:` scope the search. Collect
`primary_histogram[].{category,count,setspec}` and `total_results` across
the runs. Scout samples the Query API (capped at 30k hits); it is a
discovery aid, not an exhaustive lister.

### P2 - Decide categories

Keep categories whose pooled share clears a coverage threshold (default:
the categories covering ~90% of sampled hits, dropping any below ~5% of
the top category). Show the chosen `--category` set and the rough size
(`total_results` plus per-category counts) to the user and confirm before
harvesting a large set. Rows with an empty `setspec` are unmappable; only
harvest those via `--set <raw>` if the user asks.

### P3 - Identify (resumable)

```bash
wikify arxiv identify --category <c1> --category <c2> ... --out <out>
```

`identify` exhaustively walks the OAI-PMH record set(s), writes one record
per paper to `<out>/manifest.jsonl`, and stores the resume cursor in
`<out>/harvest_state.json`; re-running resumes from that cursor. An
unrecognized archive errors with `unknown_category` - pass the exact
setSpec via `--set`. A `state_mismatch` error means `<out>` was harvested
for different categories; use a fresh `--out`. Check progress any time
with `wikify arxiv status --out <out>`.

### P4 - Download (resumable, throttle-aware)

```bash
wikify arxiv download --out <out>
```

Defaults to arXiv's PDF-friendly ~4 req/s (tune with `--concurrency` /
`--rate`) and backs off on 429/503. It exits non-zero
(`download_incomplete`) if any PDF fails; re-run to resume. Pass
`--allow-partial` only when the user accepts a partial corpus. Re-check
with `wikify arxiv status --out <out>`.

### P5 - Hand off to ingest

```bash
wikify corpus build <out> --out <corpus>
```

Ingest enumerates the staged PDFs and ignores `manifest.jsonl` /
`harvest_state.json`.

## Stop conditions

- Stop after P2 if the user rejects the proposed category set.
- Stop after P4 if downloads are incomplete and `--allow-partial` was not
  granted; report the failed list and how to resume.
- The manifest is the durable record; every phase is re-entrant on the
  same `--out`, so an interrupted run continues where it stopped.

## References

- `../wikify/subskills/reference/references/cli/grammar.md`
- `../wikify/subskills/reference/references/cli/output-contract.md`
- `../wikify/subskills/reference/references/cli/exit-codes.md`
