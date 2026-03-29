# Generation Pipeline

## Paper generation (3 LLM calls minimum)

**Step 1 — Plan** (`planner.py`):
- System prompt defines JSON schema for outline
- User prompt includes: writing prompt + paper summaries + graph metrics
- LLM returns: `{title, sections: [{heading, description, target_tokens, source_papers, subsections}]}`
- ~250 words/page target

**Step 2 — Write** (`writer.py`):
- Sections flattened depth-first (parent -> children)
- Each section: 1 LLM call with system prompt + prior 3 sections (coherence) + literature context (8k chars)
- LLM writes body text only (heading added programmatically)
- All sections assembled into final markdown

**Step 3 — Export**:
- Paper: markdown file to `data/output/review.md`
- Slides: JSON array -> python-pptx PPTX file

## Slides generation
- Single LLM call returns JSON array of `{title, bullets, notes, source_papers}`
- Exported to PPTX with title slide + content slides

## Chat
- Single-turn RAG: retrieve chunks for query -> build system prompt with literature -> LLM answers
- Multi-turn: keeps last 6 turns of history, re-retrieves for each new query
- Cache disabled for chat (responses should vary)

## LLM client details
- All calls go through litellm (provider-agnostic)
- Responses cached to disk via diskcache (SHA-256 of model+messages+kwargs)
- `complete_json()` strips markdown fences, falls back to brace/bracket boundary detection
- Early API key validation for Anthropic models

## Where the code lives
- `generate/planner.py` — outline generation
- `generate/writer.py` — section-by-section writing
- `generate/chat.py` — interactive Q&A
- `llm/client.py` — litellm wrapper + caching
- `export/pptx_export.py` — PPTX output
