# ScholarForge — Project Status

## Current State (2026-03-29)

ScholarForge is a working end-to-end pipeline. The ingestion pipeline is battle-tested
on 20 ALD/memristor papers. Generation is implemented via MCP server (Claude Code agents
use MCP tools to read the corpus and write papers directly — no separate API key needed).

### What Works

**Ingestion (Phase 1 — complete, no LLM):**
- PDF/DOCX/PPTX parsing (pymupdf4llm + OCR fallback)
- Metadata extraction, section-aware chunking (600-token), figure/table refs
- Bibliography extraction + fuzzy citation matching
- ChromaDB embeddings + k-NN similarity, bibliographic coupling (threshold ≥ 3)
- Obsidian vault: paper notes (papers-only graph), author notes, topic hubs, Dashboard
- Incremental + parallel batch ingestion
- Auto-generated `library.bib` on every ingest (Zotero/Mendeley/JabRef compatible)

**Generation (Phase 2 — implemented):**
- 5 retrieval strategies: flat, hub-spoke, topic-cluster, query-driven, snowball
  (selectable via `--strategy` flag)
- Artifact types: lit review, research article, grant proposal, technical report,
  master thesis, PhD thesis, undergrad research paper
- Academic writing style guide (680 words) auto-injected into LLM persona
- Field-specific guides (7 fields + generic) with auto-detection from corpus topics
- Figure placeholders with detailed captions for downstream figure generation
- Chemical formula subscripting (HfO₂ in markdown, native subscripts in DOCX)
- Reference resolver with journal-specific formatting
- Section-by-section writing with self-revision requirement

**Export (Phase 3 — complete):**
- DOCX with real publisher templates (Wiley AFM downloaded, style-mapped)
  - Template cloning preserves exact formatting (spacing, fonts, headers, logos)
  - SQLite-tracked template registry (`scholarforge templates` commands)
  - User can import own paper as template
- PDF via DOCX→PDF (Word/LibreOffice) or HTML fallback
- PPTX with professional template
- Markdown with Unicode chemical subscripts

**MCP Server (Phase 4 — implemented):**
- 9 tools: search_papers, get_paper, list_papers, list_topics, deep_read,
  get_sections, get_graph_metrics, get_corpus_summary, ingest_paper
- `.mcp.json` configured for Claude Code integration
- Claude Code agents use MCP tools directly to explore corpus and write papers

**Testing: 136 unit tests, all passing.**

### CLI Commands

| Command | Status | Description |
|---|---|---|
| `scholarforge ingest <path>` | Working | Ingest PDFs/DOCX/PPTX (supports --parallel) |
| `scholarforge refresh` | Working | Recompute all batch signals + regenerate vault |
| `scholarforge stats` | Working | Show paper/chunk/figure counts |
| `scholarforge graph` | Working | Show PageRank, centrality, hub/bridge/frontier |
| `scholarforge generate "prompt"` | Working | Generate paper (--strategy, --journal flags) |
| `scholarforge slides "topic"` | Working | Generate PPTX presentation |
| `scholarforge chat` | Working | Interactive literature Q&A |
| `scholarforge mcp` | Working | Launch MCP server for Claude Code |
| `scholarforge templates list` | Working | Show available DOCX/LaTeX templates |
| `scholarforge templates import` | Working | Import a .docx as reusable template |
| `scholarforge templates download` | Working | Auto-download publisher templates |
| `scholarforge templates sources` | Working | Show publisher template URLs |
| `scholarforge templates styles` | Working | Inspect styles in a .docx file |

## Architecture Highlights

- **Retrieval strategies**: 5 strategies behind `RetrievalStrategy` ABC, selected via `--strategy`
- **Artifact types**: Each defines required sections + type-specific writing rules
- **Field guides**: 8 field-specific .md files (materials science, CS, biology, medicine,
  math, physics, social sciences, generic) auto-detected from corpus topics
- **Writing pipeline**: base style guide → artifact type rules → field guide → figure instructions
- **Template system**: SQLite-tracked, supports publisher templates + user papers as templates
- **Chemical formulas**: Regex detection + validation against periodic table, subscript rendering

## Remaining Work

### High Priority
- [ ] Test MCP-based generation end-to-end (restart Claude Code to activate .mcp.json)
- [ ] Scale to full 206-paper corpus
- [ ] Fix `list_topics` MCP tool (returns section headings, not real topics)

### Medium Priority
- [ ] LaTeX export with .cls files and BibTeX integration
- [ ] Add journal/volume/pages fields to Paper model
- [ ] N+1 query optimization in retrieval strategies (bulk-load chunks)
- [ ] Thread-safe SQLite access (lock around run_batch_steps)
- [ ] Ollama support for fully offline generation

### Low Priority
- [ ] Section-level embeddings
- [ ] Claims extraction (JIT)
- [ ] Note model + FTS5 search

## Benchmarks (20-Paper Test Corpus)

| Metric | Value |
|---|---|
| Papers | 20 (ALD/memristor, 1971-2025) |
| Chunks | ~800 |
| Figure/table refs | 195 |
| Citation cross-refs | 21 |
| Topics | 22 |
| Authors | 91 |
| Tests | 136 |

## Resume Instructions

1. Read `CLAUDE.md` for working conventions
2. Read this file for current status
3. Code: `src/scholarforge/`; vault output: `data/vault/`
4. MCP: restart Claude Code to load `.mcp.json`, then use MCP tools
5. Templates: `scholarforge templates list` to see available templates
6. Artifact types: `lit_review`, `research_article`, `grant_proposal`,
   `technical_report`, `master_thesis`, `phd_thesis`, `research_paper_undergrad`
