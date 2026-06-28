# Judge protocol

The full contract for Step 4: partitioning the candidate pool into
batches, the judge input/output schema, and the discipline guard the
supervisor runs over every accepted row.

## Batch-size heterogeneity gate

Before partitioning, set the working `judge_batch_size` from cluster
size:

- **`cluster_size >= 4`:** reduce `judge_batch_size` from the default 6
  to **4**. Larger sibling sets carry more distinct sub-topics in the
  judge's prior block, which crowds haiku's attention; smaller batches
  keep the quote-routing task within haiku's reliable range.
- **`cluster_size < 4`:** keep the configured `judge_batch_size`
  (default 6).

Person clusters use the same size rule. Size is the proxy because it is
directly readable from `cluster_slugs` at runtime, with no dependency on
per-concept category metadata. The supervisor passes the resolved batch
size to every judge Task.

## Round-1 sonnet escalation

After the first wave of judge Tasks completes, compute:

```
failure_rate = judge_discipline_failures / total_judge_calls
```

If `failure_rate >= 0.30` (30% or more of calls produced at least one
discipline failure), switch **all subsequent batches in this cluster**
to `Task(model="sonnet", ...)`. Sonnet is roughly 5x the per-token cost
of haiku but reliably satisfies the verbatim-quote rule on heterogeneous
topic mixes; the trade-off is justified above 30% because discarded
batches waste more compute than the upgrade saves.

## Judge input (supplied in the prompt)

- Sibling slugs, one tiny block per slug:
  `{slug, title, aliases, definition_priors?}`.
- The batch's chunk handles + their full text. The supervisor pre-fetches
  text by calling
  `mcp__wikify__corpus_show(handle="chunk:<short>", full=True)` once per
  chunk, caches the result, and passes the texts inline in the judge's
  prompt. `corpus_find` cannot be scoped by `chunk_id` — only `in_doc`
  (one document handle) — so the per-chunk `corpus_show` is the only
  reliable path.
- The scoring ladder (Rule 4 in SKILL.md): 1.00 definition, 0.95
  mechanism, 0.85 materials/process, 0.75 application, 0.60
  sibling-relevant.

## Judge output (strict JSON, ≤400 tokens)

```json
[
  {
    "chunk_id": "<id>",
    "on_topic_for": [
      {"slug": "<sibling-slug>", "score": 0.95, "quote": "<verbatim sentence from chunk text>"}
    ],
    "def_for": ["<slug>"],
    "section_type": "<type>"
  },
  {
    "chunk_id": "<id>",
    "on_topic_for": [],
    "reject_reason": "byline / references-list / off-topic / boilerplate"
  }
]
```

A judge MUST set `def_for: [<slug>]` when a chunk opens with
`<title> is …` / `<title> refers to …` / `<acronym> stands for …`.
A chunk may route to multiple slugs, each with its own score and quote.

## Judge discipline guard (mandatory)

For each accepted row the supervisor receives:

1. Fetch the chunk's `text` once (cache it).
2. Verify the judge's `quote` is a verbatim substring (post-NFKC
   normalise both sides to dodge Unicode-confusable rejections).
3. If the quote is missing or not present, drop that accept row and log a
   `judge_discipline_failure` event.
4. If a judge batch returns ≥2 discipline failures, re-run that batch
   once with `Task(model="sonnet")` as a fallback. Sonnet is more
   expensive but reliable on the quote rule.
5. If the sonnet re-run also returns ≥2 discipline failures, discard the
   entire batch's accepts, log a `judge_batch_abandoned` event naming the
   chunk_ids, and continue. Do not retry a third time. A batch that fails
   twice usually means the chunks themselves are malformed (OCR garbage,
   byline-only, references-list dump the section classifier missed);
   padding the ledger from them would degrade dossier quality.
