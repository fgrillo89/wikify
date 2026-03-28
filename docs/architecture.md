# ScholarForge — Architecture

## What is ScholarForge?

A local-first Python pipeline that turns a folder of academic PDFs into a living
Obsidian knowledge graph, then uses that graph to write papers, lit reviews, and
grant proposals.

**Core philosophy**: Obsidian IS the app. ScholarForge is the engine that feeds it.
We don't build UI, visualization, chat, or vector search — the Obsidian plugin
ecosystem handles all of that. We focus on what plugins can't do: high-quality
ingestion, structured linking, and LLM-powered generation with export to Word/LaTeX.

## Design Principles

1. **Vault-first**: The Obsidian vault is the single source of truth. SQLite is a
   supporting index, not a parallel database. Everything meaningful lives as markdown.
2. **Plugin-over-code**: If an Obsidian plugin already does it well, don't build it.
   Neural Composer does GraphRAG. Smart Connections does vector chat. Dataview does queries.
3. **JIT over batch**: Don't pre-compute what you can resolve at query time. Index
   cheaply (metadata + chunks), build the deep graph lazily. The user drops 200 PDFs
   and starts writing in 8 minutes — not 6 hours.
4. **LLM reads as little as possible**: The system narrows 200 papers down to the
   5-10 that matter before the LLM sees any full text. Metadata → abstract → chunks → full read.
5. **Local-first**: All parsing, embedding, and heavy lifting done locally. LLM calls
   are the only optional network dependency (and can use Ollama for full offline).

## What ScholarForge Builds (Our Unique Value)

| We build | Why plugins can't |
|---|---|
| **Ingestion pipeline** (PDF → structured markdown with frontmatter) | Plugins parse PDFs but don't extract academic metadata, section structure, or typed links |
| **Ghost Graph** (frontmatter-driven Obsidian graph with zero graph DB) | We write typed frontmatter → Obsidian renders the graph for free |
| **JIT context assembly** (narrow 200 papers to 5-10 relevant ones fast) | No plugin does progressive academic narrowing with token budgets |
| **Generation pipeline** (planner → writer with token budgets) | No plugin writes structured academic output |
| **Export** (DOCX/LaTeX with citation formatting) | No plugin does academic export |

## What Obsidian Plugins Handle (Don't Build)

| Capability | Plugin | Notes |
|---|---|---|
| Vector search + semantic chat | **Smart Connections** | Embeds vault locally, chat sidebar with citations |
| Knowledge graph + entity extraction | **Neural Composer** | LightRAG integration, auto-extracts entities, 3D graph viz |
| Structured queries | **Dataview** | SQL-like queries on frontmatter properties |
| Graph visualization | **Obsidian Graph View** + Neural Composer | Native, color-coded by tags |
| Zotero sync | **Zotero Integration** | Annotations, metadata, cite keys |
| PDF conversion (fallback) | **Marker** plugin (`L3-N0X/obsidian-marker`) | In-Obsidian PDF→MD via Marker API |
| AI agent access | **MCP servers** (multiple) | Claude/Copilot can read/write vault via MCP |
| Link styling | **Supercharged Links** | Color-code links by note type |

## Tech Stack

- **Language**: Python 3.12+
- **Package manager**: UV (with hatchling build backend)
- **Project layout**: `src/scholarforge/` (src layout)
- **Primary store**: Obsidian vault (`data/vault/`) — markdown + wikilinks
- **Supporting index**: SQLite (via SQLModel) — programmatic queries, chunk storage
- **PDF parsing**: pymupdf4llm (default), Marker (fallback for hard PDFs)
- **LLM**: litellm (any provider) or Ollama (local)
- **CLI**: Typer + Rich

## The Two-Phase Engine

### Phase 1 — Cold Start: "8-Minute Ingestion" (No LLM)

User drops 200 PDFs. ScholarForge runs fully locally, no API calls:

```
PDF → pymupdf4llm → markdown → metadata (regex) → chunks → SQLite + vault notes → link
```

| Step | Tool | Speed | LLM? |
|---|---|---|---|
| Parse PDF → markdown | pymupdf4llm | ~2.2s/paper (parallel) | No |
| Extract metadata | Regex + PDF fields + filename | <5ms/paper | No |
| Chunk text | Section-aware splitter | <40ms/paper | No |
| Write vault note | File I/O | <5ms/paper | No |
| Detect topics/methods | Keyword matching | <1ms/paper | No |
| Write author/topic/method notes | File I/O | <5ms/paper | No |
| **Embed abstracts** | **ChromaDB + all-MiniLM-L6-v2** | **~0.1s/paper** | **No** |
| **Build k-NN similarity graph** | **Cosine similarity on embeddings** | **<1s total** | **No** |
| **Total for 200 papers** | | **~9 minutes (parallel)** | **No** |

**What you get after 9 minutes:**
- 200 paper notes with typed frontmatter (title, authors, year, DOI, topics, methods)
- Author notes with paper backlinks
- Topic and method notes with paper backlinks
- **Abstract embeddings in ChromaDB** — instant semantic search over all papers
- **k-NN similarity edges** — each paper linked to its 5 most similar papers via embeddings
- Obsidian graph view showing the full citation/topic/method/similarity network
- Dataview queries working on all frontmatter
- Smart Connections indexing the vault in the background (full note embeddings)
- Neural Composer auto-extracting entities

**What you DON'T do at ingestion:**
- No LLM calls (no summaries, no entity extraction via LLM)
- No binary figure extraction (captions only)
- No deep graph construction (no triples, no contradiction detection)
- Embeddings are local (all-MiniLM-L6-v2 runs on CPU, ~80ms per abstract)

### Phase 2 — Query Time: "JIT Retrieval" (LLM Only When Needed)

When the user asks "Write a lit review section on HfO2-based memristors for
neuromorphic computing," the system doesn't read 200 papers. It narrows progressively:

```
Step 1: Semantic + structured   → 200 papers → ~0.1s   → 30 candidates
Step 2: Abstract rerank         → 30 papers  → ~8K tok  → 15 relevant
Step 3: Chunk retrieval         → 15 papers  → ~30K tok → best evidence
Step 4: LLM generates section   → ~30K context → ~3K output
```

**The narrowing funnel:**

| Stage | Input | Method | Output | Speed | LLM? |
|---|---|---|---|---|---|
| **Semantic search** | 200 papers | ChromaDB vector search on abstract embeddings | Top 30 by similarity | <0.1s | No |
| **Structured filter** | 30 candidates | SQLite WHERE on topics/methods/year | Intersect with frontmatter | <0.01s | No |
| **Abstract rerank** | 30 abstracts | LLM scores relevance 0-10 (cheap model) | 15 relevant | ~2s | Yes (cheap) |
| **Chunk retrieval** | 15 papers | Section-aware chunks from SQLite | Top chunks per paper | <0.1s | No |
| **Generate** | ~30K tokens | LLM writes section with citations | Draft text | ~10s | Yes (capable) |

**Why this is fast:**
- Step 1 is instant — ChromaDB k-NN on 200 abstract embeddings is sub-100ms
- Step 2 is instant — SQLite frontmatter filtering (topics, methods, year range)
- Step 3 uses a cheap/small model (Haiku, or Llama 3 8B via Ollama) — just scoring
- Step 4 is a SQLite query — section_path filtering, no vector search needed
- Step 5 is the only expensive LLM call, and it sees ~30K tokens not 200K

**Alternative: zero-LLM narrowing** — for maximum speed, skip step 3 entirely and
go straight from vector+structured filtering to chunk retrieval. The embedding
similarity from ChromaDB is often good enough as a "first guess."

## The Ghost Graph

The "Ghost Graph" is our term for the Obsidian-native knowledge graph that requires
**zero graph database infrastructure**. It's not a placeholder — it's a real,
queryable, multi-dimensional graph that emerges from ingestion alone.

### What the Ghost Graph contains after ingestion (no LLM)

**Explicit edges** (from metadata extraction):

| Edge Type | Source | Cost | Example |
|---|---|---|---|
| **authored_by** | PDF metadata + filename | Free (regex) | `papers/Kim 2024 → authors/Kim` |
| **hasTopic** | Keyword matching on title + abstract + chunks | Free (string match) | `papers/Kim 2024 → topics/HfO2` |
| **uses_method** | Keyword matching | Free (string match) | `papers/Kim 2024 → methods/ALD` |
| **cites** | Bibliography regex (planned) | Free (regex) | `papers/Kim 2024 → papers/Gao 2014` |
| **co-author** | Shared author nodes | Free (implicit) | `authors/Kim ← paper → authors/Park` |
| **temporal** | Year field in frontmatter | Free (metadata) | Dataview: sort/filter by year |

**Inferred edges** (from embeddings — the "first guess"):

| Edge Type | Source | Cost | Example |
|---|---|---|---|
| **similar_to** | k-NN on abstract embeddings (top 5) | <1s for 200 papers | `papers/Kim 2024 ↔ papers/Park 2022` |
| **bibliographic_coupling** | Shared references in bibliography (planned) | ~5s (string matching) | Papers that cite the same 3+ sources |

The k-NN edges are the **embedding-first graph scaffold**. Papers that are semantically
similar get linked even if they don't share explicit topic/method keywords. This catches
relationships that keyword matching misses (e.g., papers about "resistive switching
devices" and "memristive synapses" — same thing, different vocabulary).

For 206 papers, this gives us: 464 author nodes, 20 topic nodes, 9 method nodes,
~1000 k-NN similarity edges, and all cross-links. That's already a rich, multi-layered
graph — Obsidian renders it instantly, and Dataview can query any combination:

```
TABLE year, doi FROM #source/paper
WHERE contains(hasTopic, [[topics/HfO2]])
AND year >= 2022
SORT year DESC
```

### What Neural Composer adds on top (automatic, in Obsidian)

Neural Composer watches the vault and uses LightRAG to extract deeper relationships
that keyword matching misses: specific material compositions, device architectures,
performance metrics, etc. This enriches the Ghost Graph without any ScholarForge code.

### What Smart Connections adds on top (automatic, in Obsidian)

Smart Connections embeds every note and enables **embedding-based clustering** —
papers that are semantically similar cluster together even without shared keywords.
This provides a complementary discovery axis: "papers that talk about similar things"
vs "papers that share explicit topic/method tags."

**The Ghost Graph + Neural Composer + Smart Connections = a complete knowledge graph
with typed relations, entity extraction, and semantic similarity — built from
frontmatter, wikilinks, and plugin automation. No graph DB needed.**

## Module Layout

```
src/scholarforge/
├── __init__.py
├── cli.py                  # Typer CLI entry point
├── config.py               # pydantic-settings config
│
├── ingest/                 # Phase 1: Document ingestion (no LLM)
│   ├── pdf.py              # pymupdf4llm (default) / Marker (fallback)
│   ├── docx.py             # python-docx → markdown
│   ├── slides.py           # python-pptx → markdown
│   ├── data.py             # CSV/Excel/Parquet → dataset cards (polars)
│   ├── zotero.py           # Zotero library sync
│   └── registry.py         # extension → parser dispatcher + parallel orchestration
│
├── extract/                # Phase 1: Structured extraction (no LLM)
│   ├── chunker.py          # Section-aware semantic chunking
│   ├── metadata.py         # Title, authors, abstract, DOI, year
│   ├── figures.py          # Caption-first figure reference extraction
│   └── references.py       # Citation extraction
│
├── store/                  # Supporting index
│   ├── models.py           # SQLModel data models
│   ├── db.py               # SQLite engine + session
│   └── embeddings.py       # ChromaDB: abstract embeddings + k-NN similarity graph
│
├── vault/                  # Phase 1: Obsidian vault management (no LLM)
│   ├── writer.py           # Generate/update vault markdown notes
│   ├── linker.py           # Keyword-based topic/method detection + linking
│   └── templates.py        # Note templates (paper, author, topic, method)
│
├── retrieve/               # Phase 2: JIT retrieval (LLM at query time)
│   ├── narrower.py         # Progressive narrowing: frontmatter → abstract → chunks
│   ├── context.py          # Token budget allocator + compression
│   └── claims.py           # On-demand claim extraction from selected papers
│
├── generate/               # Phase 2: Content generation (LLM)
│   ├── planner.py          # TOC/structure generation
│   ├── writer.py           # Section-by-section generation
│   └── prompts/            # Jinja2 prompt templates
│
├── export/                 # Output formatting
│   ├── docx_export.py      # python-docx output
│   ├── latex_export.py     # LaTeX/Jinja2 templates
│   └── bibliography.py     # BibTeX / CSL citation formatting
│
└── llm/                    # LLM interface layer
    ├── client.py           # litellm unified client
    └── cache.py            # diskcache response caching
```

**Key change from traditional RAG pipelines:**
- No `store/vectors.py` — Smart Connections handles vector search inside Obsidian
- No `graph/` package — the Ghost Graph IS the vault; Neural Composer enriches it
- New `retrieve/` package — the JIT narrowing engine that makes generation fast
- `claims.py` moved from `extract/` to `retrieve/` — claims are extracted JIT for
  selected papers only, not pre-computed for all 200

## Vault Structure

```
data/vault/                     # Obsidian vault (gitignored, under data/)
├── papers/                     # One note per ingested paper
├── authors/                    # One note per author (auto-created)
├── topics/                     # Detected topics (keyword + Neural Composer)
├── methods/                    # Detected methods
├── concepts/                   # Field-specific concepts (Neural Composer)
├── findings/                   # Atomic claims (JIT, per-query)
├── datasets/                   # Conceptual datasets (WMT 2014, MNIST, etc.)
├── data/                       # Dataset cards for user's data files
├── docs/                       # User documents (drafts, reports)
└── templates/                  # User-provided style + structure templates
    ├── styles/                 # Example documents for tone/formatting
    └── structures/             # TOC skeletons for different output types
```

### Note Types

| Type | Tag | Graph Color | Example |
|---|---|---|---|
| Paper | `#source/paper` | blue | `papers/Vaswani 2017 - Attention Is All You Need.md` |
| Author | `#author` | gray | `authors/Ashish Vaswani.md` |
| Topic | `#topic` | orange | `topics/Transformer Architecture.md` |
| Method | `#method` | purple | `methods/Multi-Head Attention.md` |
| Concept | `#concept` | green | `concepts/Self-Attention.md` |
| Finding | `#finding` | red | `findings/Attention outperforms recurrence.md` |
| Dataset | `#dataset` | yellow | `datasets/WMT 2014.md` |
| Dataset Card | `#source/data` | yellow-green | `data/Experiment Results.md` |
| User Doc | `#source/user` | cyan | `docs/Draft - Phase 2 Analysis.md` |

### Paper Note Template

```markdown
---
title: "Attention Is All You Need"
authors:
  - "[[authors/Ashish Vaswani]]"
  - "[[authors/Noam Shazeer]]"
year: 2017
doi: "10.48550/arXiv.1706.03762"
tags:
  - source/paper
hasTopic:
  - "[[topics/Transformer Architecture]]"
  - "[[topics/Neural Machine Translation]]"
uses_method:
  - "[[methods/Multi-Head Attention]]"
  - "[[methods/Positional Encoding]]"
cites:
  - "[[papers/Bahdanau 2014 - Neural Machine Translation]]"
file_hash: "abc123..."
ingested_at: 2026-03-28
---

## Abstract

Introduces the Transformer architecture, replacing recurrence entirely with
multi-head self-attention. Achieves SOTA on WMT 2014 EN-DE and EN-FR translation.

## Figure Mentions

- **Fig. 1** (p.2): "The Transformer - model architecture" — §Architecture
- **Fig. 2** (p.4): "Scaled dot-product attention" — §Attention

## Statistics

- **Chunks**: 24
- **Figures referenced**: 5
```

Note what's NOT pre-computed in the paper note:
- No summary (generated JIT when this paper is selected for a query)
- No claims/findings (extracted JIT when this paper is relevant)
- No embeddings (Smart Connections handles this in Obsidian)

### Linking Strategy

Links are typed via frontmatter properties and inline wikilinks:

- **hasTopic**: `[[topics/X]]` — primary discovery axis
- **uses_method**: `[[methods/X]]` — methodological connections
- **cites**: `[[papers/X]]` — citation network
- **authors**: `[[authors/X]]` — collaboration network
- **supports / contradicts**: Added JIT when claims are extracted

Obsidian backlinks provide the reverse index automatically.

## Information Compression Layers

Each layer is progressively cheaper to access. The retrieval engine walks down
the layers only as far as needed.

| Layer | Content | Tokens (200 papers) | Access method | When accessed |
|---|---|---|---|---|
| **L0 — Frontmatter** | Title, authors, year, DOI, topics, methods | ~2K | SQLite / Dataview | Always — structured filtering |
| **L0.5 — Embeddings** | Abstract vectors (200 × 384 dims) | 0 tokens (vector math) | ChromaDB k-NN | Always — semantic similarity, <0.1s |
| **L1 — Abstracts** | Abstract text from paper notes | ~40K (200 × 200 tokens) | SQLite | Candidate reranking |
| **L2 — Chunks** | Section-aware semantic chunks | ~120K total, top-K selected | SQLite | Evidence gathering |
| **L3 — Claims** | 3-5 atomic findings per paper (JIT) | ~15K for selected papers | LLM extraction | Argumentation |
| **L4 — Full text** | Complete markdown | ~600K | File read | Rarely — deep analysis only |

**The LLM never sees L4.** It works with L0-L3. L0.5 (embeddings) costs zero tokens
and provides the "first guess" for semantic similarity. For a typical lit review
section, the LLM context is ~30K tokens (L0 for all + L1 for candidates + L2+L3
for selected).

## Obsidian as the User Interface

The vault is the primary UI. The user opens Obsidian and sees their knowledge graph.

- **Graph view**: The Ghost Graph — paper↔topic↔method↔author connections from frontmatter.
- **Backlinks panel**: Click any topic/method/author → see all papers that reference it.
- **Dataview tables**: `TABLE year, doi FROM #source/paper WHERE uses_method = [[methods/ALD]]`
- **Canvas**: Drag notes to visually plan a lit review structure before generation.
- **Smart Connections sidebar**: Ask questions, get answers with links to source notes.
- **Neural Composer**: Deeper entity extraction + 3D knowledge graph + LightRAG retrieval.

### MCP: AI Agents ↔ Obsidian

MCP servers allow Claude Code and other AI agents to interact with the vault:

- **mcp-obsidian** (`MarkusPfundstein/mcp-obsidian`) — CRUD via Obsidian REST API
- **obsidian-mcp-server** (`cyanheads/obsidian-mcp-server`) — comprehensive read/write/search
- **obsidian-mcp-tools** (`jacksteamdev/obsidian-mcp-tools`) — semantic search integration

This means Claude Code can search the vault, read paper notes, and draft sections
directly — turning the vault into an AI-accessible research workspace.

## SQLite Schema (Supporting Index)

SQLite stores data for fast programmatic queries during JIT retrieval.
It is NOT the primary store — the vault is.

- **Paper**: id (SHA256), title, authors (JSON), abstract, year, doi, source_path, file_hash, ingested_at, section_tree (JSON)
- **Chunk**: id (UUID), paper_id (FK), section_path, content, token_count, chunk_index, has_citations, has_equations
- **FigureRef**: id (UUID), paper_id (FK), figure_key, caption, section_path, page_number, anchor_text
- **Citation**: id, paper_id, cited_paper_id (FK nullable), raw_text, bibtex, context_chunk_id

## Figure Handling: Caption-First

- Extract figure *references* from markdown text (caption, figure key, section, page)
- Store as `FigureRef` in SQLite + `## Figure Mentions` section in paper notes
- No binary image extraction — captions are sufficient for writing
- On-demand binary extraction via pymupdf when user needs figure reuse/composition

## Key Decisions

| Decision | Rationale |
|---|---|
| Vault-first, not DB-first | The vault IS the product. SQLite is just a programmatic index. |
| JIT over batch indexing | Don't pre-compute summaries/claims for 200 papers. Narrow first, compute for the 10-15 that matter. |
| Ghost Graph (frontmatter + embeddings) | Typed frontmatter + k-NN similarity edges → Obsidian renders a rich graph. No graph DB needed. |
| Abstract embeddings at ingestion | Embed abstracts (not all chunks) into ChromaDB. ~200 vectors, <1s k-NN, enables semantic narrowing. Smart Connections handles full-vault embeddings separately. |
| Plugin-over-code | Smart Connections, Neural Composer, Dataview handle vector/graph/queries. |
| No LLM at ingestion | Ingestion is pure local compute (pymupdf4llm + regex). LLM calls only at query time. |
| Progressive narrowing | 200 → 40 → 15 → generate. The LLM never sees all 200 papers. |
| Caption-first figures | Captions are what LLMs need for writing. Pixel extraction adds latency with no writing benefit. |
| pymupdf4llm default | 2.2s/paper parallel. Marker as fallback for hard PDFs only. |
| Parallel parse, sequential persist | ProcessPoolExecutor for CPU-bound parsing; SQLite writes serialized. |
| litellm for LLM access | Abstracts over Claude/GPT/Ollama. No lock-in. |

## Data Directory Layout (gitignored)

```
data/
├── papers/                 # Source PDFs (user drops files here)
│   └── ald_references/     # Current test corpus: 206 memristor/ALD papers
├── papers.db               # SQLite (supporting index)
├── cache/                  # LLM response cache (diskcache)
└── vault/                  # Obsidian vault (point Obsidian here)
    ├── papers/
    ├── authors/
    ├── topics/
    ├── methods/
    ├── concepts/
    ├── findings/
    ├── datasets/
    ├── data/
    ├── docs/
    └── templates/
```

## Dependencies

```
# Parsing
pymupdf, pymupdf4llm          # PDF → markdown (default parser)
python-docx, python-pptx      # Office formats
polars, pyarrow                # Data files (CSV, Excel, Parquet)

# Storage
sqlmodel                       # SQLite ORM (supporting index)
chromadb                       # Abstract embeddings + k-NN similarity (lightweight, ~200 vectors)
sentence-transformers          # Local embedding model (all-MiniLM-L6-v2, CPU-friendly)

# LLM
litellm, tiktoken, diskcache   # LLM interface + caching

# References
pyzotero, bibtexparser>=1.4    # Zotero integration

# Export
jinja2                         # LaTeX templating
matplotlib, plotly             # Figure generation

# CLI
typer, pydantic-settings, rich # CLI + config
```

**Not in our dependencies** (handled by Obsidian plugins or deferred):
- `networkx` / `python-louvain` — Ghost Graph + Neural Composer handle graph
- `spacy` / `gliner` — Neural Composer handles entity extraction (or we add later for JIT claims)

**Note on ChromaDB scope**: We use ChromaDB ONLY for abstract embeddings (~200 vectors)
and k-NN similarity graph construction. Full-vault vector search for chat/Q&A is
handled by Smart Connections inside Obsidian. This keeps our ChromaDB usage minimal
and fast — it's a similarity index, not a retrieval engine.
