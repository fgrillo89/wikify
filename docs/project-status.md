# Wikify -- Project Status

## What is Wikify?

Wikify is a local-first Python pipeline with two distinct capabilities:

**A. Wikipedia Pipeline (primary focus):** Turns any corpus of PDFs, notes, and documents into
a concept-first, self-correcting personal Wikipedia. Concepts are discovered automatically from
the corpus (not from a pre-planned sitemap), built into a graph, and written into Markdown
articles that improve over multiple epochs until convergence.

**B. Research Paper Writing (secondary, enhanced by wiki later):** Generates literature reviews,
research papers, and presentations from the same corpus via a generate -> evaluate -> revise
pipeline. This pipeline will gain a richer knowledge layer once the wiki reaches maturity.

The system is model-agnostic, routing all LLM calls through litellm.

---

## A. Wikipedia Pipeline

### Wiki Building-Block Modules (complete)

All modules in `src/wikify/wiki/` are implemented and tested:

| Module | Purpose |
|--------|---------|
| `wiki/persona.py` | Domain persona generation and DB caching (`DomainPersona` table) |
| `wiki/mapreduce.py` | Map (haiku per source) + reduce (sonnet to article body) + coverage recording |
| `wiki/maintenance.py` | Three-tier updates: additive, revisionary, structural audit |
| `wiki/builder.py` | Article file I/O, slugify, hierarchical index generation, unanswered-question log |
| `wiki/linker.py` | Cross-reference pass: adds `[[wikilinks]]` and See Also sections |
| `wiki/sitemap.py` | `SitemapEntry`/`WikiSitemap` data contracts + exploration agent (optional, secondary role) |
| `wiki/agent.py` | `build_wiki_from_sitemap`, `build_article_from_entry`, `build_wiki_article` |

Data models for wiki in SQLite (`store/models.py`):
`WikiArticle`, `DomainPersona`, `SourceCoverage` -- all implemented.

### Wiki Epoch Pipeline (complete)

The concept-first epoch pipeline is fully implemented with a **dual execution model**:

**Skill-based (primary):** The `/wiki-epoch` skill (`.claude/skills/wiki-epoch.md`) orchestrates
the pipeline via Claude Code. The LLM spawns haiku subagents for batch extraction and article
writing, and calls Python tools for graph computation, DB operations, and file I/O. No API key
needed -- the LLM IS the model.

**Scripted (secondary):** `wiki/epoch.py` provides `run_epoch()` for automated/scheduled runs
via litellm (requires `ANTHROPIC_API_KEY`). Same 5-pass pipeline, same DB models.

All modules in `src/wikify/wiki/` and tested:

| Module | Purpose |
|--------|---------|
| `wiki/concepts.py` | `ConceptRecord`, `ConceptEvidence`, `ExtractionGap`, `ParameterExtraction` models + rich extraction pipeline with template system |
| `wiki/template.py` | Extraction template management: load/save/version, prompt building, self-consistent refinement with overfitting guard and pruning |
| `wiki/vectors.py` | Structured concept vectors for dedup: encodes type + relations + params into embedding strings |
| `wiki/concept_graph.py` | Co-occurrence graph, PageRank importance scoring, Louvain community detection, relation classification |
| `wiki/article.py` | Wikipedia-format article writer using `ConceptRecord` + graph neighbors |
| `wiki/epoch.py` | Epoch orchestrator: Passes 1-5, loss computation, convergence tracking (scripted mode) |
| `wiki/dashboard.py` | FastAPI dashboard API: convergence curve, concept graph, coverage heatmap, epoch log, gap clusters |

### Discovery Engine Alignment (complete)

Seven phases implemented aligning with the Discovery Engine framework (arxiv 2505.17500):

| Phase | What it does |
|-------|-------------|
| 0. Extraction Template | Evolving template replaces hardcoded prompt; concepts + params + mechanisms + relationships + gaps |
| 1. Source Evidence | `ConceptEvidence` with fuzzy quote verification against source text |
| 2. Meta-Probes | `ExtractionGap` captures what the template can't classify; `/api/gaps` dashboard |
| 3. Template Refinement | Gap-driven template evolution with overfitting guard and zero-yield pruning |
| 4. Two-Pass Extraction | Publication-level overview then targeted chunk deepening |
| 5. Parameter Extraction | `ParameterExtraction` model; auto-generated parameter tables in articles |
| 6. Structured Vectors | Type + relation + param enriched embedding strings for better dedup |

### Tiered Retrieval (complete)

Retrieval uses 4 tiers for speed:

| Tier | Mechanism | Latency |
|------|-----------|---------|
| 0 | Exact query cache (hash match) | ~0ms |
| 1 | Jaccard fuzzy cache (>= 0.6 overlap) | ~1ms |
| 2 | BM25 lexical search (if confident) | ~100ms |
| 3 | ChromaDB embedding search | ~500ms |

### Research Campaigns (complete)

Thesis-driven multi-epoch investigations (`/wiki-campaign` skill):
- `Campaign` SQLite model with thesis, confidence, findings, gaps, extraction probes
- Directed extraction: chunks scored by relevance to thesis
- Reads existing wiki first, then extracts only what's missing
- Refines existing articles with campaign-specific evidence
- Creates synthetic concepts (cross-cutting insights) only when needed
- Orchestrator has full freedom: merge, split, reclassify, or do nothing

### Adaptive Article Templates (complete)

Type-specific article structures in `src/wikify/prompts/article_templates.py`:
- 7 templates: material, technique, phenomenon, method, theory, dataset, synthesis
- Domain-agnostic (works for any field, not just materials science)
- Auto-injects parameter tables and evidence citations
- Generic fallback for unknown concept types

### Wiki Operational Model (complete)

Four skills, four modes (`.claude/skills/`):

| Skill | Mode | What it does |
|-------|------|-------------|
| `/wiki-epoch` | Build | Undirected extraction + articles + convergence |
| `/wiki-campaign` | Investigate | Thesis-driven research, opinionated epochs |
| `/wiki-ask` | Query | Answer questions, file answers back into wiki |
| `/wiki-maintain` | Maintain | GC + lint + auto-fix + self-enhance |

The cycle: ingest -> epoch -> ask -> maintain -> repeat. Each mode enriches the wiki.
Answers from `/wiki-ask` are filed back as `data/wiki/queries/` articles.

### Database Integrity (complete)

Garbage collection module (`store/gc.py`):
- `integrity_check()`: read-only health report (orphans, dangling refs, merged refs)
- `redirect_merged()`: fix references to merged concepts -> primaries
- `remove_orphans()`: delete rows pointing to nonexistent concepts/papers
- `clean_chromadb_staging()`: remove stale ChromaDB staging entries
- `gc_run()`: full GC pass, runs automatically in `/wiki-maintain`

### MCP Tools (13 total)

| Tool | Purpose |
|------|---------|
| `search_papers` | Semantic search across corpus |
| `search_wiki` | Search wiki concepts by name/definition |
| `get_paper` | Paper metadata + abstract |
| `deep_read` | Full paper text |
| `get_sections` | Cross-paper section retrieval |
| `get_corpus_summary` | Corpus overview with metrics |
| `get_graph_metrics` | PageRank, centrality, hub/bridge/frontier |
| `list_papers` | Browse all papers |
| `list_topics` | Browse extracted topics |
| `ingest_paper` | Add new documents |
| `check_wiki_health` | DB + wiki integrity report |
| `run_wiki_gc` | Trigger garbage collection |
| `search_wiki` | Search wiki concept articles |

### ML-Style Convergence Tracking (complete)

- Loss function `L` computed after each epoch's Pass 5 and stored in `EpochLog.loss_score` and
  `EpochLog.loss_delta`. Formula: `L = 0.3*stub_ratio + 0.2*orphan_concept_rate + 0.3*contradiction_density - 0.2*cross_ref_density`. Coefficients tunable in project config.
- Information gradient per concept: `new_evidence_tokens / existing_article_tokens`. Used to
  prioritise Pass 3 ordering and skip near-zero-gradient concepts that have stabilised.
- Momentum tracking: concepts with high gradient for 3+ consecutive epochs flagged
  `momentum: active` in YAML frontmatter; near-zero-gradient concepts for 3+ epochs skipped
  in Pass 3 unless new sources arrive.
- Model-selection schedule: haiku used for Pass 3 drafting while L >= 0.3; sonnet used once
  L < 0.3 (learning rate decay analog). Transition epoch recorded in `EpochLog`.

### Wiki CLI Commands

| Command | Status | Description |
|---------|--------|-------------|
| `wikify wiki init` | Working | Bootstrap wiki via sitemap pipeline |
| `wikify wiki expand` | Working | Expand stub/draft article to full |
| `wikify wiki sync` | Working | Update stale articles after new ingest |
| `wikify wiki audit` | Working | Structural health report (split/merge/orphan/drift) |
| `wikify wiki health` | Working | Orphan, staleness, and synthesis gap report |
| `wikify wiki epoch` | Working | Run one epoch (discovery + articles + cross-ref + index) |
| `wikify wiki epoch --n N` | Working | Run N epochs |
| `wikify wiki epoch --until-convergence` | Working | Run until convergence criteria met |
| `wikify wiki epoch --status` | Working | Show epoch log |
| `wikify wiki epoch --on-ingest` | Working | Auto-trigger epoch after ingest |

### What is Not Started (Wikipedia Pipeline)

- **Dashboard launch command**: `wikify wiki dashboard` CLI command to start the local FastAPI
  convergence/coverage/graph dashboard. The `wiki/dashboard.py` API is implemented; the CLI
  wiring is not yet done.
- **Obsidian dashboard layer**: auto-generated `_dashboard.md` per domain with live Dataview
  queries (stubs by domain, top concepts by importance, momentum-active concepts,
  recent-epoch updates). Written by Pass 5 each epoch. Not yet implemented.
- **Scheduled maintenance**: Automated `/wiki-maintain` runs (hooks or cron) to keep the wiki
  clean between manual sessions.

### Planned: Adaptive Knowledge Engine (partially implemented)

Six phases from `docs/design/adaptive-knowledge-engine.md`. Phases 1, 5 are superseded
by the Discovery Engine alignment work. Remaining:

| Phase | What it does | Status |
|-------|-------------|--------|
| 1. Yield-based feedback | Adaptive prompt per epoch | Superseded by template refinement |
| 2. UCB chunk scoring | UCB1-style scorer using yield + graph signals | Not started |
| 3. Contradiction-driven exploration | Boost mining priority near contradictions | Not started |
| 4. Hierarchical taxonomy | IS-A parent-child relationships | Not started |
| 5. Schema evolution | New concept types from corpus | Superseded by gap-driven template refinement |
| 6. Conceptual Nexus Model | Sparse tensor representation | Partially addressed by structured vectors |

---

## B. Research Paper Writing Pipeline

### Ingestion (complete, no LLM)

- PDF/DOCX/PPTX parsing (pymupdf4llm + fitz fallback, no OCR by default)
- Metadata extraction, section-aware chunking (600-token), figure/table refs
- Bibliography extraction + fuzzy citation matching (prefix + fuzzy scoring)
- ChromaDB embeddings: per-paper summaries, per-chunk, per-section (ONNX quantized)
- Paper vibe vectors: token-weighted chunk centroids (0.4s from stored embeddings)
- Obsidian vault: paper notes, author notes, topic hubs, Dashboard
- Incremental + parallel batch ingestion (60% of CPU cores by default)
- Auto-generated `library.bib` on every ingest
- Public ingest boundary: `ingest/service.py` and `ingest/corpus_refresh.py`
- Corpus/output isolation: `Paper.origin` field, `Project` scoping, `GeneratedOutput` tracking

### Exploration and Quality Metrics (complete)

- Enhanced hybrid strategy: greedy seeds + frontier papers + bridge papers + serendipity
- Frontier detection: density-ranked papers in sparse embedding regions
- Bridge computation: vibe midpoints between seed-frontier pairs
- 10-component automated composite quality report including prose quality
- PI-style evaluation via `wikify evaluate`
- Gap detection: embedding voids + regex gap-claim detection
- 25+ MCP/agent tools: `search_papers`, `deep_read`, `read_section`, `read_paper_digest`,
  `find_corpus_gaps`, `find_synthesis_opportunities`, `get_frontier_exploration_order`,
  `get_paper_vibes`, `suggest_next_papers`, `find_jump_target`, and others
- Greedy submodular paper ordering: lazy heap, O(N log N)
- Pre-compute cache: vibes, KMeans, gaps, links, section summaries (all <0.1s load time)

### Generation (complete)

- Five generation routes: skill (single-agent), hierarchical skill, scripted, two-agent,
  fast one-shot -- all sharing the same `ResearchNotes` writer handoff
- Artifact types: lit review, research article, grant proposal, technical report,
  master thesis, PhD thesis, undergrad research paper
- Academic writing style guide: zero em-dashes, no abstract citations, one concept per sentence
- Chemical formula subscripting (HfO2 in markdown, native subscripts in DOCX)
- Reference resolver: prefix matching + fuzzy scoring
- Run-scoped state via `RunContext`: reading log, paper summaries, concept graph, usage telemetry

### Export (complete)

- DOCX with publisher templates (template cloning, SQLite registry)
- PDF via DOCX-to-PDF or HTML fallback (explicit warning on fallback)
- PPTX with professional template
- Markdown with Unicode chemical subscripts

### Writing Pipeline CLI Commands

| Command | Status | Description |
|---------|--------|-------------|
| `wikify ingest <path>` | Working | Ingest PDFs/DOCX/PPTX (--parallel, --workers) |
| `wikify refresh` | Working | Recompute all batch signals + regenerate vault |
| `wikify stats` | Working | Show paper/chunk/figure counts |
| `wikify graph` | Working | Show PageRank, centrality, hub/bridge/frontier |
| `wikify generate "prompt"` | Working | Generate paper (--strategy, --journal) |
| `wikify evaluate <file>` | Working | PI-style + automated quality review |
| `wikify revise <file>` | Working | Targeted revision of weakest section |
| `wikify slides "topic"` | Working | Generate PPTX presentation |
| `wikify chat` | Working | Interactive literature Q&A |
| `wikify mcp` | Working | Launch MCP server for Claude Code |
| `wikify templates list` | Working | Show available DOCX/LaTeX templates |
| `wikify templates import` | Working | Import a .docx as reusable template |
| `wikify templates download` | Working | Auto-download publisher templates |

### MCP Server (complete)

- 25+ tools exposed; `.mcp.json` configured for Claude Code integration
- `ingest_paper` tool follows `ok/error` envelope contract

### What is Not Started (Writing Pipeline)

- **Falsifiable prediction step**: generation skill does not yet require a quantitative
  falsifiable prediction before writing. PI feedback identifies this as the gap between
  an 8.9 and 9.5 score.
- **LaTeX export**: not yet implemented.
- **Ollama support**: offline generation via Ollama is planned but not implemented.
- **Wiki-enhanced retrieval**: once the wiki reaches convergence, the writing pipeline will
  use concept articles as a structured knowledge layer to improve paper generation.

---

## Known Issues and Tech Debt

- **Sitemap-first vs epoch model**: `wiki init` still uses the sitemap pipeline (exploration
  agent + structured JSON plan). Both pipelines coexist: sitemap for user-directed topic focus,
  `wiki epoch` for autonomous concept-first discovery.
- **Topic coverage vocabulary**: 268 terms contain noise (filler phrases, DOI fragments).
  Needs cleaning before metric weight changes are meaningful.
- **Metric recalibration**: `topic_coverage` is overweighted; `bridge_vectors` is underweighted.
  A synthesis sentence density metric (sentences citing 2+ papers with a joint conclusion)
  would better capture what PI reviewers score highest.
- **Corpus gaps**: the current 206-paper ALD/memristor corpus is thin in the 2D material +
  ALD memristor space (MoS2/WS2/MoTe2 + ALD). Phase 4 devil's advocate consistently finds
  this territory but cannot fill it.
- **Citation-only PageRank**: current PageRank mixes citation and embedding similarity.
  A citation-only graph would give cleaner authority signals.

---

## Benchmarks (206-Paper Corpus)

| Metric | Value |
|--------|-------|
| Papers | 206 (ALD/memristor/neuromorphic, 1971-2026) |
| Chunks | 6,531 |
| Chunk embeddings | 6,531 (ChromaDB, ONNX quint8_avx2) |
| Summary embeddings | 206 |
| Figure/table refs | 2,730 |
| Citation cross-refs | 936 |
| Topics | 1,232 (268 vocabulary terms) |
| Tests | 647 |
| Ingestion time | ~10 min (206 papers, 10 workers) |
| Review generation | 3-5 min (enhanced hybrid strategy) |

### Best Strategy Results (PageIndex Hierarchical Benchmark)

| Strategy | Composite | Frontier | Arg Coherence | PI Score |
|----------|-----------|----------|---------------|----------|
| hier_hybrid_v3 | 0.621 | 1.000 | 0.393 | 8.9/10 |
| hier_hybrid_v1 | 0.599 | 1.000 | 0.375 | 9.1/10 |
| hier_gap_first | 0.611 | 1.000 | 0.482 | 8.8/10 |

Key insight: automated composite and PI scores diverge at the top. The composite cannot
detect cross-community synthesis or conclusion-level insights that PI reviewers reward most.

---

## Resume Instructions

1. Read `CLAUDE.md` for working conventions.
2. Read this file for current status.
3. Read `docs/architecture.md` for module layout and data flow.
4. Read `docs/design/wiki-wikipedia-model.md` for the epoch model design spec.
5. Read `docs/design/wiki-implementation-plan.md` for what is implemented and what needs building.
6. Code: `src/wikify/`; wiki output: `data/wiki/`; vault output: `data/vault/`.
7. MCP: restart Claude Code to load `.mcp.json`, then use MCP tools.
