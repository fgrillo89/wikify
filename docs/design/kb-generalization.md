# ScholarForge Knowledge Base Generalization

## Vision

ScholarForge evolves from an academic-paper pipeline into a general-purpose personal knowledge
base system. The user collects raw sources of any kind, ScholarForge enriches them with
semantic structure, and an LLM builds and maintains a curated wiki on top of that structure.
All three layers are separate, composable, and independently queryable.

## Three-Layer Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: Curated Wiki                data/wiki/            │
│  LLM-authored concept articles, synthesis notes, gap        │
│  analyses. Progressive: stubs grow into full articles as     │
│  the corpus expands. Self-linking. Maintained by the LLM.  │
└─────────────────┬───────────────────────────────────────────┘
                  │ reads / cites
┌─────────────────▼───────────────────────────────────────────┐
│  Layer 2: Enriched Index              data/ (existing)      │
│  ChromaDB embeddings, SQLite graph, topic tags, citation    │
│  graph, vibes, section summaries. Derived from raw.         │
│  Recomputable. Domain-agnostic already.                     │
└─────────────────┬───────────────────────────────────────────┘
                  │ ingests
┌─────────────────▼───────────────────────────────────────────┐
│  Layer 1: Raw Sources                 data/raw/             │
│  Original files. Immutable. Any format: PDF, DOCX, PPTX,   │
│  HTML, Markdown, images, code. Never modified after ingest. │
└─────────────────────────────────────────────────────────────┘
```

**Key principle:** Each layer has one direction of dependency. The wiki reads from the enriched
index but never writes back to the raw layer. Outputs (papers, reports) are generated from
the wiki + index but remain separate unless explicitly promoted.

---

## Layer 1: Generalized Ingest

### Current state
`ingest/service.py` handles PDF, DOCX, PPTX. `store/models.py:Paper` has `doc_type` (paper,
report, proposal, note, presentation, other) and `origin` (CORPUS, GENERATED).

### What changes

Rename `Paper` → `Source` (or keep `Paper` as the SQLModel table name for migration
compatibility, but expose it everywhere as `Source`). Add `source_type` to the existing
`doc_type` enum:

```python
class SourceType(str, enum.Enum):
    # Academic
    paper = "paper"
    report = "report"
    proposal = "proposal"
    thesis = "thesis"
    # Web / Knowledge
    web_article = "web_article"   # HTML clip or fetched page
    markdown = "markdown"         # Obsidian note, README, blog post
    wiki_article = "wiki_article" # promoted from Layer 3
    # Rich media
    image = "image"               # diagram, figure, screenshot with alt-text
    # Code
    repo_readme = "repo_readme"   # README + top-level docs from a repo
    # Keep existing
    presentation = "presentation"
    note = "note"
    other = "other"
```

Add new ingester modules alongside the existing PDF/DOCX/PPTX ones:

| Module | Handles | Key parsing step |
|--------|---------|-----------------|
| `ingest/markdown.py` | `.md`, `.txt` | Frontmatter extraction, heading-based chunking |
| `ingest/html.py` | `.html`, web clips | trafilatura or readability → markdown, then markdown pipeline |
| `ingest/image.py` | `.png`, `.jpg`, `.webp` | alt-text / caption → summary chunk; no embeddings for now |

`ingest/service.py:ingest_file()` extends its format dispatch to these new types. No other
caller changes — the rest of the pipeline already works on chunks.

### Raw file conventions

```
data/raw/
  papers/          # existing PDFs
  web/             # HTML clips (Obsidian Web Clipper output, wget, etc.)
  notes/           # Markdown files (personal notes, READMEs)
  images/          # diagrams, screenshots
```

`ingest_path()` already handles directories recursively; extending it to new extensions is
sufficient.

---

## Layer 2: Enriched Index (minimal changes)

The enriched layer is already domain-agnostic:
- ChromaDB embeddings work on any text chunks.
- The topic graph and citation graph degrade gracefully for non-academic sources (no DOIs,
  no formal citations — but concept co-occurrence and hyperlink structure can substitute).
- `find_corpus_gaps`, `find_synthesis_opportunities`, and `search_papers` work on any corpus.

### What changes

1. **Hyperlinkextraction** for web/markdown sources: extract `[text](url)` and `<a href>` as
   pseudo-citations into the `Citation` table with `source_type="hyperlink"`. This gives the
   graph meaningful edges for non-academic sources.

2. **Image indexing**: store alt-text + surrounding context as a single chunk. Mark the chunk
   `chunk_type="image_context"` so retrieval can filter or weight them.

3. **`display_name()` generalization**: for non-academic sources, fall back to
   `domain + year + title_slug` (e.g., `example.com 2024 - How to do X`).

No schema migration needed — `doc_type` / `source_type` already has an `other` fallback.

---

## Layer 3: Curated Wiki

This is entirely new. The wiki is a directory of LLM-authored Markdown files, built
progressively from the enriched index and maintained by an autonomous wiki agent.

### Storage

```
data/wiki/
  _index.md            # compact index of all articles (maintained by LLM)
  _pending.md          # stubs and articles flagged for update
  concepts/            # one article per concept / technology / method
  syntheses/           # cross-concept synthesis notes
  gaps/                # gap analysis articles
  queries/             # saved Q&A outputs filed back to wiki
```

Wiki articles are `Source` entries with `source_type=wiki_article` and `origin=WIKI`.
They are **also** written to `data/wiki/` as `.md` files so they are:
- Readable in Obsidian (same as today)
- Ingestible back into ChromaDB for RAG over the wiki itself

### Article format

```markdown
---
title: Hafnium Oxide in ALD Memristors
wiki_id: HfO2_ALD_memristors
status: full        # stub | draft | full
created: 2026-04-03
updated: 2026-04-03
sources:            # Source.id hashes that informed this article
  - abc123
  - def456
topics: [HfO2, ALD, memristor, resistive switching]
linked_to:
  - ALD Fundamentals
  - Neuromorphic Computing
model: claude-sonnet-4-6
---

[LLM-authored content]
```

### `WikiArticle` SQLite model

```python
class WikiArticle(SQLModel, table=True):
    id: str = Field(primary_key=True)   # slug, e.g. "HfO2_ALD_memristors"
    title: str
    status: str = "stub"                # stub | draft | full
    file_path: str                      # relative path in data/wiki/
    source_ids: str = "[]"             # JSON list of Source.id
    topic_keys: str = "[]"             # JSON list of topic vocab keys
    created_at: datetime
    updated_at: datetime
    model: str = ""
    needs_update: bool = False          # set True when new sources touch its topics
```

This table is the wiki's state machine. It drives the maintenance loop.

---

## Progressive Wiki Building

The wiki grows in four modes:

### 1. Bootstrap (`scholarforge wiki init`)
Run once after initial ingest. The wiki agent:
1. Calls `get_corpus_summary()` + `find_synthesis_opportunities()`
2. Identifies the top N concepts by topic frequency and connectivity
3. Writes a stub for each (200-word summary from digests)
4. Writes `_index.md` and `_pending.md`

Stubs are cheap — one `read_paper_digest` call per 3-5 concept terms.

### 2. Expand (`scholarforge wiki expand [concept]`)
Deepen one stub into a full article:
1. Load the stub + `source_ids` from `WikiArticle`
2. Run targeted `search_papers` for the concept + related terms
3. Call `read_section` on the most relevant papers
4. Write a full article (600-1200 words) with inline citations
5. Update `WikiArticle.status = "full"` and `updated_at`
6. Re-link: scan other articles for references to this concept, add backlinks

Without argument, `expand` works from `_pending.md` in priority order (highest
degree concepts first).

### 3. Sync (`scholarforge wiki sync`)
After any new ingest, update stale articles:
1. For each newly ingested `Source`, find `WikiArticle` rows whose `topic_keys`
   overlap with the new source's topics
2. Set `needs_update = True` on matches
3. The sync agent reads each stale article + the new sources' digests and produces
   a diff: new findings added, outdated claims flagged, new citations appended
4. Update `updated_at` and clear `needs_update`

This is the key to progressive maintenance — new sources automatically propagate
into relevant wiki articles without a full rebuild.

### 4. Health check (`scholarforge wiki health`)
Periodic quality sweep:
1. **Orphan detection**: articles with no backlinks from other articles
2. **Staleness**: articles whose `updated_at` is older than the newest source in their topics
3. **Missing articles**: `find_synthesis_opportunities()` results that have no wiki article
4. **Inconsistency**: contradictory claims across articles (uses embedding similarity
   between article chunks to flag near-duplicate or contradictory passages)
5. Output: `_health_report.md` with recommended actions

---

## Q&A Modes

| Mode | What it queries | When to use |
|------|----------------|-------------|
| `--wiki-only` | `data/wiki/` articles only | Fast; curated knowledge; offline |
| `--corpus-only` | Raw enriched index | Most current; direct evidence |
| `--hybrid` (default) | Wiki for context, corpus for evidence | Best quality |

The chat and MCP tools already work on the enriched index. Wiki-mode adds a new
retrieval path: `search_wiki(query)` does semantic search over wiki article chunks
(ingested from `data/wiki/` into a dedicated ChromaDB collection `wiki_chunks`).

---

## Output and Promote

Generated papers, reports, and Q&A outputs can be filed back to the wiki:

```
scholarforge promote data/output/review.md --as wiki_article --title "ALD Overview"
```

This:
1. Converts the output to wiki article format (adds frontmatter, updates citations)
2. Adds it to `data/wiki/queries/` or `data/wiki/syntheses/`
3. Creates a `WikiArticle` row with `source_ids` derived from the cited papers
4. Adds it to the `wiki_chunks` collection
5. Runs the linker to backlink it from relevant concept articles

---

## CLI Surface (new commands)

```
scholarforge ingest <path>           # extended: now handles .md, .html, images
scholarforge wiki init               # bootstrap wiki from corpus
scholarforge wiki expand [concept]   # expand stub(s) to full articles
scholarforge wiki sync               # update stale articles after new ingest
scholarforge wiki health             # find gaps, orphans, inconsistencies
scholarforge wiki query "..."        # Q&A against wiki + corpus
scholarforge promote <output.md>     # file generated output back to wiki
```

Existing commands unchanged.

---

## Migration Path

The current codebase needs only additive changes:

| Change | Scope | Effort |
|--------|-------|--------|
| New ingester modules (md, html, image) | `ingest/` | Small |
| `source_type` enum extension | `store/models.py` | Trivial (Alembic migration) |
| `WikiArticle` SQLite model | `store/models.py` | Small |
| `wiki/` module (builder, sync, health) | `src/scholarforge/wiki/` | Medium |
| `wiki_chunks` ChromaDB collection | `store/embeddings.py` | Small |
| `promote` command | `cli.py` | Small |
| Wiki Q&A path in chat/MCP | `agent/tools.py`, `generate/chat.py` | Small |

The enriched index (ChromaDB, citation graph, topic vocab) is already domain-agnostic.
The generation layer (writer, planner, artifact types) is already parameterized.
The Obsidian vault continues to work for the enriched layer; the wiki is a separate
curated layer above it.

---

## Suggested Execution Order

1. **Generalize ingest**: `MarkdownIngester` + `HTMLIngester` — these unlock any corpus.
2. **`WikiArticle` model + `wiki/` module skeleton** — establish the data layer.
3. **`wiki init` + `wiki expand`** — the core value: LLM builds articles from corpus.
4. **`wiki sync`** — makes wiki maintenance automatic after each ingest.
5. **`promote`** — closes the loop: outputs become knowledge.
6. **`wiki health`** — quality gate and discovery engine.
7. **`wiki query` / MCP tools** — expose wiki as a retrieval surface.

---

## What This Looks Like in Practice

**Day 1**: Ingest 50 papers on ALD, 30 web clips from Obsidian Web Clipper, and a few
repo READMEs. Run `wiki init` → 40 concept stubs created. Run `wiki expand` on the top 10
concepts → full articles in 20 minutes.

**Week 1**: Add 20 more papers. Run `scholarforge ingest data/raw/ && scholarforge wiki sync`
→ 8 existing articles updated automatically with new findings.

**On demand**: `scholarforge wiki query "what is the state of ALD for neuromorphic?"` →
answer grounded in both wiki articles and raw corpus, with citations. Promote the answer
to `data/wiki/queries/ald_neuromorphic_state.md`.

**Monthly**: `scholarforge wiki health` → identifies 5 orphaned articles, 3 synthesis
opportunities not yet covered, 2 potentially outdated claims. Run `wiki expand` on the
missing topics.
