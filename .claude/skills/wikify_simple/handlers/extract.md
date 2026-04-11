---
name: wikify_simple/handlers/extract
description: Extract concept and person entries from one chunk and return them as a validated ExtractResponse.
tier: S
dispatch_role: extract
---

# extract

## Context
Invoked by `wikify_simple/runtime/serve-dispatch` when a request file appears at `$WIKIFY_SIMPLE_DISPATCH_DIR/extract/<rid>.request.json` (default dispatch dir: `data/dispatch/`).

This is a mechanical recipe. There is no judgment in the loop — the Python harness decides what to dispatch; you just fulfil one request per invocation.

## Tier
extract runs at tier S. This maps to:
- S -> haiku-class small model
- M -> sonnet-class medium model
- L -> opus-class large model

(The tier may be overridden per-request by the LLM policy via `set_tier` — read the request to confirm.)

## Request schema
Reference: `src/wikify_simple/contracts/schema.py::ExtractRequest`

```json
{
  "chunk_id": "doc_17::chunk_004",
  "chunk_text": "Atomic layer deposition (ALD) is a thin-film growth technique ...",
  "canonical_titles": ["Atomic Layer Deposition", "Memristor"],
  "prompt_template": "wikify_simple/extract",
  "model_id": "claude-haiku-...",
  "tier": "S",
  "images_for_doc": [
    {"path": "figures/doc_17_fig3.png", "label": "Figure 3", "caption": "ALD reactor schematic"}
  ]
}
```

## Response schema
Reference: `src/wikify_simple/contracts/schema.py::ExtractResponse`

```json
{
  "chunk_id": "doc_17::chunk_004",
  "concepts": [
    {
      "title": "Atomic Layer Deposition",
      "aliases": ["ALD"],
      "kind": "article",
      "quote": "Atomic layer deposition (ALD) is a thin-film growth technique",
      "category": "method",
      "evidence_figures": ["Figure 3"],
      "confidence": "extracted",
      "score": 1.0,
      "definition": "A self-limiting vapor-phase thin-film growth technique.",
      "summary": "ALD grows films one atomic layer at a time via alternating precursor pulses ...",
      "parameters": [{"name": "growth-per-cycle", "value": "~1", "unit": "A", "conditions": "HfO2 at 250C"}],
      "mechanisms": ["surface-limited chemisorption"],
      "relationships": [{"target": "Memristor", "relation": "used_to_fabricate", "evidence": "..."}],
      "equations": []
    },
    {
      "title": "Stuart S. P. Parkin",
      "aliases": [],
      "kind": "person",
      "quote": "Parkin and colleagues first demonstrated the racetrack memory concept",
      "category": null,
      "confidence": "extracted",
      "score": 1.0
    }
  ],
  "tokens_in": 800,
  "tokens_out": 400
}
```

## Steps
1. Read the request file.
2. Spawn one Task subagent at tier S with:
   - System prompt: "You are the wikify_simple extractor. Read the supplied chunk and emit any concepts or named people that would deserve a wiki page. Respond as strict JSON matching the ExtractResponse schema. No commentary outside the JSON."
   - User prompt: the serialized request payload (chunk_id, chunk_text, canonical_titles, images_for_doc) plus the ExtractResponse schema and the content rules below.
3. Receive the subagent's JSON output.
4. Validate the output against the response schema (client-side check BEFORE writing the file).
5. If validation fails, retry ONCE with a stricter prompt that repeats the schema.
6. If validation still fails, write `<rid>.error.json` next to the request with `{error: "...", last_output: "..."}` and stop.
7. If validation passes, write `<rid>.response.json` next to the request.
8. Stop. Do not loop or interpret results.

## Content rules (the subagent must follow)
- `kind` is EXACTLY `"article"` or `"person"` — it routes the page to `articles/` or `people/`.
- `category` is a facet tag, not a type. Allowed values: `phenomenon | method | material | device | theory | metric | organization | other`, or `null`/omitted. MUST be `null` for `kind="person"`.
- `title` 2..120 chars, not a stopword, no edge punctuation.
- `aliases` deduped case-insensitively, drop entries equal to title, max 8 entries.
- `quote` 5..400 chars after stripping; MUST be a VERBATIM substring of `chunk_text` (copy-paste, do not paraphrase). Verify by literal substring search before emitting.
- `confidence` is one of `extracted` (named verbatim, default), `inferred` (implied but not named), or `ambiguous` (uncertain). `score` in [0,1]; default 1.0 for extracted, 0.5..0.8 for inferred, < 0.5 for ambiguous.
- Person extraction rule: if the chunk attributes work to a named researcher / inventor / theorist / practitioner / historical figure in the prose (not merely listed in a reference list or bibliography), emit a `kind="person"` entry with the person's full name as the title. The person need NOT be an author of the current document.
- The rich dossier fields (`definition`, `summary`, `parameters`, `mechanisms`, `relationships`, `equations`) are optional but strongly preferred — the compactor and editor use them. Emit them when the chunk supports them; omit or leave empty otherwise.

## Image awareness

When `images_for_doc` is non-empty, check whether any caption matches the title or aliases of an emitted concept (token overlap: at least one significant non-stopword token in common, or the caption contains the concept title as a substring). If a match is found, populate `evidence_figures: ["<image_id>"]` on that concept. Image IDs come from `images_for_doc[i].id`.

When processing a caption chunk (the chunk text IS the caption of a figure) and vision analysis would be needed to answer correctly, emit `needs_vision: true` in a top-level `extra` field on the response (e.g. `"extra": {"needs_vision": true}`). The pipeline logs this for future vision-on-demand binding. Vision on demand is a documented future capability; no real vision binding exists today.

## Escalation
Supported. If the subagent is uncertain about its output, it can emit `{"escalate": true, "reason": "..."}` in a top-level `escalation` field on its response instead of the normal fields.

When you see this:
1. Spawn a nested Task subagent at tier L (opus) with the original request + the escalation reason.
2. Use the opus subagent's output as the final response.
3. Record the escalation in a `meta.escalated_from` field so the cost meter attribution is visible.

The escalation happens INSIDE the skill invocation. It does NOT write a separate dispatch file. It costs more tokens but stays local.

## Errors
- **Schema validation failure**: one retry with stricter prompt, then `<rid>.error.json`.
- **Quote substring failure**: one retry reminding the subagent "quote must be a VERBATIM substring of chunk_text", then error.json.
- **Subagent refusal**: one retry with a clearer prompt, then error.json.
- **Timeout**: the Python harness has a 600s timeout on polling. You have roughly that window.

## What not to do
- Do NOT read other bundle files (use only what's in the request).
- Do NOT make multiple dispatches (one request -> one response).
- Do NOT interpret errors further than the retry logic above.
