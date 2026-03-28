# ScholarForge — Project Status

## Architecture Pivot (2026-03-28)

Shifted from "build everything" to **vault-first + JIT** strategy:

- **Obsidian IS the app**. ScholarForge is the engine that feeds it.
- **JIT retrieval**: No LLM at ingestion. Ingestion is pure local compute (~8 min for
  200 papers). LLM calls only at query/generation time with progressive narrowing
  (200 → 40 → 15 papers). The LLM never reads all papers.
- **Ghost Graph**: The knowledge graph is built from typed frontmatter + wikilinks at
  ingestion time. Obsidian renders it. No graph DB. Already contains: 464 author nodes,
  20 topics, 9 methods, and all cross-links for 206 papers.
- Obsidian plugins handle: vector search (Smart Connections), entity extraction (Neural
  Composer/LightRAG), queries (Dataview), visualization (Graph View).
- Removed `networkx`, `python-louvain` from deps (Ghost Graph + Neural Composer handle graph).
- **Kept** `chromadb` + `sentence-transformers` — scoped to abstract embeddings + k-NN
  similarity graph only (~200 vectors). Full-vault vector search is Smart Connections' job.
- Removed planned modules: `store/vectors.py` (replaced by focused `store/embeddings.py`),
  `graph/` package, `vault/sync.py`.
- Added planned modules: `store/embeddings.py` (abstract ChromaDB), `retrieve/` package.

## Implementation Phases

### Phase 1 — Foundation (COMPLETE)

- [x] UV project + pyproject.toml + hatchling build
- [x] Python 3.12 via UV
- [x] All dependencies installed via `uv sync`
- [x] Module scaffold (all directories + `__init__.py` files)
- [x] `store/models.py` — Paper, Chunk, Figure, Citation + graph enums + PaperPlan
- [x] `store/db.py` — SQLite engine + session
- [x] `config.py` — pydantic-settings with all paths/defaults
- [x] `cli.py` — Typer CLI: `ingest` (with `--parallel`, `--workers`), `link`, `stats`
- [x] `ingest/pdf.py` — pymupdf4llm pipeline (parse/persist separated for parallelism)
- [x] `ingest/registry.py` — extension dispatcher + ProcessPoolExecutor parallel ingestion
- [x] `extract/chunker.py` — section-aware semantic chunking
- [x] `extract/metadata.py` — title/authors/abstract/DOI + filename pattern `[YYYY Author] Title.pdf`
- [x] `extract/figures.py` — content-addressed figure extraction (to be replaced by caption-first)
- [x] Git repo initialized, committed, pushed to GitHub
- [x] `.gitignore` configured (data/ is gitignored)

### Phase 2 — Vault + Linking (IN PROGRESS)

- [x] `vault/__init__.py`, `vault/writer.py`, `vault/templates.py` — note generation
- [x] `vault/linker.py` — keyword-based topic/method detection + linking
- [x] Vault output moved to `data/vault/` (gitignored)
- [x] Full 206-paper ingestion tested (see Benchmarks below)
- [ ] **Implement `store/embeddings.py`** — ChromaDB abstract embeddings + k-NN similarity graph
- [ ] **Add `similar_to` edges** to paper vault notes from k-NN results
- [ ] Implement `FigureRef` model + caption-first extraction (replace binary figure extraction)
- [ ] Implement citation extraction from bibliography sections (regex-based, no LLM)
- [ ] Implement bibliographic coupling (shared references → edges)
- [ ] Evaluate Marker as fallback parser on 10 hard papers
- [ ] Set up Obsidian vault with recommended plugins (Neural Composer, Smart Connections, Dataview)
- [ ] Test Ghost Graph rendering in Obsidian graph view
- [ ] Configure MCP server for Claude Code ↔ vault integration

### Phase 3 — JIT Retrieval + Generation

- [ ] `llm/client.py` with litellm + diskcache caching
- [ ] `retrieve/narrower.py` — progressive narrowing: frontmatter → abstract → chunks
- [ ] `retrieve/context.py` — token budget allocator (200 papers → 30K tokens)
- [ ] `retrieve/claims.py` — on-demand claim extraction for selected papers only
- [ ] `generate/planner.py` — TOC generation from vault structure
- [ ] `generate/writer.py` — section-by-section generation
- [ ] Jinja2 prompt templates for lit review
- [ ] Test: generate 3-section lit review from 20 papers

### Phase 4 — Export + Polish

- [ ] `export/docx_export.py` — python-docx output with style templates
- [ ] `export/latex_export.py` — LaTeX/Jinja2 templates
- [ ] `export/bibliography.py` — BibTeX/CSL citation formatting
- [ ] `ingest/zotero.py` — Zotero library sync
- [ ] Scale test: full 200-paper lit review end-to-end

## Benchmarks

### 206-Paper Ingestion (ALD/Memristor/Neuromorphic)

| Mode | Time | Per Paper | Speedup |
|---|---|---|---|
| Sequential | 1065s | 5.2s | — |
| Parallel (4 workers) | 460s | 2.2s | 2.3× |

- **Paper notes created**: 205 in `data/vault/papers/`
- **Author notes created**: 464 in `data/vault/authors/`
- **Topics detected**: 20 (keyword-based)
- **Methods detected**: 9 (keyword-based)
- **Linking time**: 1.9s for all 206 papers

### Bottleneck Profile (per paper)

| Stage | Time | Notes |
|---|---|---|
| pymupdf4llm parse | 1-5s | Dominant cost. Parallel helps. |
| Metadata extraction | <5ms | Regex + PDF fields |
| Chunking | 5-40ms | Depends on paper length |
| Figure extraction | 0-3s | Scanned papers are worst case |
| DB persist | <10ms | Sequential, fast |
| Vault write | <5ms | Just file writes |

## Known Issues

- **Scanned papers** (e.g. 1971 Chua): pymupdf4llm extracts hundreds of image tiles.
  Caption-first approach will eliminate this issue entirely.
- **Garbled titles**: Some PDFs have internal refs as first heading (e.g. `acs_nn_nn-2014-01824r`).
  Fixed by garbled-title detection + filename fallback.
- **Author extraction**: Many PDFs lack author metadata. Filename-derived first author
  only gives surname. Full author lists need CrossRef/Semantic Scholar enrichment.

## Obsidian Plugin Setup (Recommended)

| Plugin | Purpose | Priority |
|---|---|---|
| **Neural Composer** | LightRAG: auto entity extraction + knowledge graph | High |
| **Smart Connections** | Semantic vector search + chat sidebar | High |
| **Dataview** | SQL-like queries on frontmatter properties | High |
| **Zotero Integration** | Sync Zotero library + annotations | Medium |
| **Supercharged Links** | Color-code links by note type tag | Medium |
| **Marker** | In-Obsidian PDF→MD for manual conversion | Low |
| **Local REST API** | HTTP access for external tools | Low (MCP covers this) |

## User Info

- **Name**: Fabio Grillo
- **GitHub email**: fabio.grillo89@gmail.com
- **Platform**: Windows 11, bash shell
- **Python**: 3.12.11 via UV
- **Tools**: UV 0.7.13, git configured, gh CLI v2.89.0

## Resume Instructions

1. Read `CLAUDE.md` for working conventions
2. Read `docs/architecture.md` for vault-first architecture
3. Read this file for current status
4. **Next step**: Implement FigureRef + caption-first extraction, then claim extraction
5. All code is in `src/scholarforge/`; vault output goes to `data/vault/`
6. The vault-first pivot means we no longer build: vector store, graph algorithms,
   graph visualization, or chat UI — Obsidian plugins handle those
