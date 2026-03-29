# ScholarForge — Architecture

## What is ScholarForge?

A local-first Python pipeline that turns a folder of academic PDFs into a living
Obsidian knowledge graph, then uses that graph to write papers, lit reviews, and
presentations.

**Core philosophy**: Obsidian IS the app. ScholarForge is the engine that feeds it.

## Design Principles

1. **Vault-first**: The Obsidian vault is the single source of truth. SQLite is a
   supporting index, not a parallel database. Everything meaningful lives as markdown.
2. **Plugin-over-code**: If an Obsidian plugin already does it well, don't build it.
3. **Incremental + async**: Adding one paper is O(1) sync; corpus-wide signals
   refresh asynchronously in a background thread.
4. **LLM reads as little as possible**: Graph metrics guide the LLM to start at hub
   papers and explore frontiers — no need to read all N papers.
5. **Local-first**: All parsing, embedding, and graph computation done locally.
   LLM calls are the only network dependency (and can use Ollama for offline).

## Implemented Features

### Phase 1 — Ingestion Pipeline (No LLM)

```
PDF → pymupdf4llm → markdown → metadata (regex) → chunks → SQLite
  → vault notes → topic extraction → similarity graph → citation graph
```

| Step | Tool | Speed | LLM? |
|---|---|---|---|
| Parse PDF → markdown | pymupdf4llm | ~2.2s/paper (parallel) | No |
| OCR fallback (scanned PDFs) | RapidOCR + fitz raw text | ~30s/paper | No |
| Extract metadata | Regex + PDF fields + filename patterns | <5ms/paper | No |
| Chunk text | Section-aware splitter (600-token target) | <40ms/paper | No |
| Extract figure/table refs | Caption-first regex (195+ refs) | <5ms/paper | No |
| Extract bibliography | Regex on references section | <5ms/paper | No |
| Write vault notes | File I/O (papers, authors, topics) | <5ms/paper | No |
| Embed abstracts | ChromaDB + all-MiniLM-L6-v2 | ~0.1s/paper | No |
| k-NN similarity graph | Cosine similarity on embeddings | <1s total | No |
| Citation graph | Fuzzy matching (year+author+title) | <1s total | No |
| Bibliographic coupling | Shared references (strength ≥ 2) | <1s total | No |
| Topic extraction | Corpus vocabulary from declared keywords | <1s total | No |

**Ingestion modes:**
- **Single file**: Fast incremental (O(1) sync) + background corpus refresh
- **Batch**: Parallel parse via ProcessPoolExecutor + single batch refresh
- **Refresh**: Re-run all batch steps without re-parsing

### Phase 2 — Generation Pipeline (LLM Required)

| Command | Description |
|---|---|
| `scholarforge generate "prompt"` | Generate a review paper (plan → write sections) |
| `scholarforge slides "topic"` | Generate a PowerPoint presentation |
| `scholarforge chat` | Interactive Q&A with the literature corpus |

**Generation architecture:**
1. **Retrieve**: ChromaDB k-NN + chunk assembly with token budget
2. **Graph metrics**: PageRank, centrality, hub/bridge/frontier classification
   guide the LLM to prioritize key papers and explore peripheral topics
3. **Plan**: LLM creates structured outline with source paper assignments
4. **Write**: Section-by-section generation with running context for coherence
5. **Export**: Markdown (papers) or PPTX (slides)

### Graph Metrics

NetworkX-based analysis of the citation + similarity + coupling graph:

| Metric | Purpose |
|---|---|
| **PageRank** | Identifies most influential/connected papers (hubs) |
| **Degree centrality** | Overall connectivity |
| **Betweenness centrality** | Bridge papers connecting different research clusters |
| **Peripheral detection** | Frontier papers covering emerging/niche topics |

These metrics are:
- Fed into LLM prompts to guide literature traversal
- Available via `scholarforge graph` CLI command
- Designed for reuse in downstream applications

## The Ghost Graph

Obsidian-native knowledge graph from YAML frontmatter + wikilinks. No graph DB.

**Four signal layers:**

| Layer | Source | Edge Type | Direction |
|---|---|---|---|
| **Topics** | Author-declared keywords + corpus vocabulary matching | Paper ↔ Topic | Undirected |
| **Similarity** | k-NN on abstract embeddings (ChromaDB, top-5) | Paper ↔ Paper | Undirected |
| **Citations** | Fuzzy bibliography matching against corpus | Paper → Paper | Directed |
| **Coupling** | Shared references (strength ≥ 2) | Paper ↔ Paper | Undirected |

Plus implicit edges: authored_by (Paper → Author), co-authorship (Author ↔ Author via shared papers).

## Module Layout

```
src/scholarforge/
├── cli.py                  # Typer CLI: ingest, refresh, stats, graph, generate, slides, chat
├── config.py               # pydantic-settings (supports .env files)
│
├── ingest/                 # Document ingestion (no LLM)
│   ├── pdf.py              # pymupdf4llm + OCR fallback (RapidOCR) + fitz raw text
│   ├── docx.py             # python-docx → markdown
│   ├── pptx.py             # python-pptx → markdown
│   └── registry.py         # Dispatcher + parallel orchestration + incremental/batch modes
│
├── extract/                # Structured extraction (no LLM)
│   ├── chunker.py          # Section-aware semantic chunking (tiktoken)
│   ├── metadata.py         # Title, authors, abstract, DOI, year (regex + PDF fields)
│   ├── figures.py          # Binary figure extraction (pymupdf)
│   ├── figure_refs.py      # Caption-first figure + table reference extraction
│   ├── citations.py        # Bibliography section extraction
│   └── cite_match.py       # Fuzzy citation matching (year + author + title scoring)
│
├── store/                  # Supporting index
│   ├── models.py           # SQLModel tables + Pydantic plan models
│   ├── db.py               # SQLite engine + session
│   └── embeddings.py       # ChromaDB: abstract embeddings + k-NN similarity
│
├── vault/                  # Obsidian vault management (no LLM)
│   ├── writer.py           # Generate/update paper + author notes
│   ├── linker.py           # Corpus vocabulary topic extraction + deduplication
│   ├── templates.py        # Note templates (paper, author, topic)
│   └── coupler.py          # Bibliographic coupling computation
│
├── graph/                  # Graph analysis
│   └── metrics.py          # PageRank, centrality, hub/bridge/frontier classification
│
├── retrieve/               # Context assembly for generation
│   └── context.py          # ChromaDB query + token-budgeted chunk retrieval
│
├── generate/               # Content generation (LLM required)
│   ├── planner.py          # Paper outline + slide deck planning
│   ├── writer.py           # Section-by-section paper generation
│   └── chat.py             # Interactive literature Q&A
│
├── export/                 # Output formatting
│   └── pptx_export.py      # python-pptx slide generation
│
└── llm/                    # LLM interface
    └── client.py           # litellm + diskcache response caching
```

## Vault Structure

```
data/vault/                     # Obsidian vault (gitignored)
├── papers/                     # One note per ingested paper
├── authors/                    # One note per author (auto-created)
├── topics/                     # Topic hub notes
├── Dashboard.md                # Entry point: paper table + topic index
└── .obsidian/                  # Graph config (color groups: blue/orange/green)
```

### Note Types

| Type | Tag | Graph Color | Example |
|---|---|---|---|
| Paper | `#source/paper` | Blue | `papers/Kim 2021 - 4K-memristor...md` |
| Author | `#author` | Green | `authors/Can Li.md` |
| Topic | `#topic` | Orange | `topics/Memristors.md` |

### Paper Note Contents

- YAML frontmatter: title, authors (wikilinks), year, tags, hasTopic, cites, similar_to, cites_same, file_hash, source_path
- Link to open original PDF (file:/// URI)
- Abstract (with citation brackets stripped)
- Cites section (direct citation links)
- Figure/Table References section
- Similar Papers section
- Bibliographic Coupling section
- Statistics (chunk + figure counts)

## Two-Phase Ingestion Model

### Single file: O(1) incremental + background refresh

```
1. Parse PDF → ParsedPaper (sync, ~2s)
2. Persist to SQLite + write vault note (sync, <100ms)
3. Extract topics from own keywords + cached vocabulary (sync, <50ms)
4. Embed abstract + query k-NN (sync, ~200ms)
5. Write paper note with available signals (sync, <10ms)
6. Spawn background thread → full corpus refresh (async)
```

### Batch: parallel parse + single refresh

```
1. Parse all PDFs in parallel (ProcessPoolExecutor, ~2.2s/paper)
2. Persist all sequentially (SQLite + vault)
3. Single _run_batch_steps():
   - Topic extraction (corpus vocabulary)
   - Citation graph
   - Figure/table ref re-extraction
   - Abstract embeddings
   - k-NN similarity
   - Bibliographic coupling
   - Clear + regenerate all author notes
   - Regenerate all paper notes with full signals
   - Write topic hub notes
```

## Data Directory Layout (gitignored)

```
data/
├── papers/                 # Source PDFs
├── papers.db               # SQLite (supporting index)
├── chromadb/               # Abstract embeddings
├── cache/                  # LLM response cache (diskcache)
├── corpus_vocabulary.json  # Cached topic vocabulary
├── output/                 # Generated papers + slides
└── vault/                  # Obsidian vault (point Obsidian here)
```

## Key Dependencies

| Category | Libraries |
|---|---|
| PDF parsing | pymupdf, pymupdf4llm, rapidocr-onnxruntime |
| Office formats | python-docx, python-pptx |
| Storage | sqlmodel (SQLite), chromadb, sentence-transformers |
| LLM | litellm, tiktoken, diskcache |
| Graph analysis | networkx |
| References | pyzotero, bibtexparser |
| Export | jinja2, matplotlib, plotly |
| CLI | typer, pydantic-settings, rich |

## Key Decisions

| Decision | Rationale |
|---|---|
| Vault-first | The vault IS the product. SQLite is a programmatic index. |
| Incremental + async | O(1) for adding one paper; background refresh for corpus signals. |
| Ghost Graph | Typed frontmatter + k-NN edges → Obsidian renders a rich graph. No graph DB. |
| Graph metrics for LLM | PageRank/centrality guide the model to start at hubs and explore frontiers. |
| Caption-first figures | Captions are what LLMs need for writing. Binary extraction on demand only. |
| Corpus vocabulary topics | Author-declared keywords are the only topic source; matched against papers without keywords. |
| OCR fallback chain | pymupdf4llm → fitz raw text → RapidOCR. Auto-detected by placeholder ratio. |
| litellm for LLM | Abstracts over Claude/GPT/Ollama. No lock-in. Disk-cached responses. |
