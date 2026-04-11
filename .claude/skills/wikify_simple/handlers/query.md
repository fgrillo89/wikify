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

## Steps
1. Read the request file.
2. Spawn one Task subagent at tier M with:
   - System prompt: "You are the wikify_simple query responder. Answer the question using ONLY the supplied evidence packet. Cite pages by their `page_id`. Respond as strict JSON matching the QueryResponse schema. No commentary outside the JSON."
   - User prompt: the serialized request payload and the answer rules below.
3. Receive the subagent's JSON output.
4. Validate the output against the response schema (client-side check BEFORE writing the file).
5. If validation fails, retry ONCE with a stricter prompt that repeats the schema.
6. If validation still fails, write `<rid>.error.json` next to the request with `{error: "...", last_output: "..."}` and stop.
7. If validation passes, write `<rid>.response.json` next to the request.
8. Stop. Do not loop or interpret results.

## Answer rules
- Use ONLY the supplied evidence — do not read other files or invent citations.
- Cite pages by their `page_id`.
- Keep the answer to 3-6 sentences. One concept per sentence.
- Zero em-dashes (per the project style guide).
- If the evidence is insufficient to answer, say so explicitly and list the most relevant `page_id`s as `follow_ups`.

## Escalation
Not supported. The query responder runs at a fixed tier and does not escalate.

## Errors
- **Schema validation failure**: one retry with stricter prompt, then `<rid>.error.json`.
- **Subagent refusal**: one retry with a clearer prompt, then error.json.
- **Timeout**: the Python harness has a 600s timeout on polling. You have roughly that window.

## What not to do
- Do NOT read other bundle files (use only what's in the request).
- Do NOT make multiple dispatches (one request -> one response).
- Do NOT interpret errors further than the retry logic above.
