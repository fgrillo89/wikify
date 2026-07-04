---
name: extract-data
description: Extractor that harvests verifiable numeric/factual data points from corpus chunks, tables, and figure captions into the bundle claim store. Runs as a dedicated harvest pass over table-assets plus number-dense chunks, or piggybacks on chunks an explorer just read. Every point carries a verbatim grounding quote; the CLI gate rejects numbers it cannot locate in the source.
allowed-tools: Bash(wikify *) mcp__wikify__context_set mcp__wikify__corpus_show mcp__wikify__corpus_find
---

# extract-data

Harvest factual figures — any number with a subject and a property — into
the bundle's claim store. The store is schema-on-read: a small validated
core (subject / property / value / unit + provenance) with an open
conditions map. The store is the source of truth; data-artifact tables are
materialized views over it (built by `consolidate-data`).

## Three modes

- **Dedicated pass** (target: a doc list or "global"). Pull candidate
  sources and read them: corpus tables (`asset_type='table'`, which carry
  markdown content) and number-dense body chunks. This is the main path —
  tables are the richest source.
- **Property-targeted** (target: one canonical property + its aliases and
  units). Sweep the WHOLE corpus for every chunk that mentions the property,
  then extract+verify each candidate. This is the recall path: use it when a
  property (e.g. growth per cycle) is under-covered because per-round doc
  slices only ever scanned a fraction of the papers that report it. See
  "Property-targeted mode" below.
- **Piggyback** (target: a slug an explorer just grew). Re-read the chunks
  the explorer accepted as evidence and lift any numbers stated in prose.
  Cheap, because the chunks are already in hand.

## Property-targeted mode

Inputs: a **canonical property** name, its **alias** phrasings, and its
**units**. Cover EVERY genuinely-different way the corpus names the
quantity: the acronym AND its expansion (`GPC` and `growth per cycle`),
close synonyms (`growth rate per cycle`, `deposition per cycle`), and ALL
unit spellings — ASCII, Unicode, and word forms (`A/cycle`, `Å/cycle`,
`Angstrom/cycle`, `Angstroms per cycle`, `nm/cycle`, `pm/cycle`).
Separator variants (space vs hyphen, e.g. `growth per cycle` <->
`growth-per-cycle`) are AUTO-EXPANDED by the sweep, and duplicate matches
are deduped per chunk and per doc, so list the distinct NAMES and unit
spellings — not every hyphenation. Supply 3+ phrasings so paraphrases and
acronyms are not missed.

1. Enumerate every candidate chunk across all docs and read the recall report:
   ```bash
   wikify data harvest-property --property "growth per cycle" \
     --alias GPC --alias "growth-per-cycle" \
     --unit "A/cycle" --unit "Angstrom/cycle" \
     --corpus <corpus> --run <bundle> --format json
   ```
   The result is `{report{property, docs_mentioning_property, candidate_chunks,
   docs_in_table, data_recall, truncated}, candidates:[{doc_id, chunk_id,
   matched_phrasing, source_kind}], docs_extracted, matched_chunks}`. It does
   NOT add points — it hands you the worklist. Enumeration is cheap (handles
   only); pass `--include-text` if you want each candidate's chunk body inline.
2. For each candidate `chunk_id`, read the source, lift the property's value(s),
   and stage one JSON point per number exactly as in the dedicated pass.
3. Ingest through the gate with `data add` (below). The gate's
   subject/property/value/quote check is what filters false positives from an
   ambiguous unit like `A/cycle` (amperes vs angstrom) — trust it, do not
   pre-judge candidates.
4. Re-run `harvest-property` to refresh `data_recall`. Stop when
   `data_recall >= 0.90`, or when two consecutive sweeps add < 2 verified
   claims and nothing is `truncated`. If `truncated` is true, more candidates
   remain past the `--max-chunks` cap (default 500) — continue across rounds.

## What a data point is

One assertion + its provenance. Emit one JSON object per point to a staging
JSONL file:

```json
{"subject": "Al2O3", "property": "growth per cycle", "value": "1.1 A/cycle",
 "unit": "A/cycle", "value_original": "1.1 Å/cycle", "uncertainty": "0.05",
 "value_type": "scalar", "conditions": {"temperature": "200 C", "precursor": "TMA/H2O"},
 "method": "in-situ ellipsometry", "doc_id": "doc:36784072e838",
 "chunk_id": "...__c0007_2b8fc316", "locator": "Table 2",
 "grounding_quote": "a growth per cycle of 1.1 Angstrom/cycle at 200 C",
 "source_kind": "table", "extraction_tier": "T1", "confidence": 0.9}
```

Required: `subject`, `property`, `value`, `doc_id`, `chunk_id`,
`grounding_quote`. Everything else is optional but raises the point's value.

Rules:

- **The grounding quote is verbatim.** Copy the exact substring of the chunk
  text (or table caption / cell text) that states the number. The ingest
  gate locates the quote AND the number in the source and rejects the point
  if either is missing. A point without a real quote is wasted work.
- **Cite a resolvable chunk id.** `chunk_id` must be a canonical full id
  (the `<doc>__cNNNN_<hex>` form) or a bare hex short. Do NOT pass the
  `chunk:`-prefixed handle that `corpus show` and the MCP corpus tools
  print: `data add` does not strip the `chunk:` prefix, so the gate cannot
  load the source text, and the point is rejected as if its quote were
  missing. Strip the prefix to the bare short, or copy the full id.
- **One number per point.** A table row with three measured columns is three
  points sharing a subject.
- **Keep the original.** Put the number exactly as printed in
  `value_original`/`unit_original` (including `Å`, `±`, scientific notation);
  put a clean comparable form in `value`/`unit`.
- **Conditions are open.** Temperature, pressure, precursor, substrate,
  cycle count — whatever the source ties to the value. Free keys; values may
  carry their own units.
- **`subject` is the material/system the number describes** (e.g. "Al2O3
  film", "Pt/HfO2/TiN stack"), not the page being written.

## Figures

`chunk_assets` binds each chunk to its near figures, so a chunk's figures are
in reach.

- **Figure captions** are text. A caption like "Figure 2. GPC of 1.1 Å/cycle"
  is a first-class T1 point: `source_kind: "figure_caption"`, quote = the
  caption substring. These verify like any text.
- **Plot digitization** (reading a value off the curve in the image) is
  `source_kind: "figure", extraction_tier: "T3"`. There is no verbatim number
  to verify, so the gate keeps it but flags it `figure_digitized` — it never
  counts as verified. Only digitize when explicitly asked; prefer captions.

## Procedure

1. Bind context if not already bound:
   `mcp__wikify__context_set(corpus_path="<corpus>", bundle_path="<bundle>")`.
2. Gather candidates:
   - Dedicated: read the target docs' tables and dense chunks
     (`wikify corpus show <doc>` / `corpus find --in-doc`). Tables carry their
     markdown in the asset content — read cells directly.
   - Piggyback: read the slug's `evidence.jsonl` chunks.
3. Write one JSON object per point to `<bundle>/run/io/data_staging.jsonl`.
4. Ingest through the gate:
   ```bash
   wikify data add <bundle>/run/io/data_staging.jsonl \
     --run <bundle> --corpus <corpus> --format json
   ```
   The result reports `verified` / `rejected` / `figure_digitized` / `stored`
   / `duplicate`. Rejected points were not stored — re-read the source and fix
   the quote, or drop the claim. A point rejected despite a correct quote is
   usually an unresolvable `chunk:` handle in `chunk_id` (see the chunk-id
   rule): pass the bare short or full id instead.
5. Report counts back to the editor: `{submitted, stored, rejected}` plus an
   optional `escalate` block.

## Escalate, do not guess

Return an `escalate` block when the routing is genuinely ambiguous — e.g. the
same number is reported two ways across the doc and you cannot tell which is
the measured value vs a target, or a "property" is really two conflated
quantities. Routine accept/reject of a quote is your job.

## References

- `../consolidate-data/SKILL.md` — turns the claim store into committed
  `kind=data` artifact tables (the store's downstream consumer).
- `../../SKILL.md` — the editor's DATA wave: when a harvest pass is
  dispatched, the targets it gets, and the `{submitted, stored, rejected}`
  return contract.
- `../search-corpus/SKILL.md` — `corpus_show` / `corpus_find` primitives
  and chunk-handle semantics.
