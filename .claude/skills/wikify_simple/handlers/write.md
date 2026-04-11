---
name: wikify_simple/handlers/write
description: Write one full Wikipedia-style page body from an evidence packet and return a validated WriteResponse.
tier: M
dispatch_role: write
---

# write

## Context
Invoked by `wikify_simple/runtime/serve-dispatch` when a request file appears at `$WIKIFY_SIMPLE_DISPATCH_DIR/write/<rid>.request.json` (default dispatch dir: `data/dispatch/`).

This is a mechanical recipe. There is no judgment in the loop — the Python harness decides what to dispatch; you just fulfil one request per invocation.

## Tier
write runs at tier M. This maps to:
- S -> haiku-class small model
- M -> sonnet-class medium model
- L -> opus-class large model

(The tier may be overridden per-request by the LLM policy via `set_tier` — read the request to confirm.)

## Request schema
Reference: `src/wikify_simple/contracts/schema.py::WriteRequest`

```json
{
  "page_id": "Atomic Layer Deposition",
  "title": "Atomic Layer Deposition",
  "corpus_persona": "You are writing for a corpus centered on thin-film memory devices ...",
  "style_guide": "Neutral declarative voice. Short sentences. No em-dashes ...",
  "field_guide": "Use SI units. Prefer 'thin film' over 'thin-film' as a noun ...",
  "artifact_template": "Wikipedia concept article with optional sections ...",
  "brief": {
    "article_register": "academic",
    "lead_paragraph_instruction": "Define ALD as ...",
    "sections": [{"heading": "## Mechanism", "instruction": "...", "evidence_markers": ["e1","e3"], "zone": "established", "parameters_to_include": []}],
    "comparative_notes": "Unlike CVD, ALD is self-limiting ...",
    "figures_to_embed": ["Figure 3"],
    "max_length_chars": 5000
  },
  "evidence_v2": [
    {"marker": "e1", "chunk_id": "...", "doc_id": "...", "quote": "...", "page_title": "...", "summary": "..."}
  ],
  "figures": [{"path": "figures/doc_17_fig3.png", "label": "Figure 3", "caption": "ALD reactor schematic"}],
  "prompt_template": "wikify_simple/write",
  "model_id": "claude-sonnet-...",
  "tier": "M"
}
```

## Response schema
Reference: `src/wikify_simple/contracts/schema.py::WriteResponse`

```json
{
  "page_id": "Atomic Layer Deposition",
  "body_markdown": "Atomic layer deposition (ALD) is a self-limiting vapor-phase technique ... [^e1] ...\n\n## Mechanism\n\n...\n\n## References\n\n[^e1]: chunk_004 (doc_17) > \"Atomic layer deposition (ALD) is a thin-film growth technique\"\n",
  "used_markers": ["e1", "e3"],
  "tokens_in": 3200,
  "tokens_out": 2100
}
```

## Steps
1. Read the request file.
2. Spawn one Task subagent at tier M (or whatever the request's `tier` field says) with:
   - System prompt: concatenate the supplied prompt layers in this order — `corpus_persona`, `style_guide`, `field_guide`, `artifact_template` — then append the floor constraints and validator rules below. If the request supplies an editor `brief`, treat it as the authoritative section plan and append it after the layer stack.
   - User prompt: the request-specific content (title, evidence_v2, figures, page_id, any remaining fields) and the WriteResponse schema.
3. Receive the subagent's JSON output.
4. Validate the output against the response schema AND the Wikipedia-structure checks below (client-side, BEFORE writing the file).
5. If validation fails, retry ONCE with a stricter prompt that repeats the schema and the specific constraint that failed.
6. If validation still fails, write `<rid>.error.json` next to the request with `{error: "...", last_output: "..."}` and stop.
7. If validation passes, write `<rid>.response.json` next to the request.
8. Stop. Do not loop or interpret results.

## Floor constraints (apply even if layer strings are empty)
The page must read like a real Wikipedia entry: connected prose paragraphs, neutral declarative voice, faithful to the supplied evidence. It must NOT read like a list of related concepts, a table of crosslinks, or a stack of bullet points.

Voice and style:
- Wikipedia voice: neutral, declarative, third person.
- Connected prose paragraphs. One concept per sentence.
- Short sentences. No em-dashes as parenthetical separators.
- No meta-commentary ("this article covers...", "in summary...", "in this corpus we see...").
- Do not invent claims that are not supported by the evidence list.
- Do NOT use `[[wikilinks]]` anywhere in the body. A separate crosslink pass populates the page frontmatter; the body stays clean.
- Cite evidence using `[^eN]` markers (1-based into the evidence list).

Figure placement:
- When `figures` is supplied, mention each figure by its label ("as shown in Figure 3") inside the relevant section. On the line IMMEDIATELY after the sentence that references it, embed the figure as `![Figure N](<figure.path>)`. Never group figures at the top. Skip figures that do not fit.

Sections are GUIDANCE, not strict. Drop or reorder sections to fit the actual evidence; add extras (`## Performance`, `## Variants`, `## Alternative Explanations`, ...) when the material calls for them.

## Validator (matches `contracts/schema.py::_check_wikipedia_structure`)
- Total body length >= 1200 characters.
- No `[[wikilinks]]` anywhere in the body.
- At least one `## H2` heading.
- At least 3 non-blank paragraphs of prose outside the References section.
- At least one `[^eN]` marker somewhere in the prose.
- A final `## References` section containing at least one `[^eN]:` definition.
- Every `[^eN]` marker in the prose must resolve to a matching `[^eN]:` definition in References.
- Every `![Figure N](path)` embed must be textually referenced on the immediately preceding non-blank prose line.

### The `[^eN]:` reference format (CRITICAL — do not simplify)

The References section MUST use the full-chunk-id format, and the chunk_id MUST come from the request's `evidence[i].chunk_id` field verbatim:

```
[^eN]: <full_chunk_id> (<doc_id>) > "<exact_quote>"
```

Example — if `evidence[0]` contains:
```json
{"chunk_id": "[2008 Strukov] The missing memristor found_b5610c500e6b__c0000__1f0ed598", "doc_id": "[2008 Strukov] The missing memristor found_b5610c500e6b", "quote": "which he called a memristor"}
```

Then the reference line is:
```
[^e1]: [2008 Strukov] The missing memristor found_b5610c500e6b__c0000__1f0ed598 ([2008 Strukov] The missing memristor found_b5610c500e6b) > "which he called a memristor"
```

**Common mistake to avoid**: do NOT drop the `__c####__hex` suffix from chunk_id or write `[^e1]: <doc_id> > "quote"`. The eval harness looks up the chunk by the full chunk_id; stripping the suffix causes the grounding gate (M6 g2) to report zero resolvable markers.

### Pre-submit checklist (run it mentally before writing the response file)
1. Body length >= 1200 characters? If short, expand Background/Mechanism with supporting prose.
2. Count in-prose `[^eN]` markers. Count must be >= 1.
3. For every in-prose marker, find the matching `[^eN]:` definition in References. No orphans.
4. For every `[^eN]:` definition, verify the chunk_id includes the `__c####__hex` suffix (copy from `evidence[i].chunk_id`).
5. No `[[wikilinks]]` anywhere.
6. At least 3 paragraphs of prose outside References.
7. Every `![Figure N](path)` has a preceding line mentioning "Figure N".

## Escalation
Supported. The writer should escalate when:
- the evidence list is internally contradictory,
- cross-domain synthesis is required (multiple distinct sub-topics must be reconciled),
- the editor brief calls for synthesis beyond any single source.

When the subagent emits `{"escalate": true, "reason": "..."}` in a top-level `escalation` field:
1. Spawn a nested Task subagent at tier L (opus) with the original request + the escalation reason.
2. Use the opus subagent's output as the final response.
3. Record the escalation in a `meta.escalated_from` field so the cost meter attribution is visible.

The escalation happens INSIDE the skill invocation. It does NOT write a separate dispatch file. It costs more tokens but stays local.

## Errors
- **Schema validation failure**: one retry with stricter prompt, then `<rid>.error.json`.
- **Wikipedia-structure failure** (missing references, short body, stray `[[wikilinks]]`, orphan markers): one retry naming the specific constraint, then error.json.
- **Subagent refusal**: one retry with a clearer prompt, then error.json.
- **Timeout**: the Python harness has a 600s timeout on polling. You have roughly that window.

## What not to do
- Do NOT read other bundle files (use only what's in the request).
- Do NOT make multiple dispatches (one request -> one response).
- Do NOT interpret errors further than the retry logic above.
- Do NOT introduce a fixed required-sections table: sections are guidance only.
