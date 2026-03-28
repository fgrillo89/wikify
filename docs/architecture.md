# ScholarForge — Architecture

## What is ScholarForge?

A Python app to help researchers write papers, slides, abstracts, and grant proposals.
It manages a personal knowledge base built from ingested PDFs, slides, and docs into a
curated knowledge graph backed by an Obsidian vault, optimized for both human browsing
and LLM consumption.

## Key Requirements

- **Knowledge graph**: Ingest PDFs, slides, docs → Obsidian vault of interlinked markdown notes
- **Figure handling**: Extract figures, annotate with metadata (avoid re-ingestion), support reuse/combination
- **Output formats**: Word (.docx) and LaTeX
- **Reference management**: Zotero integration (pyzotero) + Obsidian Zotero bridge
- **Data visualization**: Python-powered (matplotlib/plotly)
- **Optional**: Google Drive ingestion
- **Local-first**: Heavy lifting (PDF parsing, ingestion, embedding) done locally
- **Staged creation**: First TOC/structure, then sections/figures — unless user asks otherwise

## Current Iteration Target

Literature review of 200 papers (user will provide them). Backend first, frontend later.

## Tech Stack

- **Language**: Python 3.10+
- **Package manager**: UV (with hatchling build backend)
- **Project layout**: `src/scholarforge/` (src layout, created by `uv init --lib`)
- **Knowledge graph**: Obsidian vault (markdown files + wikilinks)
- **Graph algorithms**: Via MCP tooling or networkx on parsed vault
- **Location**: `C:\Users\fgril\OneDrive\Documents\scholarforge\`

## Pipeline Overview

```
PDF/DOCX/PPTX → [parse] → [extract] → [store + vault] → [link] → [generate] → [export]
```

### Stage 1 — Parse: Document → Markdown

Convert source documents to clean markdown. See `docs/parser-evaluation.md` for
detailed comparison of parsing options.

**Current implementation**: pymupdf4llm (fast, local, good 80% solution)
**Under evaluation**: LiteParse (spatial layout preservation, TypeScript-native with Python wrapper)

### Stage 2 — Extract: Markdown → Structured Data

- **Chunking**: Section-aware semantic chunking (600 target / 800 max tokens)
- **Metadata**: Title, authors, abstract, year, DOI (regex + PDF fields, CrossRef enrichment planned)
- **Figures**: Content-addressed extraction via pymupdf (SHA256 dedup)
- **Citations**: Regex detection of `[Author YYYY]` and `[1]` styles
- **Equations**: LaTeX delimiter detection

### Stage 3 — Store + Vault: Structured Data → SQLite + Obsidian

Two parallel persistence layers:

1. **SQLite** (via SQLModel) — structured queryable data for programmatic access
2. **Obsidian vault** — human-readable/editable markdown notes forming the knowledge graph

The vault IS the graph. Every `[[wikilink]]` is an edge. Every `.md` file is a node.

### Stage 4 — Link: Incremental Graph Maintenance

When a new paper is added, detect and create links to existing notes:
- Shared references/citations → `[[cites::Paper X]]`
- Shared authors → `[[author::Author Name]]`
- Topic/keyword overlap → `[[hasTopic::Topic]]`
- Shared methodology → `[[uses_method::Method]]`
- Embedding similarity → suggest related papers

### Stage 5 — Generate: Knowledge Graph + Context → LLM → Text

- Two-stage: Planner (TOC) → Writer (sections)
- Token budget: 40% source, 20% figures, 10% structure, 30% output
- Context assembly from vault notes + SQLite chunks

### Stage 6 — Export: Generated Content → Word/LaTeX

- python-docx for .docx output
- Jinja2 templates for LaTeX
- BibTeX/CSL citation formatting

## Module Layout

```
src/scholarforge/
├── __init__.py
├── cli.py                  # Typer CLI entry point
├── config.py               # pydantic-settings config
│
├── ingest/                 # Stage 1: Document ingestion
│   ├── pdf.py              # pymupdf4llm (current) / LiteParse (evaluating)
│   ├── docx.py             # python-docx → markdown
│   ├── slides.py           # python-pptx → markdown (slide-by-slide + speaker notes)
│   ├── data.py             # CSV/Excel/Parquet → dataset cards (polars)
│   ├── txt.py              # Plain text → direct read + chunk
│   ├── gdrive.py           # Optional: Google Drive
│   ├── zotero.py           # Zotero library sync
│   └── registry.py         # file extension + source type → parser dispatcher
│
├── extract/                # Stage 2: Structured extraction
│   ├── chunker.py          # Section-aware semantic chunking
│   ├── figures.py          # Figure/table extraction + content-hash dedup
│   ├── references.py       # Citation extraction and linking
│   ├── metadata.py         # Title, authors, abstract, DOI
│   └── equations.py        # LaTeX equation extraction
│
├── store/                  # Stage 3: Storage layer
│   ├── models.py           # SQLModel/Pydantic data models
│   ├── db.py               # SQLite via SQLModel
│   ├── vectors.py          # ChromaDB embedded vector store
│   ├── figures_store.py    # Content-addressed figure storage
│   └── migrations.py       # Schema versioning
│
├── vault/                  # Stage 3+4: Obsidian vault management
│   ├── writer.py           # Generate/update vault markdown notes
│   ├── linker.py           # Incremental link detection + creation
│   ├── templates.py        # Note templates (paper, concept, author, method, topic)
│   └── sync.py             # Vault ↔ SQLite consistency
│
├── graph/                  # Graph algorithms on vault data
│   ├── builder.py          # Build networkx graph from vault links
│   ├── schema.py           # Node/edge type definitions
│   ├── traverse.py         # Token-efficient traversal (4 strategies)
│   ├── queries.py          # Pre-built graph query patterns
│   └── serialize.py        # Graph → compact LLM-readable format
│
├── generate/               # Stage 5: Content generation
│   ├── planner.py          # TOC/structure generation
│   ├── writer.py           # Section-by-section generation
│   ├── prompts/            # Jinja2 prompt templates
│   ├── context.py          # Token budget allocator
│   └── figures_gen.py      # matplotlib/plotly figure generation
│
├── export/                 # Stage 6: Output formatting
│   ├── docx_export.py      # python-docx output
│   ├── latex_export.py     # LaTeX/Jinja2 templates
│   ├── templates/          # .docx and .tex templates
│   └── bibliography.py     # BibTeX / CSL citation formatting
│
└── llm/                    # LLM interface layer
    ├── client.py           # litellm unified client
    ├── tokenizer.py        # Token counting
    └── cache.py            # diskcache response caching
```

## Obsidian Vault Structure

The vault is the knowledge graph. ScholarForge generates and maintains it programmatically;
the user can browse and edit it in Obsidian at any time.

### Source Categories

The vault distinguishes four source categories with different ingestion paths:

| Category | Formats | Vault Path | Graph Role | Mutable? |
|---|---|---|---|---|
| **Literature** | PDF, DOCX, PPTX | `vault/papers/` | Source nodes — citable references | No (re-ingest = update) |
| **User Documents** | DOCX, PPTX, TXT | `vault/docs/` | Working nodes — link to literature + data | Yes (track changes) |
| **Data Files** | CSV, XLSX, Parquet, TSV | `vault/data/` | Evidence nodes — support findings | Yes (dataset cards) |
| **Presentations** | PPTX | `vault/papers/` or `vault/docs/` | Depends on `--source-type` flag | Depends |

### Note Types

| Type | Tag | Color | Example |
|---|---|---|---|
| Paper | `#source/paper` | blue | `papers/Vaswani 2017 - Attention Is All You Need.md` |
| User Doc | `#source/user` | cyan | `docs/Draft - Phase 2 Analysis.md` |
| Dataset Card | `#source/data` | yellow-green | `data/Experiment Results - Phase 2.md` |
| Topic | `#topic` | orange | `topics/Transformer Architecture.md` |
| Concept | `#concept` | green | `concepts/Self-Attention.md` |
| Method | `#method` | purple | `methods/Multi-Head Attention.md` |
| Author | `#author` | gray | `authors/Ashish Vaswani.md` |
| Dataset | `#dataset` | yellow | `datasets/WMT 2014.md` |
| Finding | `#finding` | red | `findings/Attention outperforms recurrence.md` |

**Note**: `#source/data` (dataset card — metadata about a specific file) is distinct from
`#dataset` (a conceptual dataset like "MNIST" that multiple papers reference).

### Paper Note Template

```markdown
---
title: "Attention Is All You Need"
authors:
  - "[[authors/Ashish Vaswani]]"
  - "[[authors/Noam Shazeer]]"
year: 2017
doi: "10.48550/arXiv.1706.03762"
zotero_key: "vaswani2017"
tags:
  - source/paper
hasTopic:
  - "[[topics/Transformer Architecture]]"
  - "[[topics/Neural Machine Translation]]"
uses_method:
  - "[[methods/Multi-Head Attention]]"
  - "[[methods/Positional Encoding]]"
cites:
  - "[[papers/Bahdanau 2014 - Neural Machine Translation by Jointly Learning to Align and Translate]]"
  - "[[papers/Luong 2015 - Effective Approaches to Attention-based NMT]]"
file_hash: "abc123..."
ingested_at: 2026-03-28
---

## Summary

Introduces the Transformer architecture, replacing recurrence entirely with
multi-head self-attention. Achieves SOTA on WMT 2014 EN-DE and EN-FR translation.

## Key Contributions

- [[concepts/Self-Attention]] mechanism as sole building block
- [[methods/Multi-Head Attention]] for parallel attention across subspaces
- [[methods/Positional Encoding]] to inject sequence order without recurrence

## Methodology

- [[datasets/WMT 2014]] English-German (4.5M pairs) and English-French (36M pairs)
- Base model: 6 layers, 512 dims, 8 heads
- Training: 8 P100 GPUs, 3.5 days (base), 12 hours (big)

## Findings

- [[findings/Attention outperforms recurrence]]: BLEU 28.4 vs 26.4 on EN-DE
- Training cost: 1/4 of previous SOTA

## Figures

![[figures/ab/cd/abcdef...png|Transformer architecture diagram]]

## Raw Chunks

Stored in SQLite — see chunks table with paper_id matching this note's file_hash.
```

### Dataset Card Template

```markdown
---
title: "Experiment Results - Phase 2"
source_type: data
format: csv
file_path: "data/raw/experiment_phase2.csv"
rows: 15420
columns: ["sample_id", "treatment", "response", "p_value", "effect_size"]
tags:
  - source/data
hasTopic:
  - "[[topics/Drug Response]]"
linked_papers:
  - "[[papers/Smith 2024 - Phase 2 Trial Results]]"
linked_docs:
  - "[[docs/Draft - Phase 2 Analysis Report]]"
ingested_at: 2026-03-28
---

## Schema

| Column | Type | Description | Range |
|---|---|---|---|
| sample_id | int | Unique sample ID | 1-15420 |
| treatment | str | Treatment group | control, low, high |
| response | float | Primary endpoint | 0.1 - 98.7 |

## Summary Statistics

- **Rows**: 15,420 | **Columns**: 5
- **Null rate**: 2.3% (response column)
- **Groups**: control (5140), low (5140), high (5140)

## Preview

| sample_id | treatment | response | p_value |
|---|---|---|---|
| 1 | control | 45.2 | 0.03 |
| 2 | low | 52.1 | 0.01 |
| ... | ... | ... | ... |

## Notes

Supports [[findings/Treatment X improves response by 15%]].
```

### User Document Template

```markdown
---
title: "Draft - Phase 2 Analysis Report"
source_type: user
format: docx
file_path: "user_docs/phase2_report.docx"
tags:
  - source/user
hasTopic:
  - "[[topics/Drug Response]]"
references:
  - "[[papers/Smith 2024 - Phase 2 Trial Results]]"
  - "[[papers/Jones 2023 - Phase 1 Safety Data]]"
uses_data:
  - "[[data/Experiment Results - Phase 2]]"
updated_at: 2026-03-28
---

## Summary

User's draft analysis report for Phase 2 clinical trial results.

## Sections

1. Introduction
2. Methods
3. Results
4. Discussion
```

### Vault Directory Structure

```
vault/
├── papers/                 # Literature — one note per ingested paper
├── docs/                   # User documents — drafts, reports, memos
├── data/                   # Dataset cards — metadata about data files
├── topics/                 # High-level subject areas
├── concepts/               # Field-specific concepts
├── methods/                # Research methods and techniques
├── authors/                # Researcher pages
├── datasets/               # Conceptual datasets (e.g., "MNIST", "WMT 2014")
├── findings/               # Key results and claims
├── figures/                # Content-addressed images (symlinked from data/figures/)
└── templates/              # Obsidian note templates
```

### Linking Strategy

Links are typed via frontmatter properties and inline wikilinks:

- **hasTopic**: `[[topics/X]]` — primary discovery axis
- **uses_method**: `[[methods/X]]` — methodological connections
- **cites**: `[[papers/X]]` — citation network
- **authors**: `[[authors/X]]` — collaboration network
- **supports / contradicts**: Inline links between findings

### Incremental Graph Updates

When paper N+1 is added to a vault of N papers:

1. **Extract metadata** → create/reuse author, method, dataset, topic notes
2. **Match citations** → resolve `cites` links to existing paper notes
3. **Detect topics** → compare extracted keywords to existing `topics/` notes
4. **Embedding similarity** → find top-K similar papers via ChromaDB, suggest `related` links
5. **Update backlinks** — Obsidian handles this automatically via `[[wikilinks]]`

### Obsidian Plugins (Recommended for User)

- **Dataview** — query across paper notes (e.g., "all papers using method X published after 2020")
- **Zotero Integration** — sync annotations and metadata from Zotero
- **Supercharged Links** — color-code links by note type
- **Graph Analysis** — enhanced graph view with clustering

### Claude ↔ Obsidian via MCP

Available MCP servers for Claude to interact with the vault:

- **mcp-obsidian** (`MarkusPfundstein/mcp-obsidian`) — read/search/create/modify notes via Obsidian REST API
- **obsidian-claude-code-mcp** (`iansinnott/obsidian-claude-code-mcp`) — direct vault access for Claude Code
- **Knowledge Graph Tools** (`blog.fsck.com`) — parses vault into graph with SQLite + vector embeddings, exposes 10 MCP tools (semantic search, n-hop traversal, community detection, PageRank, path finding)

## Core Data Models (SQLite)

SQLite remains the structured storage layer alongside the vault:

- **Paper**: id (SHA256 of file), title, authors, abstract, year, doi, zotero_key, source_path, file_hash, ingested_at, section_tree (JSON)
- **Chunk**: id (UUID), paper_id (FK), section_path, content, token_count, chunk_index, has_citations, has_equations
- **Figure**: id (content-hash), paper_id (FK), caption, figure_number, section_path, image_path, width_px, height_px, format, tags (JSON), extracted_data, reuse_count
- **Citation**: id, paper_id, cited_paper_id (FK nullable), raw_text, bibtex, csl_json, context_chunk_id

## Token-Efficient Graph Traversal Strategies

These operate on the graph derived from vault links:

1. **Map-then-Dive**: All paper nodes as 1-line summaries (~4K tokens for 200 papers), LLM picks which to expand
2. **Cluster Walkthrough**: Louvain community detection → cluster summaries, expand one cluster at a time
3. **Query-Guided Subgraph**: Embed query → top-K nearest chunks → extract 2-hop subgraph
4. **Progressive Summary Pyramid**: 3-level hierarchy (chunks → sections → papers → clusters)

### Compact Serialization Format (for LLM consumption)

```
[P:abc123] "Attention Is All You Need" (Vaswani 2017) score=0.95
  [S:3] Methods: Multi-head attention mechanism with scaled dot-product
  -> cites [P:def456] "Neural Machine Translation"
  -> uses_method [M:transformer]
```

## Figure Handling

- Extract with pymupdf, link captions via spatial proximity
- Content-hash (SHA256) for dedup — same figure extracted twice is stored once
- Store at `data/figures/{hash[:2]}/{hash[2:4]}/{hash}.{ext}`
- Symlink or copy into vault `figures/` for Obsidian display
- Auto-tag from caption + surrounding text
- Composite figures: `CompositeRequest` model specifies layout + figure IDs, matplotlib renders

## Key Architectural Decisions

| Decision | Rationale |
|---|---|
| Obsidian vault as knowledge graph | Human-readable/editable, graph = wikilinks, user can browse + edit alongside programmatic access |
| SQLite for structured data | Chunks, embeddings, and metadata need fast programmatic queries |
| ChromaDB for vectors | Local embedded vector store, Windows-compatible |
| pymupdf4llm for parsing (current) | Local, fast, good structure preservation for 80% case |
| LiteParse (evaluating) | Spatial layout preservation, handles tables without structure detection, Python wrapper available |
| litellm for LLM access | Abstracts over Claude/GPT/local models |
| Content-addressed figures | Dedup across re-ingests and duplicate papers |
| Typed wikilinks | `[[hasTopic::X]]` enables structured queries while remaining valid Obsidian markdown |
| MCP for Claude ↔ Obsidian | Claude can read/search/modify vault notes directly via MCP servers |

## Data Directory Layout (gitignored)

```
data/
├── papers.db           # SQLite
├── vectors/            # ChromaDB index
├── figures/            # Content-addressed images
└── cache/              # LLM response cache

vault/                  # Obsidian vault (could be separate repo or synced)
├── papers/
├── topics/
├── concepts/
├── methods/
├── authors/
├── datasets/
├── findings/
└── figures/            # Symlinks to data/figures/
```

## Dependencies (Phase 1 — minimal)

```
pymupdf, pymupdf4llm          # PDF parsing (current)
liteparse                      # PDF parsing (evaluating — Python wrapper for TS core)
sqlmodel                       # SQLite ORM
chromadb                       # Embedded vector DB
networkx, python-louvain       # Graph algorithms
sentence-transformers          # Local embeddings
litellm, tiktoken, diskcache   # LLM interface
pyzotero, bibtexparser>=1.4    # References
matplotlib, plotly             # Visualization
python-docx, python-pptx      # Office formats
polars                         # Data file parsing (CSV, Excel, Parquet) — preferred over pandas
pyarrow                        # Parquet support + efficient schema reading
jinja2                         # Templating
typer, pydantic-settings, rich # CLI + config
```
