---
name: wikify_simple/extract
description: Mechanical recipe — fulfil one extract dispatch request from the wikify_simple harness.
---

# extract

This is a mechanical recipe with no judgment. The harness has written a
request file at `data/dispatch/extract/{rid}.request.json`. Your job is to
read it, run one Task subagent with the included prompt + schema, and
write the validated JSON response to `data/dispatch/extract/{rid}.response.json`.

Steps:

1. Read the request file with the Read tool.
2. Spawn one Task subagent (haiku tier) with this prompt: "Read this
   chunk and extract any concepts or person names that would deserve a
   wiki page. Respond as strict JSON matching the schema." Pass the
   `chunk_text`, `canonical_titles`, and the JSON schema from
   `src/wikify_simple/agents/schema.py::ExtractResponse`:
   `chunk_id, concepts[{title, aliases, kind, quote, category?, evidence_figures?}], tokens_in, tokens_out`.

   Rules the subagent must follow (slice 6+):
   - `kind` is EXACTLY `"concept"` or `"person"` — it routes the page to
     `concepts/` or `people/`.
   - `category` is a facet tag, not a type. Allowed values:
     `phenomenon | method | material | device | theory | metric |
     organization | other`, or `null`/omitted. MUST be `null` for
     `kind="person"`.
   - `title` 2..120 chars, not a stopword, no edge punctuation.
   - `aliases` deduped case-insensitively, drop entries equal to title,
     max 8 entries.
   - `quote` 5..400 chars AFTER stripping and MUST be a verbatim
     substring of `chunk_text` (copy-paste, do not paraphrase). Verify
     by literal substring search before emitting.
3. Receive the subagent's JSON response.
4. Write it to the response file path next to the request file
   (rename suffix `.request.json` -> `.response.json`).
5. Stop. Do not interpret the result.
