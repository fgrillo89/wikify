---
name: wikify-extract-data
description: Extractor that harvests verifiable numeric/factual data points from corpus chunks, tables, and figure captions into the bundle claim store. Runs as a dedicated harvest pass over table-assets plus number-dense chunks, or piggybacks on chunks an explorer just read. Every point carries a verbatim grounding quote; the CLI gate rejects numbers it cannot locate in the source.
allowed-tools: Bash(wikify *) mcp__wikify__context_set mcp__wikify__corpus_show mcp__wikify__corpus_find
---

# wikify-extract-data

Harvest factual figures — any number with a subject and a property — into
the bundle's claim store. The store is schema-on-read: a small validated
core (subject / property / value / unit + provenance) with an open
conditions map. The store is the source of truth; data-artifact tables are
materialized views over it.

## Two modes

- **Dedicated pass** (target: a doc list or "global"). Pull candidate
  sources and read them: corpus tables (`asset_type='table'`, which carry
  markdown content) and number-dense body chunks. This is the main path —
  tables are the richest source.
- **Piggyback** (target: a slug an explorer just grew). Re-read the chunks
  the explorer accepted as evidence and lift any numbers stated in prose.
  Cheap, because the chunks are already in hand.

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
   wikify data add <bundle>/run/io/data_staging.jsonl --run <bundle> --format json
   ```
   The result reports `verified` / `rejected` / `figure_digitized` / `stored`
   / `duplicate`. Rejected points were not stored — re-read the source and fix
   the quote, or drop the claim.
5. Report counts back to the editor: `{submitted, stored, rejected}` plus an
   optional `escalate` block.

## Escalate, do not guess

Return an `escalate` block when the routing is genuinely ambiguous — e.g. the
same number is reported two ways across the doc and you cannot tell which is
the measured value vs a target, or a "property" is really two conflated
quantities. Routine accept/reject of a quote is your job.
