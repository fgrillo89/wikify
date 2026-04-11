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
    {"id": "doc_17/Figure_03", "path": "figures/doc_17_fig3.png", "label": "Figure 3", "caption": "ALD reactor schematic", "near_chunk_ids": ["doc_17::chunk_004"]}
  ],
  "equations": [
    {"id": "ab12cd34", "latex": "GPC = 0.9 A/cycle", "type": "unicode", "label": null, "context": "We measured a growth-per-cycle (GPC) of [...] in the trench geometry."}
  ],
  "figure_captions": [
    {"key": "Fig. 3", "kind": "figure", "num": 3, "sub": "", "caption": "ALD reactor schematic showing the TDMAHf and H2O precursor lines.", "image_id": "doc_17/Figure_03"}
  ]
}
```

`equations` and `figure_captions` are pre-filtered for THIS chunk:

* `equations` lists every equation whose source position falls inside this chunk (computed at ingest time via `Document.equations` + `Chunk.equation_ids`). Use them to ground parameter and mechanism extraction — the equation `latex` and `context` are authoritative.
* `figure_captions` lists figures the body discussion already mentions near this chunk. Each entry's `image_id` is set when a binary image was matched, otherwise `null` (caption-only). Use these to populate `evidence_figures` on concepts the figure clearly supports — see "Image awareness" below.

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
- **Do NOT emit any entries from reference-list, bibliography, or acknowledgments sections.** If the chunk is clearly a reference list (e.g. "(59) Linearity Tuning of Weight Update..."), emit an empty `concepts: []` response and stop. The section_type field on the request is your signal; reject chunks with `section_type` in `["references", "acknowledgments", "appendix"]` entirely.
- **`definition` is REQUIRED for `kind="article"` when the chunk contains substantive content.** Write 50-200 words: what the concept is, what it does, its domain, and why it matters. Example: "Atomic layer deposition (ALD) is a self-limiting vapor-phase thin-film growth technique where alternating exposures to gaseous precursors produce conformal films one atomic layer at a time. The self-limiting surface reactions enable atomic-level thickness control and exceptional step coverage on high-aspect-ratio structures. ALD is the dominant technique for depositing dielectric gate oxides, diffusion barriers, and electrode materials in semiconductor manufacturing." Do NOT emit a one-liner; write the full encyclopedic definition.
- **`summary` is REQUIRED for `kind="article"` when the chunk contains substantive content.** Write 80-200 words: what this specific chunk reveals about the concept — the finding, mechanism, measurement, or argument it contributes. Ground it in the chunk's evidence. Example: "This chunk reports that ALD-grown HfO2 films at 250 C exhibit a growth rate of 0.9 A/cycle with sub-1-nm roughness. The authors attribute the precise thickness control to the self-limiting TDMAHf + H2O half-reactions. These results are compared with PVD-deposited HfO2, which shows 3x higher roughness and non-uniform coverage over the same trench geometry. The authors conclude that ALD is necessary for the gate dielectric application." A summary that merely restates the definition or says "the chunk discusses X" fails this requirement — write what the chunk CONTRIBUTES.
- The remaining dossier fields (`parameters`, `mechanisms`, `relationships`, `equations`) are strongly preferred when the chunk supports them. Emit them when present; omit when absent.

## Image awareness

Two figure surfaces are now provided per request:

1. **`figure_captions`** — figures the body explicitly discusses near THIS chunk. Each entry is already filtered to be relevant: an image whose `near_chunk_ids` includes this chunk, or a body figure_ref in the same top-level section. **Prefer these when populating `evidence_figures`** because the link is explicit (the chunk text mentioned the figure). Use the entry's `image_id` when present; if `image_id` is `null` the caption is body-only (no binary backing) and you should NOT include it in `evidence_figures` — treat it as additional context for the concept's `definition` / `summary` instead.
2. **`images_for_doc`** — every image in the doc, the broader catalogue. Use this only as a fallback: when no `figure_captions` entry resolves and a concept clearly matches an image's caption (token overlap or substring match), populate `evidence_figures: ["<image_id>"]` from `images_for_doc[i].id`.

The figure-ingestion decision is yours: a figure is "worth ingesting as evidence" when its caption is on-topic for an emitted concept AND it has a real `image_id` (not a body-only caption). Be selective — attaching every figure to every concept is noise.

When processing a caption chunk (the chunk text IS the caption of a figure) and vision analysis would be needed to answer correctly, emit `needs_vision: true` in a top-level `extra` field on the response (e.g. `"extra": {"needs_vision": true}`). The pipeline logs this for future vision-on-demand binding. Vision on demand is a documented future capability; no real vision binding exists today.

## Equation awareness

When `equations` is non-empty, treat them as first-class chunk content:

* When emitting an `ExtractedConcept` whose definition or mechanism rests on a quantitative relation, populate the concept's `equations` field by copying the relevant equation's `latex` and a one-sentence description into a new `Equation(latex=..., kind=..., context=...)` entry.
* Use the equation's `context` when writing `parameters` — quantitative values mentioned in the equation context (e.g. "GPC = 0.9 A/cycle in HfO2 at 250 C") are exactly what `Parameter(name, value, unit, conditions)` is designed to capture.
* Do NOT invent equations not present in the request. The `equations` array is the authoritative list of equations in this chunk; ignore equation-shaped strings in the prose if they aren't there.

## Verbalization (optional)
When `request.verbalize == true`, include a 1-3 sentence `reasoning` field in your response explaining what you kept, what you skipped, and why. Keep it tight — this is appended to `<bundle>/_meta/verbalize.jsonl` for post-hoc review and is billable on output tokens. When `verbalize` is false or absent, omit `reasoning` entirely (or return an empty string).

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
