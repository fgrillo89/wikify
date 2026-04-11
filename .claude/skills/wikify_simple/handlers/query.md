# wikify_simple query skill

Synthesise a short answer from a small evidence packet. This skill is
the model-facing half of `wikify-simple query`; the deterministic
retrieval half lives in `src/wikify_simple/distill/query.py`.

## Contract

Input: one request file under
`$WIKIFY_SIMPLE_DISPATCH_DIR/query/{rid}.request.json` with shape

    {
      "question": str,
      "evidence": [
        {"page_id", "page_title", "body_excerpt", "citations": [page_id, ...]}
      ],
      "prompt_template": "wikify_simple/query/v1",
      "model_id": str,
      "tier": str
    }

Output: `{rid}.response.json` with shape

    {
      "answer": {
        "text": str,
        "citations": [page_id, ...],
        "chunks": [chunk_id, ...],
        "follow_ups": [page_id, ...]
      },
      "tokens_in": int,
      "tokens_out": int
    }

## Rules

- Use only the supplied evidence — do not read other files.
- Cite pages by their `page_id`. Never invent citations.
- Keep the answer to 3-6 sentences. One concept per sentence.
- Zero em-dashes (per the project style guide).
- If evidence is insufficient, say so and list the most relevant
  `page_id`s as follow-ups.
