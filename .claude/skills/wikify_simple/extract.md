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
   `src/wikify_simple/agents/schema.py::ExtractResponse` (chunk_id,
   concepts[{title, aliases, kind, quote}], tokens_in, tokens_out).
3. Receive the subagent's JSON response.
4. Write it to the response file path next to the request file
   (rename suffix `.request.json` -> `.response.json`).
5. Stop. Do not interpret the result.
