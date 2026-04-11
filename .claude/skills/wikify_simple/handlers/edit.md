---
name: wikify_simple/edit
description: Fulfil one edit dispatch request — produce an editorial brief for a wiki page.
---

# edit

The harness has written a request at `data/dispatch/edit/{rid}.request.json`.
Read it, produce an editorial brief, and write the response to
`data/dispatch/edit/{rid}.response.json`.

## Request shape

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
      "parameters": [...],
      "mechanisms": [...],
      "relationships": [...],
      "equations": [...],
      "evidence": [{"chunk_id": "...", "doc_id": "...", "quote": "...", "section_type": "..."}],
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

## Your task

You are the editor. Read the dossier and decide:

1. **article_register**: "academic" | "applied" | "tutorial" | "general"
2. **tone_guidance**: specific tone instructions for the writer
3. **lead_paragraph_instruction**: what the opening should say
4. **sections**: a list of sections the article needs. For each:
   - `heading`: the `## Heading` text
   - `instruction`: what the writer should cover (be specific!)
   - `evidence_markers`: which `eN` markers to cite (e.g. `["e1", "e3"]`)
   - `zone`: "established" (consensus), "contested" (disagreement), "frontier" (preliminary), or ""
   - `parameters_to_include`: which quantitative values to mention
5. **comparative_notes**: how this differs from neighbor pages
6. **figures_to_embed**: figure IDs to include (from the dossier evidence)
7. **max_length_chars**: target article length (2000 for minor concepts, 6000+ for important ones)

Choose sections based on the ACTUAL material, not a fixed template.
A concept with rich mechanisms needs "## Mechanism". A concept with
performance data needs "## Performance". A concept with competing
models needs "## Alternative Explanations".

## Response shape (write to `data/dispatch/edit/{rid}.response.json`)

```json
{
  "page_id": "Resistive Switching",
  "title": "Resistive Switching",
  "article_register": "academic",
  "tone_guidance": "Neutral. Emphasize the filament model as dominant but note alternatives.",
  "lead_paragraph_instruction": "Define resistive switching as voltage-driven...",
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
`article_register` must be one of: academic, applied, tutorial, general.
