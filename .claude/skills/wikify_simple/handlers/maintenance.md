---
name: wikify_simple/handlers/maintenance
description: Decide what wiki improvement to apply for a query log entry that was not answered well. Produce a structured MaintenanceAction and apply it via the write pipeline.
tier: L
dispatch_role: maintenance
---

# maintenance

## Context
Invoked by `wikify_simple/runtime/serve-dispatch` when a maintenance action request appears at `$WIKIFY_SIMPLE_DISPATCH_DIR/maintenance/<rid>.request.json` (default dispatch dir: `data/dispatch/`).

This is an editor-level (tier L) decision. The handler reads a query log entry and the current wiki state, decides what improvement to make, and returns a structured `MaintenanceAction`.

## Tier
maintenance runs at tier L. This maps to:
- S -> haiku-class small model
- M -> sonnet-class medium model
- L -> opus-class large model

Tier is LOCKED at L. The query log represents latent knowledge gaps; resolving them requires editor-level reasoning.

## Input

The maintenance handler receives:
- `query`: the original question text
- `answer_text`: the answer the wiki produced (may be thin or incomplete)
- `pages_touched`: list of page ids consulted to answer the query
- `escalation_events`: list of `{reason, chunk_ids}` — corpus chunks the query had to escalate to
- `related_pages`: top-5 related pages (from `inspect_related_pages`) for context
- `bundle_root`: path to the wiki bundle

## Output schema
Reference: `src/wikify_simple/contracts/schema.py::MaintenanceAction`

```json
{
  "action": "extend_page",
  "target_page": "Resistive Switching",
  "brief": "Add a section on low-voltage switching mechanisms. The query asked about HfO2 filament formation but the page lacks this detail.",
  "evidence_additions": ["doc_07__c0003__b2f1a9", "doc_12__c0011__3d8e72"],
  "rationale": "The query triggered 2 escalation events pointing to chunk doc_07__c0003 and doc_12__c0011, which contain direct evidence about HfO2 switching not yet reflected in the page.",
  "source_query_id": "abc123def456"
}
```

### `action` values
| Value | When to use |
|---|---|
| `extend_page` | The page exists but is missing sections or evidence that the query needed |
| `create_page` | No page covers the query topic at all |
| `add_evidence` | The page exists and covers the topic but lacks citations for specific claims; escalation chunks contain the missing evidence |
| `merge_pages` | Two pages cover the same topic from different angles and should be merged |

## Steps
1. Read the request.
2. Call `read_wiki_page(target_page)` to get the current page body (if it exists).
3. Spawn one Task subagent at tier L with:
   - System prompt: "You are the wikify_simple maintenance editor. Given a query that the wiki did not answer well, decide what improvement to make and produce a MaintenanceAction. Be specific: name the section to add, the evidence chunks to incorporate, and the rationale. Respond as strict JSON matching the MaintenanceAction schema."
   - User prompt: the query, the current answer, the page body (if any), the escalation events, and the related_pages context.
4. Receive the subagent's JSON output.
5. Validate against the MaintenanceAction schema (client-side).
6. If validation fails, retry ONCE with a stricter prompt.
7. If validation still fails, write `<rid>.error.json` and stop.
8. If validation passes, write `<rid>.response.json`.
9. The Python maintenance orchestration layer (`distill/maintenance.py`) applies the action via the existing write pipeline and deletes the query log entry after the page is updated.

## Decision heuristics
- **escalation_events present**: prefer `add_evidence` with `evidence_additions = [chunk_id for ev in escalation_events for chunk_id in ev.chunk_ids][:5]`.
- **pages_touched is non-empty and the page exists**: prefer `extend_page` with a brief that describes the missing section.
- **pages_touched is empty**: prefer `create_page` with the query as the seed for the page title.
- **Two related pages with `topic_overlap >= 0.80` and overlapping evidence**: prefer `merge_pages` with both page ids named in `target_page` (comma-separated).

## What not to do
- Do NOT invent chunk ids. Only use chunk ids from `escalation_events[i].chunk_ids`.
- Do NOT recommend actions for queries that are already well-answered (those are filtered out before dispatch).
- Do NOT produce vague briefs. The writer that acts on this must have a specific, actionable instruction.
- Do NOT escalate further (this handler IS the top-level editor for maintenance decisions).
