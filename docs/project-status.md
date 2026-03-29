# ScholarForge — Project Status

## Current State (2026-03-29)

ScholarForge is a working end-to-end pipeline. The ingestion pipeline is battle-tested
on 20 ALD/memristor papers (scoped from 206 for MVP). The generation pipeline
(paper writing, slides, literature chat) is implemented and wired to CLI commands.

### What Works

**Ingestion (Phase 1 — complete, no LLM required):**
- PDF parsing via pymupdf4llm with OCR fallback (RapidOCR) for scanned papers
- DOCX and PPTX ingestion
- Metadata extraction: title, authors, summary, DOI, year
- Section-aware chunking (600-token target, tiktoken-counted)
- Caption-first figure + table reference extraction (195 refs from 20 papers)
- Bibliography extraction + fuzzy citation matching (21 cross-references)
- Summary embeddings in ChromaDB (all-MiniLM-L6-v2)
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
- Auto deep-read: top 3 hub papers (by PageRank) get all chunks; rest get first 3
- Graph metrics: PageRank, centrality, hub/bridge/frontier classification
- Paper generation: structured planning → section-by-section writing
- Slides generation: plan via LLM → export to PPTX via python-pptx
- Interactive literature chat with retrieval-augmented generation
- Multi-library support: `--library` flag scopes all data per domain
- Full text in vault notes (Obsidian collapsible callout, invisible to LLM pipeline)

**MCP Server (Phase 3 — implemented):**
- FastMCP server exposable via `scholarforge mcp`
- Tools: search_papers, get_paper, list_papers, list_topics, deep_read, get_sections, get_graph_metrics, get_corpus_summary, ingest_paper
- Section classification: keyword-based normalizer maps raw headings to canonical types (introduction, methods, results, conclusion, etc.)
- Cross-paper queries: `get_sections("conclusion")` returns all conclusions across corpus
- Auto-injected `scholarforge://corpus` resource for LLM context

**Summary generalization:**
- `Paper.summary` (renamed from `abstract`) — works for all document types
- 4-strategy extraction: slide-aware synthesis → labeled section → first prose paragraph → fallback
- Slide summaries: first 3 slides + conclusion slides from last 3, including speaker notes
- PaperTopic junction table for efficient topic queries

**Testing (107 unit tests, all passing):**
- Metadata extraction, graph metrics, Paper model, chunker, vault templates
- No external services required (no API key, no ChromaDB)

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
| `scholarforge mcp` | Working | Launch MCP server for LLM tool access |

## Completed Phases

### Phase 1 — Ingestion Pipeline
- [x] PDF parsing (pymupdf4llm + OCR + fitz fallback)
- [x] Metadata extraction (regex + PDF fields + filename patterns)
- [x] Section-aware chunking
- [x] Caption-first figure/table reference extraction
- [x] Bibliography extraction + citation matching
- [x] ChromaDB summary embeddings + k-NN similarity
- [x] Bibliographic coupling
- [x] Topic extraction (corpus vocabulary from declared keywords)
- [x] Incremental + async ingestion architecture
- [x] Parallel batch ingestion
- [x] Obsidian vault generation with Ghost Graph
- [x] Dashboard + graph color configuration

### Phase 2 — Generation Pipeline
- [x] LLM client (litellm + diskcache)
- [x] Retrieval context assembly (ChromaDB query + chunk budget)
- [x] Auto deep-read top 3 hub papers by PageRank (all chunks vs 3 for rest)
- [x] Full text in vault notes (collapsed Obsidian callout, invisible to LLM)
- [x] Graph metrics (PageRank, centrality, hub/bridge/frontier)
- [x] Multi-library support (--library flag scopes DB/vault/embeddings per domain)
- [x] Paper planner (structured outline from prompt + literature)
- [x] Section-by-section writer
- [x] Slides planner + PPTX export
- [x] Literature chat (RAG-based Q&A)
- [x] CLI commands wired

### Phase 3 — MCP & Cross-Paper Intelligence
- [x] MCP server (FastMCP) with 9 tools
- [x] Section type classification (keyword regex → canonical IMRaD types)
- [x] Summary generalization (abstract → summary, multi-strategy extraction)
- [x] Slide-aware summary synthesis (first 3 + conclusion slides)
- [x] PaperTopic table for efficient topic queries
- [x] `get_sections` tool for cross-paper section queries

**Reference Management:**
- Auto-generated `library.bib` on every batch ingest (importable by Zotero, Mendeley, JabRef)
- Bibliography uses journal profile `reference_format` (AFM: `N. Authors, Title. Year.`)
- DOCX bibliography renders `[N]` as plain text (not superscript)

**Academic Writing Style Guide (`docs/logic/academic_writing_style.md`):**
- Synthesized from Williams, Strunk & White, Orwell, Schimel, Sword, Graff/Birkenstein, McEnerney
- Auto-injected into LLM persona system prompt before every generation call
- Self-revision requirement: LLM checks output against guide rules after drafting

**Obsidian Graph (cleaned up):**
- Paper notes: authors/topics stored as plain text (no wikilinks) — graph shows only paper-to-paper connections
- Default graph filter: `path:papers` (papers network only)
- Switch to `path:authors` for author collaboration view
- Color groups: papers (blue), authors (green), topics (orange)

## Remaining Work

### High Priority
- [ ] Set up ANTHROPIC_API_KEY in .env and test generation end-to-end
- [ ] Scale to full 206-paper corpus

### Medium Priority
- [ ] LaTeX export with citation formatting
- [ ] Claims extraction (JIT, for selected papers only)
- [ ] Note model + FTS5 search for personal notes
- [ ] Ollama support for fully offline generation
- [ ] Add journal/volume/pages fields to Paper model for complete reference formatting

### Low Priority
- [ ] Section-level embeddings (expensive, may not be needed with good MCP tools)
- [ ] Abstract reranking step in retrieval narrowing

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
