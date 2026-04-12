---
name: wikify_simple/workflows/ask
description: Ask one question of a wiki bundle.
---

# ask

User-facing workflow for querying a wiki bundle.

## Inputs

| Parameter | Default | Description |
|---|---|---|
| `bundle` | — | Path to the wiki bundle (required). |
| `question` | — | The question to ask (required). |

## Steps

1. Verify `bundle` exists.
2. Verify a serve-dispatch session is running (or start one in parallel).
3. Run: `uv run python -m wikify_simple.cli query --bundle {bundle} "{question}"`
4. The Python retrieval builds a small evidence packet deterministically, writes ONE query dispatch request, blocks polling.
5. The serve-dispatch session invokes `handlers/query` to synthesize an answer at tier M (sonnet).
6. Python receives the response and writes it under `data/queries/...`.
7. Print the result path and preview the answer.

## Outputs
- Markdown file at `data/queries/<bundle_name>/<timestamp>.md` with the answer, citations, and follow-ups.
- The bundle itself is NEVER mutated by a query.

## Failure modes
- Bundle missing → abort with a clear message.
- serve-dispatch not running → harness hangs for 600s then times out.
- No evidence retrieved deterministically → Python returns an empty evidence packet; the handler answers with "insufficient evidence" and lists follow-ups.
