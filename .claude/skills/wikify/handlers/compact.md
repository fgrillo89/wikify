---
name: wikify/handlers/compact
description: Consolidate raw dossier entries for a concept into one clean non-redundant dossier.
tier: S
dispatch_role: compact
---

> **DEPRECATED**: dispatch-era handler, scheduled for deletion after baseline parity lands. See `docs/skill-centric-pivot.md`.

# compact

## Context
Invoked by `wikify/runtime/serve-dispatch` when a request file appears at `$WIKIFY_DISPATCH_DIR/compact/<rid>.request.json` (default dispatch dir: `data/dispatch/`).

This is a mechanical recipe. There is no judgment in the loop — the Python harness decides what to dispatch; you just fulfil one request per invocation.

## Tier
compact runs at tier S. This maps to:
- S -> haiku-class small model
- M -> sonnet-class medium model
- L -> opus-class large model

(The tier may be overridden per-request by the guided mode via `set_tier` — read the request to confirm.)

## Request schema
Raw shape (not a pydantic model; consumed directly by the compactor).

```json
{
  "page_id": "Atomic Layer Deposition",
  "title": "Atomic Layer Deposition",
  "entries": [
    {
      "chunk_id": "doc_17::chunk_004",
      "doc_id": "doc_17",
      "quote": "Atomic layer deposition (ALD) is a thin-film growth technique",
      "definition": "Self-limiting vapor-phase thin-film growth.",
      "summary": "ALD grows films one layer at a time ...",
      "parameters": [{"name": "growth-per-cycle", "value": "~1", "unit": "A", "conditions": "HfO2 at 250C"}],
      "mechanisms": ["surface-limited chemisorption"],
      "relationships": [{"target": "Memristor", "relation": "used_to_fabricate", "evidence": "..."}],
      "equations": [{"latex": "", "label": "", "kind": "chemical", "context": "..."}],
      "section_type": "methods"
    }
  ]
}
```

## Response schema
Raw shape.

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

## Steps
1. Read the request file.
2. Spawn one Task subagent at tier S with:
   - System prompt: "You are the wikify compactor. Consolidate N raw dossier entries for one concept into a single clean non-redundant dossier. Respond as strict JSON matching the compact response shape. No commentary outside the JSON."
   - User prompt: the serialized request payload and the consolidation rules below.
3. Receive the subagent's JSON output.
4. Validate the output against the response schema (client-side check BEFORE writing the file).
5. If validation fails, retry ONCE with a stricter prompt that repeats the schema.
6. If validation still fails, write `<rid>.error.json` next to the request with `{error: "...", last_output: "..."}` and stop.
7. If validation passes, write `<rid>.response.json` next to the request.
8. Stop. Do not loop or interpret results.

## Consolidation rules
1. **definition**: pick the most precise and complete definition. If none exists, write one.
2. **summary**: synthesize a 3-5 sentence summary across all entries.
3. **parameters**: deduplicate by name. Keep the most specific value (with conditions). Max 10.
4. **mechanisms**: merge near-duplicates. Keep at most 6.
5. **relationships**: deduplicate by target. Keep at most 8.
6. **equations**: deduplicate by LaTeX content. Keep at most 8.
7. **top_evidence**: select the 5-8 most informative entries, preferring different source documents for breadth. Each entry must include `chunk_id`, `doc_id`, `quote`, and `summary`.

## Escalation
Not supported. The compactor is deterministic enough that escalation is not configured.

## Errors
- **Schema validation failure**: one retry with stricter prompt, then `<rid>.error.json`.
- **Subagent refusal**: one retry with a clearer prompt, then error.json.
- **Timeout**: the Python harness has a 600s timeout on polling. You have roughly that window.

## What not to do
- Do NOT read other bundle files (use only what's in the request).
- Do NOT make multiple dispatches (one request -> one response).
- Do NOT interpret errors further than the retry logic above.
