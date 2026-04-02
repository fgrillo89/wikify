# ScholarForge -- Project Status

## Current State (2026-03-31)

ScholarForge is a working end-to-end pipeline with a 206-paper ALD/memristor corpus.
The exploration strategy has been optimized through 9 strategy variants benchmarked
on 7 quality metrics. Reviews generate in 3-5 minutes with gap identification and
cross-paper synthesis.

### What Works

**Ingestion (Phase 1 -- complete, no LLM):**
- PDF/DOCX/PPTX parsing (pymupdf4llm + fitz fallback, no OCR by default)
- Metadata extraction, section-aware chunking (600-token), figure/table refs
- Bibliography extraction + fuzzy citation matching (prefix + fuzzy scoring)
- ChromaDB embeddings: per-paper summaries + per-chunk + per-section (ONNX quantized)
- Paper vibe vectors: token-weighted chunk centroids (0.4s from stored embeddings)
- Obsidian vault: paper notes, author notes, topic hubs, Dashboard
- Incremental + parallel batch ingestion (60% of CPU cores by default)
- Auto-generated `library.bib` on every ingest
- Conclusion fallback: last section marked as conclusion if no heading matches
- Corpus/output isolation: Paper.origin field, Project scoping, GeneratedOutput tracking

**Exploration & Quality (Phase 2 -- optimized):**
- Enhanced hybrid strategy: greedy seeds + frontier papers + bridge papers + serendipity
- Frontier detection: density-ranked papers in sparse embedding regions
- Bridge computation: vibe midpoints between seed-frontier pairs (replaces random walks)
- 7 quality metrics: frontier shift, bridge vectors, semantic residual, gap detection,
  argumentative coherence, topic coverage, factual specificity
- All metrics computable from stored embeddings in 4-5s per review
- Gap detection: embedding voids + regex gap-claim detection
- Agent tools: find_corpus_gaps, find_synthesis_opportunities, get_frontier_exploration_order,
  suggest_next_papers, find_jump_target, evaluate_coverage, get_paper_vibes
- Reading log: per-tool reason tracking for reproducibility
- Greedy submodular paper ordering: lazy heap, O(N log N), 500 papers in 3s

**Generation (Phase 2 -- implemented):**
- Default strategy: enhanced hybrid (greedy + frontier + bridge + serendipity + gaps)
- Snowball strategy as fallback (5 retrieval strategies available)
- Artifact types: lit review, research article, grant proposal, technical report,
  master thesis, PhD thesis, undergrad research paper
- Academic writing style guide with sentence complexity rules
- Hard bans: zero em-dashes, no abstract citations, one concept per sentence
- Chemical formula subscripting (HfO2 in markdown, native subscripts in DOCX)
- Reference resolver: prefix matching + fuzzy scoring, warns on unresolved refs
- Post-processing: em-dash safety net, duplicate References section stripping

**Export (Phase 3 -- complete):**
- DOCX with publisher templates (template cloning, SQLite registry)
- PDF (default on) via DOCX->PDF or HTML fallback
- PPTX with professional template
- Markdown with Unicode chemical subscripts

**Hierarchical Retrieval (Phase 2b -- implemented, PageIndex-inspired):**
- PDF TOC extraction via fitz.get_toc() for better section structure
- Chunk-level semantic queries (query_chunks) on existing ChromaDB collection
- Section summaries: extractive (default, free) or LLM (opt-in, ~$0.002/paper)
- Section summary embeddings in dedicated ChromaDB collection
- `read_section` tool: mid-granularity reading (~5KB per section)
- Progressive disclosure: get_paper (200 chars) -> digest (1.5KB) -> read_section (5KB) -> deep_read (70KB)
- `--strategy hierarchical`: 3-level cascade (paper -> section -> chunk)
- `/generate-hierarchical` skill: digest all, drill selectively

**MCP Server (Phase 4 -- implemented):**
- 25+ tools: search_papers, get_paper, list_papers, deep_read, read_section,
  get_sections, get_graph_metrics, get_corpus_summary, scan_all_abstracts,
  read_paper_digest, get_paper_vibes, find_corpus_gaps, find_synthesis_opportunities,
  get_frontier_exploration_order, suggest_next_papers, find_jump_target,
  evaluate_coverage, get_coverage_gaps, get_reading_log_text, save_reading_log,
  ingest_paper
- `.mcp.json` configured for Claude Code integration

**Testing: 274 unit tests, all passing.**

### CLI Commands

| Command | Status | Description |
|---|---|---|
| `scholarforge ingest <path>` | Working | Ingest PDFs/DOCX/PPTX (--parallel, --workers) |
| `scholarforge refresh` | Working | Recompute all batch signals + regenerate vault |
| `scholarforge stats` | Working | Show paper/chunk/figure counts |
| `scholarforge graph` | Working | Show PageRank, centrality, hub/bridge/frontier |
| `scholarforge generate "prompt"` | Working | Generate paper (--strategy snowball, --journal) |
| `scholarforge slides "topic"` | Working | Generate PPTX presentation |
| `scholarforge chat` | Working | Interactive literature Q&A |
| `scholarforge mcp` | Working | Launch MCP server for Claude Code |
| `scholarforge templates list` | Working | Show available DOCX/LaTeX templates |
| `scholarforge templates import` | Working | Import a .docx as reusable template |
| `scholarforge templates download` | Working | Auto-download publisher templates |

## Remaining Work

### High Priority
- [ ] Citation-only PageRank: separate citation authority from embedding similarity
- [ ] Seed selection: #1 PageRank + #2-3 greedy coverage (orthogonal views)
- [ ] Pass precomputed embeddings through all quality metrics (40s -> 5s)
- [ ] Expert review: qualitative assessment of best reviews as materials scientist

### Medium Priority
- [ ] LaTeX export with .cls files and BibTeX integration
- [ ] Ollama support for fully offline generation
- [ ] Talk-to-corpus mode: same tools, conversational output
- [ ] Coverage metric optimization: ANN query instead of brute-force at 25k+ chunks

### Low Priority
- [ ] Claims extraction (JIT)
- [ ] Note model + FTS5 search
- [ ] Attention pooling for paper vibes (learn which chunks matter)
- [ ] GPU embedding support for 17x speedup

### Future Phase: Output Promotion
- [ ] `/promote` command: flip Paper.origin from "generated" to "corpus"
- [ ] Re-chunk, embed, integrate into citation graph
- [ ] "Draft" intermediate state between generated and corpus

## Benchmarks (206-Paper Corpus)

| Metric | Value |
|---|---|
| Papers | 206 (ALD/memristor/neuromorphic, 1971-2026) |
| Chunks | 6,531 |
| Chunk embeddings | 6,531 (ChromaDB, ONNX quint8_avx2) |
| Summary embeddings | 206 |
| Figure/table refs | 2,730 |
| Citation cross-refs | 936 |
| Topics | 1,232 (268 vocabulary terms) |
| Tests | 274 |
| Ingestion time | ~10 min (206 papers, 10 workers) |
| Review generation | 3-5 min (enhanced hybrid strategy) |

### Strategy Benchmark (7 quality metrics, 9 strategies tested)

| Strategy | Composite | Frontier | Bridge | Gaps | Chain | Time |
|----------|-----------|----------|--------|------|-------|------|
| random_walk | **0.489** | **0.910** | 9% | 0.008 | **0.515** | 16m |
| **enhanced_hybrid** | **0.459** | 0.738 | **15%** | 0.007 | 0.418 | **4.4m** |
| gap_aware | 0.447 | 0.767 | 8% | **0.012** | 0.460 | 4.5m |
| hybrid | 0.445 | 0.587 | **17%** | 0.004 | 0.446 | 4.75m |
| greedy_v2 | 0.420 | 0.605 | 5% | 0.000 | 0.542 | 3.4m |
| snowball_v4 | 0.347 | 0.269 | 8% | 0.012 | 0.388 | 24m |

Key: enhanced hybrid achieves 94% of random walk quality in 28% of the time.

### PageIndex Hierarchical Benchmark (5 strategies, automated + PI scores)

Hierarchical retrieval adds 3-level progressive disclosure: paper digest → section summary → full section.

| Strategy | Composite | Frontier | Arg Coherence | Topic Cov | Citations | PI Score |
|----------|-----------|----------|---------------|-----------|-----------|----------|
| s5_gap_structured | **0.634** | 0.967 | 0.383 | 28.4% | 80 | 7.8/10 |
| **hier_gap_first** | 0.611 | **1.000** | **0.482** | 17.8% | 10 | **8.8/10** |
| hierarchical_v1 | 0.596 | **1.000** | 0.392 | 17.8% | 28 | 6.5/10 |
| s5_injected | 0.572 | 0.677 | 0.364 | **31.8%** | 40 | 7.0/10 |
| hier_broad | 0.555 | 0.885 | 0.348 | 23.3% | 70 | 8.3/10 |

Key findings:
- **hier_gap_first scores 8.8/10 from PI** despite only 10 citations — the nucleation-filament cross-community synthesis is the most original contribution in any benchmark review
- **Metric vs PI diverge on creativity**: composite ranks s5_gap_structured #1, PI ranks hier_gap_first #1
- **More papers ≠ better**: hier_broad (70 citations) scores lowest on composite; broad reading collapses bridge_ratio and frontier_score
- **Gap-first unlocks coherence**: running find_corpus_gaps first forces a thesis, producing highest argumentative coherence (0.482) of any review
- **Path to 9.5/10**: one paragraph — state the predicted direction of first-cycle coverage / conductance-state-count, quantified from Matveyev trap density data

## Resume Instructions

1. Read `CLAUDE.md` for working conventions
2. Read this file for current status
3. Read `docs/architecture.md` for module layout and strategy design
4. Read `docs/design/research-loop-insights.md` for benchmark details
5. Code: `src/scholarforge/`; vault output: `data/vault/`
6. MCP: restart Claude Code to load `.mcp.json`, then use MCP tools
7. Key modules: `evaluate/quality.py` (metrics), `evaluate/frontier.py` (exploration),
   `evaluate/strategies.py` (greedy/max-distance/spectral), `agent/tools.py` (20+ tools)
