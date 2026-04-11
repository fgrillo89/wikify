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

## Prompt-layer caching (vendor-neutral)

The request carries two representations of each stable prompt layer:

- **Inline strings** (`style_guide`, `field_guide`, `artifact_template`, `corpus_persona`): always present; used directly by fake/heuristic bindings.
- **Hash fields** (`style_guide_hash`, `field_guide_hash`, `artifact_template_hash`, `corpus_persona_hash`): sha256[:16] hex, present when the pipeline wrote layer files to disk.

**In a serve-dispatch session** (file_dispatch binding), maintain a session-scoped `{hash: text}` dict in memory. On each write dispatch:
1. For each non-null hash field in the request, check whether the hash is already in the session cache.
2. If not cached, read `<bundle_root>/_meta/prompt_layers/<hash>.md` and store in cache.
3. Compose the system prompt from cached (or inline) layer text in the order: `corpus_persona`, `style_guide`, `field_guide`, `artifact_template`.
4. The inline strings are used as fallback if hashes are absent or the layer file is missing.

**VENDOR NEUTRAL**: Do NOT use Anthropic prompt-caching primitives (`cache_control`) or OpenAI system-message caching APIs. The cache lives in Python/Claude Code session memory only. This design is compatible with adding vendor caching later (the hashes are stable `cache_control` candidates) but does not depend on it.

## Request schema
Reference: `src/wikify_simple/contracts/schema.py::WriteRequest`

```json
{
  "page_id": "Atomic Layer Deposition",
  "title": "Atomic Layer Deposition",
  "corpus_persona": "You are writing for a corpus centered on thin-film memory devices ...",
  "corpus_persona_hash": "a1b2c3d4e5f60718",
  "style_guide": "Neutral declarative voice. Short sentences. No em-dashes ...",
  "style_guide_hash": "9f8e7d6c5b4a3210",
  "field_guide": "Use SI units. Prefer 'thin film' over 'thin-film' as a noun ...",
  "field_guide_hash": "0102030405060708",
  "artifact_template": "Wikipedia concept article with optional sections ...",
  "artifact_template_hash": "deadbeef12345678",
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
  "tokens_out": 2100,
  "extends_page_id": null
}
```

`extends_page_id`: set to the page id of an existing article if you extended it (see step 2d), or `null` for a new article. This field is how the pipeline detects extend-vs-create decisions.

## Steps
1. Read the request file.
2. **Related-wiki check (MANDATORY before writing):**
   a. Inspect `request.related_pages` (list of up to 5 related pages, each with `{id, title, topic_overlap, body_excerpt, see_also, evidence_doc_ids}`).
   b. If any entry has `topic_overlap >= 0.80`, the existing page already covers this topic substantially. Prefer extending it over creating a new page. Call `read_wiki_page(page_id)` to retrieve its full markdown before deciding (this is a local Python call — NOT a subagent).
   c. If a related page is distinct but relevant (lower overlap), you may cite it in prose and list it under `## See also`. You are authorized to follow one level of `## See also` or evidence links on a related page to pull additional context when the current evidence is ambiguous. One level only — do not recurse.
   d. Decide: are you (i) creating a new article, or (ii) extending an existing one? Record this decision. The `WriteResponse` must carry `extends_page_id` — set it to the extended page's id if you chose (ii), or leave it null for a new article.
3. Resolve each prompt layer: for each layer, if a hash field is present, look up the session-scoped cache (see "Prompt-layer caching" above). If not cached, read from `_meta/prompt_layers/<hash>.md`. Fall back to the inline string if the file is missing or hash is null.
4. Spawn one Task subagent at tier M (or whatever the request's `tier` field says) with:
   - System prompt: concatenate the resolved prompt layers in this order — `corpus_persona`, `style_guide`, `field_guide`, `artifact_template` — then append the floor constraints and validator rules below. If the request supplies an editor `brief`, treat it as the authoritative section plan and append it after the layer stack.
   - User prompt: the request-specific content (title, evidence_v2, figures, page_id, any remaining fields), the `related_pages` context, the related-wiki decision from step 2, and the WriteResponse schema.
5. Receive the subagent's JSON output.
6. Validate the output against the response schema AND the Wikipedia-structure checks below (client-side, BEFORE writing the file).
7. If validation fails, retry ONCE with a stricter prompt that repeats the schema and the specific constraint that failed.
8. If validation still fails, write `<rid>.error.json` next to the request with `{error: "...", last_output: "..."}` and stop.
9. If validation passes, write `<rid>.response.json` next to the request.
10. Stop. Do not loop or interpret results.

## Wikipedia MoS references (authoritative source)

- Layout: https://en.wikipedia.org/wiki/Wikipedia:Manual_of_Style/Layout
- Biography: https://en.wikipedia.org/wiki/Wikipedia:Manual_of_Style/Biography

## Floor constraints (apply even if layer strings are empty)

The page must read like a real Wikipedia entry: connected prose paragraphs,
neutral declarative voice, faithful to the supplied evidence. It must NOT
read like a list of related concepts, a table of crosslinks, or a stack of
bullet points.

### Article pages (`kind="article"`)

Structure per Wikipedia MoS/Layout:

1. **Lead** (no heading): bold title in first sentence, one-sentence IS
   definition, then context. No bullets.
2. **Body** (at least 2 topical `## H2` sections before the appendix group).
   Common labels: `## Background`, `## Mechanism`, `## Process`, `## Theory`,
   `## Applications`, `## Uses`, `## Specifications`, `## Characterization`.
   Topic-specific substitutions are allowed; the requirement is at least 2.
3. **Appendix group** in this order: `## See also` (optional) then
   `## References` (required last).

### Person pages (`kind="person"`)

Structure per Wikipedia MoS/Biography:

1. **Lead** (no heading): `**Full Name** (year-range) is a [field] [role]
   known for [contribution].` For mentioned-only persons (no `author_context`):
   `**Name** is credited with [specific contribution].[^e1]`
2. **Body** (at least 2 `## H2` sections before References). Required:
   `## Research` or `## Contributions`. Include `## Publications` only when
   `author_context.primary_publications` is non-empty; format as a real
   blank-line-separated Markdown list.
3. Optional sections when evidence supports: `## Education`, `## Career`,
   `## Collaborations`, `## Legacy`.
4. `## References` required last.

### Voice and style (all page kinds)

- Wikipedia voice: neutral, declarative, third person.
- Connected prose paragraphs. One concept per sentence.
- Short sentences. No em-dashes as parenthetical separators.
- Do not invent claims that are not supported by the evidence list.
- Do NOT use `[[wikilinks]]` anywhere in the body. A separate crosslink pass
  populates the page frontmatter; the body stays clean.
- Cite evidence using `[^eN]` markers (1-based into the evidence list).

Figure placement:
- When `figures` is supplied, mention each figure by its label ("as shown in Figure 3") inside the relevant section. On the line IMMEDIATELY after the sentence that references it, embed the figure as `![Figure N](<figure.path>)`. Never group figures at the top. Skip figures that do not fit.
- Prefer figures whose ID appears in `evidence_v2[i].evidence_figures` — these were flagged by the extractor as directly relevant to the concept being described.

### Banned phrases (project-wide, enforced here)

Never write:

- "in this corpus"
- "appears in this corpus"
- "mentioned in this corpus only through citations"
- "this corpus contains"
- "in this article"
- "as discussed above"
- first-person references to the work ("we examine", "we show", "our analysis")

### Figure placement

When `figures` is supplied, mention each figure by its label ("as shown in
Figure 3") inside the relevant section. On the line IMMEDIATELY after the
sentence that references it, embed the figure as `![Figure N](<figure.path>)`.
Never group figures at the top. Skip figures that do not fit.

**Article pages (kind=article) minimum structure**: At least two `## H2` sections must precede `## References`. Recommended labels include `## Definition`, `## Background`, `## Mechanism`, `## Applications`, `## Open Questions`, `## Significance` — but these are suggestions, not required names. Choose headings that fit the actual evidence. The rule is "at least 2 topical H2 sections before the appendix group", not "these specific labels".

Appendix order: `## See also` (optional) -> `## References` (required last).

## Writing a person page (`kind=person`)

Person pages are biographical articles in Wikipedia voice, not deterministic author stubs.

**Opening sentence**: Start with the person's full name in **bold**, followed by a factual role/field descriptor drawn from the evidence. Use the `author_context.primary_publications` titles (if present) to infer the research area. Example: `**Alice Adams** is a materials scientist whose work focuses on atomic layer deposition for memristive devices.`

**BANNED phrasing** (never write these — they signal the old deterministic output):
- "X appears in this corpus..."
- "X appears in this corpus only through citations..."
- "mentioned in this corpus"
- "in this corpus"
- "this corpus contains"

**Required structure** (at least 2 in-body H2 sections before `## References`):
- `## Biography` or `## Background` — career context and research timeline
- `## Contributions` or `## Research` — what the person discovered or developed, grounded in evidence chunks
- Optionally: `## Collaborations`, `## Notable works`, `## Legacy`
- `## References` (required last)

**Using `author_context`** (when present in the request):
- `primary_publications`: titles and years of the person's papers in the corpus. Use as grounded facts about research topics and timeline. Do NOT cite the `author_context` field itself — cite via the `[^eN]` evidence markers that accompany the request.
- `cited_works`: works this person has been cited for; useful for scoping their broader contributions.
- `collaborators`: names of co-authors; may be mentioned in prose as "Adams worked closely with [name]".
- `year_range`: earliest and latest publication years; use to frame the temporal scope of their work.

**When `author_context` is absent** (person mentioned in text but not a corpus author):
- Write only from the evidence chunks. Do not speculate about unconfirmed affiliations, dates, or fields.
- The lead may be shorter: `**Name** is credited with [specific contribution grounded in evidence].`

**Publication lists**: if you include a publications section, use real markdown list syntax with a blank line before each `- ` item so the renderer produces a `<ul>`. Do not follow a bullet item immediately with another bullet item without a blank line between them.

## Validator (matches `contracts/schema.py::_check_wikipedia_structure`)
- Total body length >= 1200 characters.
- No `[[wikilinks]]` anywhere in the body.
- At least one `## H2` heading.
- **For article and person pages**: at least 2 non-appendix `## H2` headings before `## References`. Appendix headings that do NOT count toward this minimum: `References`, `Notes and References`, `See also`, `Further reading`, `External links` (case-insensitive).
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
8. For article and person pages: at least 2 topical `## H2` sections before `## References`. "Topical" means NOT one of: References, Notes and References, See also, Further reading, External links.

## Verbalization (optional)
When `request.verbalize == true`, include a 1-3 sentence `reasoning` field in your response summarising the editorial choices: which structure you picked, which evidence you foregrounded, and anything you deliberately deferred or skipped (e.g. missing sections, thin evidence, conflicting sources). Keep it tight — the pipeline appends it to `<bundle>/_meta/verbalize.jsonl` for review and it is billed on output tokens. When `verbalize` is false or absent, omit `reasoning` or return an empty string.

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
- Do NOT introduce new artifact types or page kinds not in the request.
- Do NOT omit the lead paragraph or start with a `## H2` heading as the first line.
