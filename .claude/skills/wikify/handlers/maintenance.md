---
name: wikify/handlers/maintenance
description: Decide what wiki improvement to apply for a query log entry that was not answered well. Produce a structured MaintenanceAction and apply it via the write pipeline.
tier: L
dispatch_role: maintenance
---

> **DEPRECATED**: dispatch-era handler, scheduled for deletion after baseline parity lands. See `docs/skill-centric-pivot.md`.

# maintenance

## Context
Invoked by `wikify/runtime/serve-dispatch` when a maintenance action request appears at `$WIKIFY_DISPATCH_DIR/maintenance/<rid>.request.json` (default dispatch dir: `data/dispatch/`).

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
Reference: `src/wikify/schema.py::MaintenanceAction`

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
   - System prompt: "You are the wikify maintenance editor. Given a query that the wiki did not answer well, decide what improvement to make and produce a MaintenanceAction. Be specific: name the section to add, the evidence chunks to incorporate, and the rationale. Respond as strict JSON matching the MaintenanceAction schema."
   - User prompt: the query, the current answer, the page body (if any), the escalation events, and the related_pages context.
4. Receive the subagent's JSON output.
5. Validate against the MaintenanceAction schema (client-side).
6. If validation fails, retry ONCE with a stricter prompt.
7. If validation still fails, write `<rid>.error.json` and stop.
8. If validation passes, write `<rid>.response.json`.
9. The Python maintenance orchestration layer (`distill/maintenance.py`) applies the action via the existing write pipeline and deletes the query log entry after the page is updated.

## Knowledge Graph evidence discovery

The maintenance handler has access to both corpus and wiki graphs.

- Corpus KG API: `.claude/skills/wikify/reference/knowledge-graph.md`
- Wiki KG API: `.claude/skills/wikify/reference/wiki-graph.md`

### Wiki graph: diagnose wiki gaps

```python
wkg = preloaded.wiki_knowledge_graph

# Is the question's topic covered at all?
hits = wkg.search(query, top_k=3)
if not hits or hits[0]["score"] < 0.5:
    # No page covers this topic -> create_page

# Is the page thin?
page = wkg.page(target_page_id).first()
if page and page["n_evidence"] < 3:
    # Needs more evidence -> add_evidence or extend_page

# Are two pages duplicating coverage?
co = wkg.page(target_page_id).co_evidence()
for neighbor in co.collect():
    hits = wkg.search(neighbor["title"], top_k=1)
    if hits and hits[0]["score"] > 0.85 and hits[0]["id"] != neighbor["id"]:
        # Merge candidate
        pass
```

### Corpus KG: find missing evidence

Use the corpus KG to find evidence the wiki is missing:

```python
kg = preloaded.knowledge_graph

# Find chunks about the query topic from the whole corpus
kg.search("the query topic", top_k=10)

# Find what foundation papers say about the topic
kg.sources().top(5, by="pagerank").chunks().search("topic", top_k=5)

# Find evidence from papers citing a specific source
kg.source(doc_id).cited_by().chunks().search("missing aspect", top_k=5)

# Find equations or figures that should be in the page
kg.sources().equations().search("relevant model", top_k=3)
kg.sources().figures().search("relevant diagram", top_k=3)

# Check if a concept is covered across multiple sources
kg.search("concept", top_k=20)  # -> count unique source_ids
```

Use KG results to populate `evidence_additions` with specific chunk IDs
that the writer should incorporate.

## Decision heuristics
- **escalation_events present**: use KG to expand evidence beyond the escalated chunks. The escalation chunks are a starting point; use `kg.source(doc_id).cited_by().chunks().search(query)` to find corroborating evidence from related papers.
- **pages_touched is non-empty and the page exists**: prefer `extend_page`. Use KG to identify which sections are missing by comparing the page's evidence sources against what the KG knows about the topic.
- **pages_touched is empty**: prefer `create_page`. Use `kg.search(query)` to find the best evidence chunks for the new page.
- **Two related pages with `topic_overlap >= 0.80` and overlapping evidence**: prefer `merge_pages`.

## What not to do
- Do NOT invent chunk ids. Use chunk ids from `escalation_events[i].chunk_ids` or from KG traversal results.
- Do NOT recommend actions for queries that are already well-answered (those are filtered out before dispatch).
- Do NOT produce vague briefs. The writer that acts on this must have a specific, actionable instruction.
- Do NOT escalate further (this handler IS the top-level editor for maintenance decisions).
