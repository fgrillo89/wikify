# Citation Format

Use `[^eN]` markers in prose. Every marker must have exactly one
matching definition in the final `## References` section:

```text
[^eN]: <chunk_id> (<doc_id>) > "<quote>"
```

`<quote>` must be a verbatim substring of the cited source chunk.
Validation fails if the quote is fabricated or edited.

## `N` is an index, not a label

`[^eN]` resolves POSITIONALLY to the Nth (1-based) entry of
`draft.json`'s `evidence` array: `[^e7]` means `evidence[6]`, always.
The dossier's **Marker index** table is that array in order — take each
chunk's marker from there and never renumber. `<chunk_id>` in the
definition must be `evidence[N-1].chunk_id`; the validator cross-checks
the two and rejects a disagreement as `marker_evidence_mismatch`,
naming both indices.

This is not cosmetic. A quote frequently occurs in more than one
gathered chunk (adjacent chunks from one paper overlap), so a freely
numbered marker can pass a naive quote check while the rendered page
attributes the claim to the wrong paper.

Evidence order is the order of active records in
`work/concepts/<slug>/evidence.jsonl`, so appends are stable but a drop
or reorder repoints every later marker. `draft build --task refine`
reports the old->new mapping as `marker_remap`.
