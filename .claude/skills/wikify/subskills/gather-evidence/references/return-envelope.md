# Return envelope

The final assistant message MUST contain ONLY this JSON object — no
preamble prose, no trailing notes. One `results` entry per slug. Any
deviation (extra fields, renamed keys, alternate shape) is a contract
violation and breaks orchestrator parsing. Narrative notes go inside the
per-slug `errors[]` list, one short string per entry.

## Exact schema

All fields required, exact key names, exact types.

```
{
  "cluster_size":              int,          // number of slugs in this cluster
  "queries_issued":            int,          // total corpus_find calls across all rounds
  "unique_chunks_judged":      int,          // deduplicated chunk_ids sent to any judge
  "judge_calls":               int,          // total haiku/sonnet Task invocations
  "judge_discipline_failures": int,          // rows dropped by the quote guard
  "results": {
    "<slug>": {
      "appended":          int,             // rows committed via build-evidence
      "distinct_docs":     int,             // unique doc handles in accepted rows
      "iterations":        int,             // query rounds used (1 = initial plan only)
      "stop_reason":       "quota_met" | "max_rounds" | "pool_exhausted" | "error",
      "definition_chunk":  true | false,    // at least one def_for row committed
      "score_tiers":       int,             // distinct score values among accepted rows
      "errors":            []               // short strings; empty list when clean
    }
  }
}
```

`stop_reason` values: `quota_met` (slug hit `quota_per_slug`),
`pool_exhausted` (a genuine plateau -- two consecutive rounds added no new
distinct doc AND no new section-type facet; the WRITE recall gate treats
this as permission to write despite missing docs), `max_rounds`
(`max_query_rounds` ceiling reached; not a true plateau, so the editor
re-dispatches), `error`.

## Worked example (two slugs)

```json
{
  "cluster_size": 2,
  "queries_issued": 14,
  "unique_chunks_judged": 87,
  "judge_calls": 15,
  "judge_discipline_failures": 1,
  "results": {
    "atomic-layer-deposition": {
      "appended": 12,
      "distinct_docs": 6,
      "iterations": 2,
      "stop_reason": "quota_met",
      "definition_chunk": true,
      "score_tiers": 4,
      "errors": []
    },
    "al2o3-film": {
      "appended": 8,
      "distinct_docs": 4,
      "iterations": 3,
      "stop_reason": "pool_exhausted",
      "definition_chunk": false,
      "score_tiers": 3,
      "errors": ["no definition chunk found after 3 rounds; writer should open with alias expansion"]
    }
  }
}
```

## Telemetry

The orchestrator records cost telemetry from the harness `<usage>`
totals at each Task boundary; the supervisor does NOT self-report
tokens. Because this skill runs the haiku judge fleet, its per-chunk work
lands on the haiku tier (tier H) — distinct from a `build-evidence`
gather, whose work lands on the supervisor/editor tier (tier M).
