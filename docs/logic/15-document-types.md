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

**Summary extraction strategies (in `_extract_summary`):**

`Paper.abstract` has been renamed to `Paper.summary` — every document gets one, real or synthetic.

The extraction tries strategies in order, first match wins:

1. **Slide-aware synthesis** (≥3 `## Slide N` headings detected):
   - Extract title + body text + speaker notes from **first 3 slides**
   - Check **last 3 slides** for conclusion-like headings (conclusion, summary, takeaway, future work, etc.)
   - Combine: opening content + "Conclusions: ..." if found
   - Result: rich summary with both topic overview and key findings

2. **Labeled section** (abstract, summary, executive summary, overview, scope, synopsis, etc.):
   - Standard paper abstract extraction — matches heading or inline label
   - Works for papers, reports with executive summaries, proposals with scope sections

3. **First substantial prose paragraph** (>100 chars with sentence punctuation):
   - Catches reports/books that start with a meaningful opening paragraph

4. **Fallback**: first ~400 words of body text, truncated at last sentence boundary

This keeps the architecture uniform: every document gets a summary, and the rest
of the pipeline (ChromaDB embedding, LLM prompts, k-NN similarity) works unchanged.

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
