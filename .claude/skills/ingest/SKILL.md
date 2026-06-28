---
name: ingest
description: Parse a directory of documents (PDF / DOCX / PPTX / HTML) into a queryable Wikify corpus. Use when the user has local files to turn into a corpus, or to finish an arxiv harvest. Wraps `wikify corpus build` and owns parser-backend choice (docling default, marker, lite), the --out convention, partial-failure policy, and post-build health checks.
allowed-tools: Bash(wikify corpus *)
---

# ingest

Turn a folder of documents into a queryable Wikify corpus. This is a
thin, decision-light wrapper over `wikify corpus build`. Its one real
job is choosing the parser backend and confirming the corpus is healthy
before handing it to a build workflow.

## Command

```sh
wikify corpus build <source> --out data/corpora/<name> \
  [--mode additive|sync] \
  [--parser default|lite|marker|docling] \
  [--workers N] \
  [--openalex/--no-openalex] \
  [--allow-partial]
```

`<source>` is a directory of documents (PDF / DOCX / PPTX / HTML).

## Parser-backend choice

- `default` (= `docling`): Docling for every format. The first run
  downloads the Granite-Docling-258M formula model (~258 MB) plus the
  layout / table models; after the cache is warm the median PDF parses
  in ~10 s on an Ampere GPU. Pick this when equation/table fidelity
  matters.
- `marker`: the fastest PDF path. Use it when equation extraction is
  not needed and wall-clock matters more than formula fidelity.
- `lite`: CI / low-resource backend (pymupdf4llm + python-docx +
  python-pptx + trafilatura, no models). Use it where the model
  downloads are unaffordable.

(`default` and `docling` select the same backend.)

## Mode

- `--mode additive` (default): keep documents already in the corpus and
  add the new ones. Use for incremental growth.
- `--mode sync`: drop corpus documents no longer present under
  `<source>`. Use when `<source>` is the authoritative set.

## Citation enrichment / network

`--openalex` is ON by default: the refresh stage issues network requests
to `api.openalex.org` to canonicalise bibliography metadata and surface
in-corpus citation matches. Set `OPENALEX_EMAIL` for the polite-pool rate
limit (10 req/s). Pass `--no-openalex` to skip that stage and run fully
offline (air-gapped or no-network builds).

## Output convention

Always write to `--out data/corpora/<name>`, never `build/<name>`.

## Partial-failure policy

Default is OFF: any per-file parse failure aborts the whole build with
exit 5. Pass `--allow-partial` to continue past failures and recover the
successful papers; a later re-run picks up the files that failed.

## Post-build health check

```sh
wikify corpus check data/corpora/<name>
```

The corpus dir is a positional argument (not `--out`); when omitted it
falls back to `WIKIFY_CORPUS` or the cwd (see
`docs/filesystem-state-design.md`, "Corpus build/read commands").
Confirm the doc / chunk / embedding / edge counts look right before
declaring the corpus ready.

## Rebuild surface

For re-deriving artifacts on an existing corpus without a full rebuild,
`corpus rechunk` and `corpus refresh` are available. Mention them; do not
expand on them here.

## References

- `../wikify/subskills/reference/references/cli/grammar.md`
- `../wikify/subskills/reference/references/cli/output-contract.md`
- `../wikify/subskills/reference/references/cli/exit-codes.md`
