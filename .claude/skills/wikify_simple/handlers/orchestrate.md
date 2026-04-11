---
name: wikify_simple/orchestrate
description: Mechanical recipe — fulfil one orchestrator step request.
---

# orchestrate

The harness has written `data/dispatch/orchestrate/{rid}.request.json`.
Read it, take one decision from the action menu in
`src/wikify_simple/agents/schema.py::OrchAction`, and write the JSON
response next to the request.

## The wiki index is the orchestrator's primary read surface

Every request includes an `index_path` field pointing at the bundle's
`_index.json`. **Read the index file before taking any decision.** It
is a single JSON file with:

```
{
  "version": 1,
  "entries": [
    {"id", "kind", "title", "aliases", "path", "n_evidence",
     "doc_ids", "links"}
  ]
}
```

Use the index — not the page files — for:

- counting concept vs person pages and gauging breadth
- checking whether a candidate title or alias already exists
  (`propose_concept` should never propose a duplicate)
- finding hub pages (large `links` lists, or many pages sharing the
  same `doc_ids`)
- finding orphan pages (`links` empty, or `doc_ids` size 1) that may
  need a `walk_local` to grow their evidence
- planning which docs to `jump_uniform` next by diffing the corpus
  doc list against `index_summary.docs_covered`

Only call `inspect_page(id)` (which loads a full body) when the index
entry alone is not enough — for example when deciding whether to
`merge_concepts(a, b)`.

## Steps

1. Read the request file.
2. Read `index_path` to see the current state of the wiki.
3. Spawn one Task subagent (opus tier) with the run state, the index
   summary, and the action menu (walk_local, jump_uniform,
   jump_pagerank, jump_gap, propose_concept, merge_concepts,
   inspect_page, write_page, inspect_metric, done).
4. Receive the subagent's chosen action as JSON: {name, args,
   tokens_in, tokens_out}.
5. Write to `{rid}.response.json`.
6. Stop.

The orchestrator MUST consult the index before `propose_concept` (to
avoid duplicates), before `merge_concepts` (to confirm both exist),
and before `write_page` (to confirm the page exists and is not
already written above the evidence threshold).
