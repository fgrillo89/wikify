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
- [ ] Implement gap detection tools (`find_corpus_gaps`, `find_synthesis_opportunities`)
- [ ] Implement novel synthesis metric (source diversity * novelty * relevance per review chunk)
- [ ] Add gap/synthesis instructions to /generate skill prompt
- [ ] Re-run research loop with gap-aware strategy

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

### Future Phase: Output Promotion
- [ ] **Promote generated output to corpus**: A `/promote` command that takes a
  GeneratedOutput, flips its Paper.origin from "generated" to "corpus", embeds
  its chunks into ChromaDB, and integrates it into the citation graph. This lets
  a user's own draft become part of the knowledge base, enabling the agent to
  cite and build upon previous writing. Requires: re-chunking the output as if
  it were an ingested paper, updating the similarity graph, and refreshing vault
  notes. The user should review the draft before promotion (the agent should not
  auto-promote). Consider a "draft" intermediate state between generated and corpus.

## Benchmarks (206-Paper Corpus)

| Metric | Value |
|---|---|
| Papers | 206 (ALD/memristor/neuromorphic, 1971-2026) |
| Chunks | 6,531 |
| Chunk embeddings | 6,531 (ChromaDB) |
| Figure/table refs | 2,730 |
| Citation cross-refs | 936 |
| Topics | 1,232 (268 vocabulary terms) |
| Tests | 274 |

### Quality Metrics (recalibrated, 9 dimensions)

| Strategy | Time | Composite | Centroid | Topics | Coherence | Span | Factual |
|----------|------|-----------|----------|--------|-----------|------|---------|
| greedy_v2 | 3.4m | 0.525 | 0.904 | 0.232 | 0.525 | 0.231 | 0.974 |
| greedy_v1 | 4m | 0.524 | 0.905 | 0.232 | 0.466 | 0.253 | 0.976 |
| snowball_v4 | 24m | 0.521 | 0.925 | 0.300 | 0.433 | 0.221 | 0.910 |

Key: greedy strategies achieve same quality in 3-4 min as snowball in 24 min.

## Resume Instructions

1. Read `CLAUDE.md` for working conventions
2. Read this file for current status
3. Code: `src/scholarforge/`; vault output: `data/vault/`
4. MCP: restart Claude Code to load `.mcp.json`, then use MCP tools
5. Templates: `scholarforge templates list` to see available templates
6. Artifact types: `lit_review`, `research_article`, `grant_proposal`,
   `technical_report`, `master_thesis`, `phd_thesis`, `research_paper_undergrad`
