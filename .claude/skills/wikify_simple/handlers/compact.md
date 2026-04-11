---
name: wikify_simple/compact
description: Fulfil one compact dispatch request — consolidate dossier entries for a concept.
---

# compact

The harness has written a request at `data/dispatch/compact/{rid}.request.json`.
Read it, consolidate the dossier entries, and write the response to
`data/dispatch/compact/{rid}.response.json`.

## Request shape

```json
{
  "page_id": "Atomic Layer Deposition",
  "title": "Atomic Layer Deposition",
  "entries": [
    {
      "chunk_id": "...",
      "doc_id": "...",
      "quote": "...",
      "definition": "one-sentence definition",
      "summary": "2-3 sentence summary",
      "parameters": [{"name": "...", "value": "...", "unit": "...", "conditions": "..."}],
      "mechanisms": ["short phrase"],
      "relationships": [{"target": "...", "relation": "...", "evidence": "..."}],
      "equations": [{"latex": "...", "label": "...", "kind": "mathematical|chemical", "context": "..."}],
      "section_type": "methods"
    }
  ]
}
```

## Your task

Consolidate N raw entries into one clean, non-redundant dossier:

1. **definition**: Pick the most precise and complete definition. If none exists, write one.
2. **summary**: Synthesize a 3-5 sentence summary across all entries.
3. **parameters**: Deduplicate by name. Keep the most specific value (with conditions). Max 10.
4. **mechanisms**: Merge near-duplicates. Keep at most 6.
5. **relationships**: Deduplicate by target. Keep at most 8.
6. **equations**: Deduplicate by LaTeX content. Keep at most 8.
7. **top_evidence**: Select the 5-8 most informative entries, preferring different source documents for breadth. Each entry must include chunk_id, doc_id, quote, and summary.

## Response shape (write to `data/dispatch/compact/{rid}.response.json`)

```json
{
  "page_id": "Atomic Layer Deposition",
  "definition": "one best definition",
  "summary": "consolidated 3-5 sentence summary",
  "parameters": [{"name": "...", "value": "...", "unit": "...", "conditions": "..."}],
  "mechanisms": ["phrase1", "phrase2"],
  "relationships": [{"target": "...", "relation": "...", "evidence": "..."}],
  "equations": [{"latex": "...", "label": "...", "kind": "...", "context": "..."}],
  "top_evidence": [{"chunk_id": "...", "doc_id": "...", "quote": "...", "summary": "..."}],
  "tokens_in": 500,
  "tokens_out": 300
}
```

No extra fields. Respond as strict JSON only.
