# Dedup rulebook

The editor decides merges; these are the signals and the action. Run it
in CURATE over the candidate pairs `cluster-concepts --by evidence`
surfaces (plus any near-duplicate titles).

**Signals (cheap, no chunk-text reads):**

- **Evidence overlap** — Jaccard of the two concepts' evidence doc-sets
  from `cluster-concepts`. High overlap (>= 0.5) is a strong merge
  signal; the two dossiers are built from the same papers.
- **Semantic proximity** — `wiki_find`/`corpus_find` on one title
  returns the other in its top hits (the editor may read titles +
  previews, never chunk bodies).
- **Subsumption** — one title is a property / part / instance / acronym
  of the other (`Memristance` is the defining property of `Memristor`;
  `RRAM` <-> `Resistive Random-Access Memory`). This is the case lexical
  distance misses.
- **Lexical** — near-identical normalised titles (plural/singular,
  hyphenation): always merge the variant into the base.

**Decision:**

- **Merge** when evidence overlap is high AND (semantic proximity OR
  subsumption OR lexical match). Pick the canonical slug = the broader /
  more-cited concept (`Memristor` over `Memristance`); the narrower
  becomes an alias.
- **Keep distinct** when the pair shares sources but covers genuinely
  separate facets a reader would want apart (e.g. `Bipolar Resistive
  Switching` vs `Non-Filamentary Resistive Switching` — different
  mechanisms). Sharing evidence is not sufficient; demand redundancy of
  the *concept*, not just the sources.
- **When unsure, keep distinct** — a wrong merge is lossy and hard to
  undo; a missed merge is cheap to catch next CURATE.

**Execute a merge of `<dup>` into `<canonical>` (both in-flight):**

```bash
wikify work seen-chunks <dup> --run <bundle> \
  | jq '[.seen_chunk_ids[] | {chunk_id: .}]' \
  | wikify work build-evidence <canonical> --from-ids @- \
      --corpus <corpus> --run <bundle>            # fold evidence (dedups)
wikify work set <canonical> --aliases '[<existing>, "<Dup Title>", <dup aliases>]' \
  --run <bundle>                                   # keep the dup title resolvable
wikify work set <dup> --status merged --run <bundle>   # tombstone (drops from roster)
wikify run record-event --type concept_status_changed --concept-id <dup> \
  --run <bundle> --data '{"status": "merged", "into": "<canonical>"}'
```

A `merged` (also `parked`, `dropped`) card never re-enters `ready` /
`growing`, so WRITE/GROW skip it and it does not hold the stop check
open. The `build-evidence` fold does NOT self-emit an `evidence_added`
event, so emit one for `<canonical>` in this round's CONSOLIDATE (see
Hard Rules) or the merged-in evidence will not count toward its
growth-stall timer. **If either page is already committed**, do NOT
hand-edit — run `refine` to redirect/fold (committed pages are repaired
only through refine).
