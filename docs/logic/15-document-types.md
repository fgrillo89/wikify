# Document Types & Non-Article Handling

## The problem

The current pipeline assumes every document has an abstract. It's the anchor for:
- ChromaDB embedding (what gets vectorized)
- Paper summaries in LLM prompts (abstract[:200])
- k-NN similarity graph (abstract-to-abstract distance)

This works for journal articles and conference papers. It breaks for:

| Document type | Has abstract? | Structure | Example |
|---|---|---|---|
| Journal article | Yes | Sections, references | Standard — current pipeline |
| Conference paper | Usually | Sections, references | Same as above |
| Slides/PPTX | No | Flat list of slides | Lab meeting, conference talk |
| Report/thesis | Sometimes | Chapters, sections | Technical report, PhD thesis |
| Book/chapter | No (has preface) | Long chapters | Reference textbook |
| Personal notes | No | Unstructured | Literature notes, ideas, TODOs |
| Data files | No | Tabular | CSV, Excel (already supported but not embedded) |

## Proposed approach: two categories

### Source documents (papers, reports, books, slides)

These are **external knowledge** — things you're reading, not writing. They go into the
corpus and should be searchable.

**Embedding strategy for documents without abstracts:**
- Generate a **synthetic abstract** from the first ~500 tokens of content
- For slides: concatenate all slide titles + first bullet of each slide
- For books: use preface/introduction or first chapter opening
- For reports: use executive summary or introduction section
- Store as `Paper.abstract` — same field, same pipeline, no special cases

This keeps the architecture uniform: every document gets an abstract (real or synthetic),
and the rest of the pipeline works unchanged.

**Implementation**: Add a fallback in `extract_metadata()`:
```
If no abstract found AND doc_type != "article":
    abstract = first 500 tokens of content, cleaned up
```

This is a small change with big impact — slides and reports immediately become searchable.

### Personal notes (separate handling)

Personal notes are **your own thinking** — they reference papers but aren't papers
themselves. They don't belong in the same similarity graph because:

- They'd pollute k-NN results (your half-formed note is not "similar to" a paper)
- They don't have authors, years, or DOIs
- They change frequently (papers don't)
- They serve a different purpose: planning, synthesis, questions, annotations

**Proposed treatment:**
- Separate `Note` model in SQLite (not `Paper`)
- Stored in vault under `notes/` (not `papers/`)
- Chunked and indexed by section path (same chunker)
- NOT embedded in ChromaDB — not part of similarity graph
- Searchable via full-text SQLite search (FTS5) or Obsidian's own search
- Can reference papers via wikilinks (Obsidian handles this natively)
- MCP tool: `search_notes(query)` — keyword search, not semantic

**Why not embed notes?**
Notes are ephemeral and personal. Embedding them creates noise in the similarity graph
and forces re-embedding on every edit. The LLM can find relevant notes via keyword
search + wikilink traversal — the graph already connects papers to topics to notes.

## Decision matrix

| Question | Source documents | Personal notes |
|---|---|---|
| Goes in ChromaDB? | Yes (via synthetic abstract) | No |
| Has Paper record? | Yes | No (separate Note model) |
| Part of citation graph? | Yes | No |
| Part of k-NN similarity? | Yes | No |
| In vault? | Yes (`papers/`) | Yes (`notes/`) |
| Searchable? | Semantic (embeddings) | Keyword (FTS5) |
| MCP tool? | `search_papers` | `search_notes` (new) |

## What to implement now vs later

**Now (low effort, high value):**
- Synthetic abstract fallback for non-article documents
- This makes slides/reports/books immediately searchable via existing pipeline

**Later (when needed):**
- `Note` model + `notes/` vault directory
- FTS5 index for keyword search
- `search_notes` MCP tool
- Note ingestion CLI command
