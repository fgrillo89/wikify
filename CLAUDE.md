# ScholarForge — Project Context

## What is ScholarForge?

A Python app to help researchers write papers, slides, abstracts, and grant proposals.
It manages a personal knowledge base built from ingested PDFs, slides, and docs into a
curated knowledge graph optimized for LLM consumption.

## Key Requirements

- **Knowledge graph**: Ingest PDFs, slides, docs into a curated graph with custom token-efficient data structures
- **Figure handling**: Extract figures, annotate with metadata (avoid re-ingestion), support reuse/combination
- **Output formats**: Word (.docx) and LaTeX
- **Reference management**: Zotero integration (pyzotero)
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
- **Location**: `C:\Users\fgril\OneDrive\Documents\scholarforge\`

## Architecture — 6-Stage Pipeline

```
PDF/DOCX/PPTX → [ingest] → [extract] → [store] → [graph] → [generate] → [export]
```

### Module Layout

```
src/scholarforge/
├── __init__.py
├── cli.py                  # Typer CLI entry point
├── config.py               # pydantic-settings config
│
├── ingest/                 # Stage 1: Document ingestion
│   ├── pdf.py              # pymupdf4llm
│   ├── slides.py           # python-pptx
│   ├── docx.py             # python-docx
│   ├── gdrive.py           # Optional: Google Drive
│   ├── zotero.py           # Zotero library sync
│   └── registry.py         # file extension → parser dispatcher
│
├── extract/                # Stage 2: Structured extraction
│   ├── chunker.py          # Section-aware semantic chunking (500-800 tok, no cross-section splits)
│   ├── figures.py          # Figure/table extraction + content-hash dedup
│   ├── references.py       # Citation extraction and linking
│   ├── metadata.py         # Title, authors, abstract, DOI
│   └── equations.py        # LaTeX equation extraction
│
├── store/                  # Stage 3: Storage layer
│   ├── models.py           # SQLModel/Pydantic data models (Paper, Chunk, Figure, Citation)
│   ├── db.py               # SQLite via SQLModel
│   ├── vectors.py          # LanceDB embedded vector store
│   ├── figures_store.py    # Content-addressed figure storage
│   └── migrations.py       # Schema versioning
│
├── graph/                  # Stage 4: Knowledge graph
│   ├── builder.py          # Build graph from extracted data
│   ├── schema.py           # Node/edge type definitions
│   ├── traverse.py         # Token-efficient traversal (4 strategies)
│   ├── queries.py          # Pre-built graph query patterns
│   └── serialize.py        # Graph → compact LLM-readable format
│
├── generate/               # Stage 5: Content generation
│   ├── planner.py          # TOC/structure generation (stage 1 of creation)
│   ├── writer.py           # Section-by-section generation (stage 2)
│   ├── prompts/            # Jinja2 prompt templates
│   │   ├── lit_review.j2
│   │   ├── abstract.j2
│   │   ├── grant_proposal.j2
│   │   └── section.j2
│   ├── context.py          # Token budget allocator (40% source, 20% figs, 10% structure, 30% output)
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

## Core Data Models

### SQLite (via SQLModel)

- **Paper**: id (SHA256 of file), title, authors, abstract, year, doi, zotero_key, source_path, file_hash, ingested_at, section_tree (JSON)
- **Chunk**: id (UUID), paper_id (FK), section_path (e.g. "3.Methods.3.2.Data Collection"), content, token_count, chunk_index, has_citations, has_equations
- **Figure**: id (content-hash), paper_id (FK), caption, figure_number, section_path, image_path (content-addressed), width_px, height_px, format, tags (JSON), extracted_data, reuse_count
- **Citation**: id, paper_id, cited_paper_id (FK nullable), raw_text, bibtex, csl_json, context_chunk_id

### Knowledge Graph (networkx, in-memory)

**Node types**: paper, section, chunk, figure, concept, author, method, dataset, finding
**Edge types**: contains, cites, describes, uses_method, uses_dataset, supports, contradicts, related_to, authored_by

Every node carries: type, db_id, summary (<200 tokens), token_cost, importance_score (PageRank), embedding
Every edge carries: type, weight (0-1), evidence (one sentence)

## Token-Efficient Graph Traversal Strategies

1. **Map-then-Dive**: Show LLM all paper nodes as 1-line summaries (~4K tokens for 200 papers), LLM picks which to expand
2. **Cluster Walkthrough**: Louvain community detection → cluster summaries (~500 tokens for 200 papers), expand one cluster at a time
3. **Query-Guided Subgraph**: Embed query → top-K nearest chunks → extract 2-hop subgraph → serialize only that
4. **Progressive Summary Pyramid**: 3-level precomputed hierarchy (chunks → sections → papers → clusters), always start at top

### Compact Serialization Format (not JSON — 3-5x fewer tokens)

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
- Auto-tag from caption + surrounding text
- Composite figures: `CompositeRequest` model specifies layout + figure IDs, matplotlib renders

## Staged Paper Creation

### Stage 1 — PaperPlan (structure)
```
PaperPlan: title, paper_type, target_length, sections[]
SectionPlan: heading, level, description, target_tokens, source_papers[], figures[]
FigurePlan: type (reuse|composite|generate), source_figure_ids, generation_spec, caption_draft
```

### Stage 2 — Section-by-section generation
For each section: query-guided subgraph → token budget packing → generate → verify citations

## Dependencies (Phase 1 — minimal)

```
pymupdf, pymupdf4llm          # PDF parsing
sqlmodel                       # SQLite ORM
chromadb                       # Embedded vector DB (replaced lancedb — no Windows wheels)
networkx, python-louvain       # Graph
sentence-transformers          # Local embeddings
litellm, tiktoken, diskcache   # LLM interface
pyzotero, bibtexparser>=1.4    # References
matplotlib, plotly             # Visualization
python-docx, python-pptx      # Office formats
jinja2                         # Templating
typer, pydantic-settings, rich # CLI + config
```

## Implementation Phases

### Phase 1 — Foundation (CURRENT)
- [x] UV project created at `C:\Users\fgril\OneDrive\Documents\scholarforge\`
- [x] pyproject.toml with hatchling build
- [x] Add dependencies to pyproject.toml (chromadb instead of lancedb — no Windows wheels)
- [x] Python upgraded to 3.12 (uv python pin 3.12)
- [x] All dependencies installed via `uv sync`
- [x] Create full module scaffold (all `__init__.py` files + directories)
- [x] Implement `store/models.py` — Paper, Chunk, Figure, Citation + graph enums + PaperPlan
- [x] Implement `store/db.py` — SQLite engine + session
- [x] Implement `config.py` — pydantic-settings with all paths/defaults
- [x] Implement `cli.py` — Typer CLI with `ingest` and `stats` commands
- [x] Implement `ingest/pdf.py` — pymupdf4llm full pipeline
- [x] Implement `ingest/registry.py` — file extension dispatcher
- [x] Implement `extract/chunker.py` — section-aware semantic chunking
- [x] Implement `extract/metadata.py` — title/authors/abstract/DOI extraction
- [x] Implement `extract/figures.py` — content-addressed figure extraction
- [x] VSCode workspace file created
- [x] `.gitignore` configured
- [x] Git repo initialized, all files staged
- [ ] **NEXT: Run `gh auth login` (user email: fabio.grillo89@gmail.com)**
- [ ] **NEXT: Create GitHub repo and push initial commit**
- [ ] Test: ingest 5 papers, verify chunks + metadata in SQLite

### Phase 2 — Graph + Figures
- [ ] `extract/figures.py` with content-addressed storage
- [ ] `graph/schema.py`, `graph/builder.py`
- [ ] `store/vectors.py` with LanceDB
- [ ] `graph/serialize.py` and `graph/traverse.py`
- [ ] Test: ingest 20 papers, verify graph connectivity

### Phase 3 — Generation
- [ ] `llm/client.py` with litellm + caching
- [ ] `generate/planner.py` — TOC generation
- [ ] `generate/context.py` — token budget packing
- [ ] `generate/writer.py` — section generation
- [ ] Jinja2 prompt templates for lit review
- [ ] Test: generate 3-section lit review from 20 papers

### Phase 4 — Export + Polish
- [ ] `export/docx_export.py` and `export/latex_export.py`
- [ ] `ingest/zotero.py` and `export/bibliography.py`
- [ ] `generate/figures_gen.py`
- [ ] CLI (`cli.py`)
- [ ] Scale test: full 200-paper lit review end-to-end

## Key Architectural Decisions

| Decision | Rationale |
|---|---|
| SQLite + ChromaDB (not Postgres/Weaviate) | Local-first, zero-config, no server (ChromaDB replaced LanceDB due to no Windows wheels) |
| networkx (not Neo4j) | 200 papers fits in memory; built-in PageRank + community detection |
| pymupdf4llm (not GROBID) | Local, fast, good structure preservation for 80% case |
| litellm (not raw API) | Abstracts over Claude/GPT/local models; user can switch without code changes |
| Content-addressed figures | Dedup across re-ingests and duplicate papers |
| Compact serialization (not JSON) | 3-5x fewer tokens for graph maps |

## Data Directory Layout (gitignored)

```
data/
├── papers.db           # SQLite
├── vectors/            # LanceDB index
├── figures/            # Content-addressed images
└── cache/              # LLM response cache
```

## Setup Progress

- **UV project**: Created ✓
- **Python**: 3.12.11 via UV (upgraded from 3.10) ✓
- **Dependencies**: All installed via `uv sync` ✓
- **Module scaffold**: All directories + Phase 1 code ✓
- **Git repo**: Initialized, all files staged, no commits yet ✓
- **GitHub repo**: NOT YET CREATED — `gh auth login` needed first
- **VSCode workspace**: `scholarforge.code-workspace` ✓

## User Info

- **Name**: Fabio Grillo
- **GitHub email**: fabio.grillo89@gmail.com
- **Work email**: f.grillo@altastechnologies.com
- **Platform**: Windows 11, bash shell
- **Python**: 3.12.11 via UV
- **Tools**: UV 0.7.13, git configured, gh CLI v2.89.0 installed (needs `gh auth login`)

## Resume Instructions

When resuming this project:
1. Read this file for full context
2. The immediate next step is: user runs `gh auth login` → then create GitHub repo + initial commit
3. After that: test the ingestion pipeline with sample PDFs
4. All Phase 1 code is written and ready — see files in `src/scholarforge/`
5. The `vectors.py` module references should use ChromaDB (not LanceDB)
