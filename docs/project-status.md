# ScholarForge — Project Status

## Current State (2026-03-29)

ScholarForge is a working end-to-end pipeline. The ingestion pipeline is battle-tested
on 20 ALD/memristor papers (scoped from 206 for MVP). The generation pipeline
(paper writing, slides, literature chat) is implemented and wired to CLI commands.

### What Works

**Ingestion (Phase 1 — complete, no LLM required):**
- PDF parsing via pymupdf4llm with OCR fallback (RapidOCR) for scanned papers
- DOCX and PPTX ingestion
- Metadata extraction: title, authors, abstract, DOI, year
- Section-aware chunking (600-token target, tiktoken-counted)
- Caption-first figure + table reference extraction (195 refs from 20 papers)
- Bibliography extraction + fuzzy citation matching (21 cross-references)
- Abstract embeddings in ChromaDB (all-MiniLM-L6-v2)
- k-NN similarity graph (top-5 per paper)
- Bibliographic coupling
- Topic extraction from corpus vocabulary (author-declared keywords)
- Obsidian vault generation: paper notes, author notes, topic hubs, Dashboard
- Graph config with color groups (blue=papers, orange=topics, green=authors)
- Incremental ingestion (O(1) per paper) + async background refresh
- Parallel batch ingestion via ProcessPoolExecutor

**Generation (Phase 2 — implemented, needs API key):**
- LLM client with litellm + diskcache response caching
- Retrieval: ChromaDB k-NN + token-budgeted chunk assembly
- Graph metrics: PageRank, centrality, hub/bridge/frontier classification
- Paper generation: structured planning → section-by-section writing
- Slides generation: plan via LLM → export to PPTX via python-pptx
- Interactive literature chat with retrieval-augmented generation

### CLI Commands

| Command | Status | Description |
|---|---|---|
| `scholarforge ingest <path>` | Working | Ingest PDFs/DOCX/PPTX (supports --parallel) |
| `scholarforge refresh` | Working | Recompute all batch signals + regenerate vault |
| `scholarforge stats` | Working | Show paper/chunk/figure counts |
| `scholarforge graph` | Working | Show PageRank, centrality, hub/bridge/frontier |
| `scholarforge generate "prompt"` | Implemented | Generate review paper (needs API key) |
| `scholarforge slides "topic"` | Implemented | Generate PPTX presentation (needs API key) |
| `scholarforge chat` | Implemented | Interactive literature Q&A (needs API key) |

## Completed Phases

### Phase 1 — Ingestion Pipeline
- [x] PDF parsing (pymupdf4llm + OCR + fitz fallback)
- [x] Metadata extraction (regex + PDF fields + filename patterns)
- [x] Section-aware chunking
- [x] Caption-first figure/table reference extraction
- [x] Bibliography extraction + citation matching
- [x] ChromaDB abstract embeddings + k-NN similarity
- [x] Bibliographic coupling
- [x] Topic extraction (corpus vocabulary from declared keywords)
- [x] Incremental + async ingestion architecture
- [x] Parallel batch ingestion
- [x] Obsidian vault generation with Ghost Graph
- [x] Dashboard + graph color configuration

### Phase 2 — Generation Pipeline
- [x] LLM client (litellm + diskcache)
- [x] Retrieval context assembly (ChromaDB query + chunk budget)
- [x] Graph metrics (PageRank, centrality, hub/bridge/frontier)
- [x] Paper planner (structured outline from prompt + literature)
- [x] Section-by-section writer
- [x] Slides planner + PPTX export
- [x] Literature chat (RAG-based Q&A)
- [x] CLI commands wired

## Remaining Work

### High Priority
- [ ] Set up ANTHROPIC_API_KEY in .env and run end-to-end mock tests
- [ ] Multi-library support (different domains/fields)
- [ ] Scale to full 206-paper corpus
- [ ] DOCX export for generated papers

### Medium Priority
- [ ] Zotero integration
- [ ] LaTeX export with citation formatting
- [ ] Claims extraction (JIT, for selected papers only)
- [ ] Abstract reranking step in retrieval narrowing

### Low Priority
- [ ] Ollama support for fully offline generation
- [ ] MCP server for Claude Code ↔ vault integration

## Benchmarks

### 20-Paper Test Corpus (ALD/Memristor)

| Metric | Value |
|---|---|
| Papers ingested | 20 |
| Chunks | ~800 |
| Figure/table refs | 195 |
| Citation cross-refs | 21 |
| Topics | 22 |
| Authors | 91 |
| Refresh time | ~15s |

### Graph Metrics (top papers by PageRank)

1. Jo 2010 — Nanoscale memristor device as synapse (PR=0.097, hub)
2. Kim 2021 — 4K-memristor analog-grade crossbar (PR=0.073, hub)
3. Kim 2017 — Silicon nitride memristor (PR=0.066, hub)
4. Matveyev 2015 — ALD TiN/HfO2 resistive switching (PR=0.065, hub)

## Known Issues

- **Chua 1971 abstract**: OCR'd text fragments the abstract (gets truncated). Improved
  abstract extraction now extends short abstracts, but OCR quality limits results.
- **Bibliographic coupling**: Currently 0 papers coupled — bibliography text matching
  may need loosening.
- **API key setup**: Generation commands require ANTHROPIC_API_KEY (see .env.example).

## Resume Instructions

1. Read `CLAUDE.md` for working conventions
2. Read `docs/architecture.md` for vault-first architecture
3. Read this file for current status
4. All code is in `src/scholarforge/`; vault output goes to `data/vault/`
5. Generation requires ANTHROPIC_API_KEY in .env
