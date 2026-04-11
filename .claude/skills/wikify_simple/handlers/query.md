---
name: wikify_simple/handlers/query
description: Synthesize a short cited answer from a small evidence packet supplied by the wikify_simple retrieval half.
tier: M
dispatch_role: query
---

# query

## Context
Invoked by `wikify_simple/runtime/serve-dispatch` when a request file appears at `$WIKIFY_SIMPLE_DISPATCH_DIR/query/<rid>.request.json` (default dispatch dir: `data/dispatch/`).

This is a mechanical recipe. There is no judgment in the loop — the Python harness decides what to dispatch; you just fulfil one request per invocation. The deterministic retrieval half lives in `src/wikify_simple/distill/query.py`.

## Tier
query runs at tier M. This maps to:
- S -> haiku-class small model
- M -> sonnet-class medium model
- L -> opus-class large model

(The tier may be overridden per-request by the LLM policy via `set_tier` — read the request to confirm.)

## Request schema
Reference: `src/wikify_simple/contracts/schema.py::QueryRequest`

```json
{
  "question": "How does HfO2 enable low-voltage resistive switching?",
  "evidence": [
    {
      "page_id": "Resistive Switching",
      "page_title": "Resistive Switching",
      "body_excerpt": "Resistive switching in HfO2 proceeds via oxygen-vacancy filaments ...",
      "citations": ["Hafnium Oxide", "Memristor"]
    }
  ],
  "prompt_template": "wikify_simple/query",
  "model_id": "claude-sonnet-...",
  "tier": "M"
}
```

## Response schema
Reference: `src/wikify_simple/contracts/schema.py::QueryResponse`

```json
{
  "answer": {
    "text": "HfO2 supports low-voltage resistive switching because oxygen vacancies form conductive filaments at modest fields. ...",
    "citations": ["Resistive Switching", "Hafnium Oxide"],
    "chunks": ["doc_17::chunk_004"],
    "follow_ups": ["Memristor"]
  },
  "tokens_in": 1200,
  "tokens_out": 400
}
```

## Traversal helpers

The query handler is authorized to use these **local Python helpers** (NOT subagents) to gather additional context before composing the answer:

### `read_wiki_page(bundle, page_id) -> str | None`
Returns the full markdown of a page from the bundle. Use to read a page in full when the excerpt in the request evidence is insufficient.

### `read_corpus_chunks(corpus, chunk_ids) -> list[dict]`
Returns raw corpus chunk dicts `{id, doc_id, text}` for the given chunk ids. Use ONLY when the wiki pages do not answer the question. Each result is `{id, doc_id, text}`. Capped at 5 chunk ids per call.

### Traversal budget (per query)
- Up to **3 additional page reads** via `read_wiki_page`. Each call counts as one of the 3.
- You may follow links listed in a page's `## See also` section, ONE level at a time. One level means: read the linked page, do not then follow links from that page.
- Up to **5 corpus chunks** via `read_corpus_chunks`. Only escalate to corpus chunks when wiki pages are insufficient. Log each escalation in `escalation_events`.

### Escalation logging
Every `read_corpus_chunks` call is an escalation event. Record it:
```json
{"reason": "wiki pages did not cover the mechanism", "chunk_ids": ["c1", "c2"]}
```
These events are stored in the query log and consumed by the maintenance verb.

## Steps
1. Read the request file.
2. Inspect the supplied evidence packet. If the evidence is thin or clearly incomplete:
   a. Call `read_wiki_page` for pages cited in `evidence[i].citations` (follow-ups), up to the 3-read cap.
   b. If still insufficient, call `read_corpus_chunks` with relevant chunk ids, recording the escalation.
3. Spawn one Task subagent at tier M with:
   - System prompt: "You are the wikify_simple query responder. Answer the question using the supplied evidence. Cite pages by their `page_id`. Respond as strict JSON matching the QueryResponse schema. No commentary outside the JSON."
   - User prompt: the serialized request payload, any additional page content from step 2, and the answer rules below.
4. Receive the subagent's JSON output.
5. Validate the output against the response schema (client-side check BEFORE writing the file).
6. If validation fails, retry ONCE with a stricter prompt that repeats the schema.
7. If validation still fails, write `<rid>.error.json` next to the request with `{error: "...", last_output: "..."}` and stop.
8. If validation passes, write `<rid>.response.json` next to the request.
9. Stop. Do not loop or interpret results.

## Answer rules
- Cite pages by their `page_id`.
- Keep the answer to 3-6 sentences. One concept per sentence.
- Zero em-dashes (per the project style guide).
- If the evidence is insufficient to answer after traversal, say so explicitly and list the most relevant `page_id`s as `follow_ups`.
- Do NOT invent citations for pages or chunks you have not actually read.

## Escalation
The query handler escalates to corpus chunks (not to a higher model tier). Every corpus-chunk read must be logged in `escalation_events`. The query responder does NOT escalate model tier.

## Errors
- **Schema validation failure**: one retry with stricter prompt, then `<rid>.error.json`.
- **Subagent refusal**: one retry with a clearer prompt, then error.json.
- **Timeout**: the Python harness has a 600s timeout on polling. You have roughly that window.

## What not to do
- Do NOT read more than 3 extra pages per query.
- Do NOT read more than 5 corpus chunks per query.
- Do NOT follow links more than one level deep.
- Do NOT make multiple dispatches (one request -> one response).
- Do NOT interpret errors further than the retry logic above.
