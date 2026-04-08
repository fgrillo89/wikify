---
name: wikify_simple/write
description: Mechanical recipe — fulfil one write dispatch request from the wikify_simple harness.
---

# write

The harness has written `data/dispatch/write/{rid}.request.json`. Read
it, spawn one Task subagent (sonnet or opus tier per the request's
`tier` field), and write the JSON response next to the request.

Steps:

1. Read the request file.
2. Spawn one Task subagent with this prompt: "You are writing a wiki
   page. Use the title, evidence quotes, and skeleton supplied. Anchor
   every factual sentence with a [^eN] marker referencing the supplied
   evidence list. Respond as strict JSON: {page_id, body_markdown,
   used_markers, tokens_in, tokens_out}."
3. Pass the request fields verbatim into the subagent prompt.
4. Write the response JSON to `{rid}.response.json`.
5. Stop.
