---
name: wikify/handlers/edit
description: Produce an editor brief for a wiki page from its dossier and neighbor context.
tier: M
dispatch_role: edit
---

> **DEPRECATED**: dispatch-era handler, scheduled for deletion after baseline parity lands. See `docs/skill-centric-pivot.md`.

# edit

## Context
Invoked by `wikify/runtime/serve-dispatch` when a request file appears at `$WIKIFY_DISPATCH_DIR/edit/<rid>.request.json` (default dispatch dir: `data/dispatch/`).

This is a mechanical recipe. There is no judgment in the loop — the Python harness decides what to dispatch; you just fulfil one request per invocation.

## Tier
edit runs at tier M. This maps to:
- S -> haiku-class small model
- M -> sonnet-class medium model
- L -> opus-class large model

(The tier may be overridden per-request by the guided mode via `set_tier` — read the request to confirm.)

## Request schema
Raw shape.

```json
{
  "page_id": "Resistive Switching",
  "title": "Resistive Switching",
  "dossier": [
    {
      "page_id": "Resistive Switching",
      "title": "Resistive Switching",
      "definition": "best definition",
      "summary": "consolidated summary",
      "parameters": [{"name": "ON/OFF ratio", "value": "10^3", "unit": "", "conditions": "HfO2"}],
      "mechanisms": ["filament formation"],
      "relationships": [{"target": "Memristor", "relation": "is_a", "evidence": "..."}],
      "equations": [],
      "evidence": [{"chunk_id": "...", "doc_id": "...", "quote": "...", "section_type": "methods"}],
      "n_sources": 5,
      "n_entries": 12
    }
  ],
  "neighbors": [
    {"title": "Memristor", "id": "Memristor"},
    {"title": "Hafnium Oxide", "id": "Hafnium Oxide"}
  ]
}
```

## Response schema
Reference: `src/wikify/schema.py::EditorBrief`

```json
{
  "page_id": "Resistive Switching",
  "title": "Resistive Switching",
  "article_register": "academic",
  "tone_guidance": "Neutral. Emphasize the filament model as dominant but note alternatives.",
  "lead_paragraph_instruction": "Define resistive switching as voltage-driven conductance change in metal-insulator-metal stacks.",
  "sections": [
    {
      "heading": "## Mechanism",
      "instruction": "Explain conductive filament formation. Compare HfO2 and TaOx.",
      "evidence_markers": ["e1", "e3"],
      "zone": "established",
      "parameters_to_include": ["switching speed", "ON/OFF ratio"]
    }
  ],
  "comparative_notes": "Unlike Memristor (broader device concept), this focuses on the physical switching phenomenon.",
  "figures_to_embed": [],
  "max_length_chars": 5000,
  "tokens_in": 500,
  "tokens_out": 300
}
```

The schema uses `extra="forbid"` — no extra fields allowed.

## Steps
1. Read the request file.
2. Spawn one Task subagent at tier M with:
   - System prompt: "You are the wikify editor. Read the dossier and emit a structured editor brief for the writer. Respond as strict JSON matching the EditorBrief schema. No commentary outside the JSON."
   - User prompt: the serialized request payload and the editorial rules below.
3. Receive the subagent's JSON output.
4. Validate the output against the response schema (client-side check BEFORE writing the file).
5. If validation fails, retry ONCE with a stricter prompt that repeats the schema.
6. If validation still fails, write `<rid>.error.json` next to the request with `{error: "...", last_output: "..."}` and stop.
7. If validation passes, write `<rid>.response.json` next to the request.
8. Stop. Do not loop or interpret results.

## Editorial rules
1. **article_register**: one of `academic | applied | tutorial | general`.
2. **tone_guidance**: specific tone instructions for the writer.
3. **lead_paragraph_instruction**: what the opening should say.
4. **sections**: a list of sections the article needs. For each:
   - `heading`: the `## Heading` text
   - `instruction`: what the writer should cover (be specific)
   - `evidence_markers`: which `eN` markers to cite (e.g. `["e1", "e3"]`)
   - `zone`: `established` (consensus), `contested` (disagreement), `frontier` (preliminary), or `""`
   - `parameters_to_include`: which quantitative values to mention
5. **comparative_notes**: how this page differs from its neighbor pages.
6. **figures_to_embed**: figure IDs to include (from the dossier evidence).
7. **max_length_chars**: target article length (2000 for minor concepts, 6000+ for important ones).

Choose sections based on the ACTUAL material, not a fixed template. A concept with rich mechanisms needs `## Mechanism`. A concept with performance data needs `## Performance`. A concept with competing models needs `## Alternative Explanations`. Do not invent sections the evidence does not support.

## Knowledge Graph context

The editor has access to both the corpus Knowledge Graph and the Wiki
Knowledge Graph for informed section planning.

- Corpus KG API: `.claude/skills/wikify/reference/knowledge-graph.md`
- Wiki KG API: `.claude/skills/wikify/reference/wiki-graph.md`

### Wiki graph: assess page state and gaps

```python
wkg = preloaded.wiki_knowledge_graph

# How connected is this page? More connections -> longer, richer article
co_ev = wkg.page(page_id).co_evidence().count()
n_links = wkg.page(page_id).links().count()
# Well-connected: 6000+ chars, 4+ sections. Isolated: 2000 chars, 2 sections.

# Does a related page already cover a subsection topic?
hits = wkg.search("specific subtopic", top_k=3)
if hits and hits[0]["score"] > 0.7:
    # Subtopic covered elsewhere -- reference it in See Also, don't duplicate

# Find pages that share evidence with this one
related = wkg.page(page_id).co_evidence().collect()
# -> use in comparative_notes to differentiate from neighbors

# Check if this page is thin compared to its neighbors
page = wkg.page(page_id).first()
neighbor_evidence = [
    wkg.page(nid).first()["n_evidence"]
    for nid in wkg.page(page_id).co_evidence().ids()
]
# If this page has much less evidence than neighbors, flag for more extraction
```

### Corpus KG: enrich with what the corpus knows

Use the corpus KG to enrich editorial decisions:

```python
kg = preloaded.knowledge_graph

# How important is this concept in the corpus?
# Check citation count of evidence sources
for doc_id in evidence_doc_ids:
    source = kg.source(doc_id).first()
    # source["citation_count"], source["pagerank"]
    # High-PageRank sources -> deeper sections, longer max_length

# Are there equations the dossier missed?
kg.sources().equations().search(page_title, top_k=3)
# -> if found, add a parameters/theory section to the brief

# Are there figures the dossier missed?
kg.sources().figures().search(page_title, top_k=3)
# -> if found, add to figures_to_embed

# What do citing papers say? (for contested zones)
for doc_id in evidence_doc_ids:
    citing = kg.source(doc_id).cited_by()
    if citing.count() > 0:
        # Multiple citing sources -> potential for "contested" or "frontier" zones
        pass

# Author impact: inform biographical sections for person pages
kg.author(author_key).sources().count()
kg.author(author_key).coauthors().collect()
```

Use KG insights to set `max_length_chars` (foundation topics get 6000+,
peripheral concepts get 2000), choose `zone` labels (established vs
contested based on citation patterns), and identify missing figures/equations.

## Escalation
Not supported. The editor IS the escalation target for lower-tier handlers — it does not escalate further.

## Errors
- **Schema validation failure**: one retry with stricter prompt, then `<rid>.error.json`.
- **Subagent refusal**: one retry with a clearer prompt, then error.json.
- **Timeout**: the Python harness has a 600s timeout on polling. You have roughly that window.

## What not to do
- Do NOT read other bundle files (use only what's in the request).
- Do NOT make multiple dispatches (one request -> one response).
- Do NOT interpret errors further than the retry logic above.
