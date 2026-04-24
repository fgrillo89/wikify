---
name: wikify/handlers/query
description: Synthesize a short cited answer from a small evidence packet supplied by the wikify retrieval half.
tier: M
dispatch_role: query
---

> **DEPRECATED**: dispatch-era handler, scheduled for deletion after baseline parity lands. See `docs/skill-centric-pivot.md`.

# query

## Context
Invoked by `wikify/runtime/serve-dispatch` when a request file appears at `$WIKIFY_DISPATCH_DIR/query/<rid>.request.json` (default dispatch dir: `data/dispatch/`).

This is a mechanical recipe. There is no judgment in the loop — the Python harness decides what to dispatch; you just fulfil one request per invocation. The deterministic retrieval half lives in `src/wikify/distill/query.py`.

## Tier
query runs at tier M. This maps to:
- S -> haiku-class small model
- M -> sonnet-class medium model
- L -> opus-class large model

(The tier may be overridden per-request by the guided mode via `set_tier` — read the request to confirm.)

## Request schema
Reference: `src/wikify/schema.py::QueryRequest`

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
  "prompt_template": "wikify/query",
  "model_id": "claude-sonnet-...",
  "tier": "M"
}
```

## Response schema
Reference: `src/wikify/schema.py::QueryResponse`

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

The query handler is authorized to use these **local Python helpers** (NOT subagents) to gather additional context before composing the answer.

### Wiki page reads

**`read_wiki_page(bundle, page_id) -> str | None`**
Returns the full markdown of a page from the bundle. Use to read a page in full when the excerpt in the request evidence is insufficient.

### Wiki graph: page discovery and navigation

The query handler has access to the Wiki Knowledge Graph for finding
relevant pages. See `.claude/skills/wikify/reference/wiki-graph.md`.

```python
wkg = preloaded.wiki_knowledge_graph

# Find pages about the query topic
wkg.search("the query topic", top_k=5)

# Find pages related to a known page (co-evidence, links)
wkg.page("Resistive Switching").co_evidence().collect()
wkg.page("Resistive Switching").links().collect()

# Check if any page covers a subtopic
wkg.search("filament formation mechanism", top_k=3)
```

### Corpus KG: deep evidence retrieval

The query handler has full access to the corpus Knowledge Graph for
finding evidence the wiki pages missed.
See `.claude/skills/wikify/reference/knowledge-graph.md` for the complete API.

Use the corpus KG to discover evidence the wiki pages missed:

```python
kg = preloaded.knowledge_graph

# Find chunks about a topic from the most-cited papers
kg.sources().top(5, by="pagerank").chunks().search("the query topic", top_k=5)

# Find what a specific paper says about a concept
kg.source(doc_id).chunks().search("concept from the question", top_k=3)

# Find related authors and their work
kg.author("smith j").sources().chunks().search("topic", top_k=3)

# Foundation check: if a paper is highly cited, get its full sections
source = kg.source(doc_id)
if source.cited_by().count() > 3:
    context = source.sections(type="conclusions").chunks().collect()

# Equation context: find equations related to the question
kg.sources().equations().search("equation topic", top_k=3)

# Figure context: find figures discussing a concept
kg.sources().figures().search("IV curve characteristic", top_k=3)
```

**Prefer KG traversal over raw chunk reads.** The KG scopes vector search
to graph neighborhoods, producing higher-precision results than global search.
Use the Librarian decision pattern: foundation papers get full sections,
specific references get targeted search.

### Traversal budget (per query)
- Up to **3 additional page reads** via `read_wiki_page`.
- Up to **5 KG traversal chains** that produce chunks. Each `search()` or `collect()` that returns chunks counts as one traversal.
- You may follow links listed in a page's `## See also` section, ONE level at a time.

### Escalation logging
Every KG traversal that retrieves corpus chunks is an escalation event. Record it:
```json
{"reason": "wiki pages did not cover the mechanism", "chunk_ids": ["c1", "c2"]}
```
These events are stored in the query log and consumed by the maintenance verb.

## Steps
1. Read the request file.
2. Inspect the supplied evidence packet. If the evidence is thin or clearly incomplete:
   a. Call `read_wiki_page` for pages cited in `evidence[i].citations` (follow-ups), up to the 3-read cap.
   b. If still insufficient, use the Knowledge Graph to discover relevant corpus chunks:
      - Use `kg.search(question, top_k=5)` for broad topic search.
      - Use `kg.source(doc_id).chunks().search(question, top_k=3)` for targeted source search.
      - Use `kg.source(doc_id).cited_by().chunks().search(question, top_k=3)` for citation-chain discovery.
      - Apply the Librarian foundation-vs-specific pattern: foundation papers (cited by >3) get full sections, others get targeted search.
      - Record each KG traversal as an escalation event.
3. Spawn one Task subagent at tier M with:
   - System prompt: "You are the wikify query responder. Answer the question using the supplied evidence. Cite pages by their `page_id`. Respond as strict JSON matching the QueryResponse schema. No commentary outside the JSON."
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
