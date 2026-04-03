# ScholarForge -- Architecture

## What is ScholarForge?

A local-first Python pipeline that turns academic PDFs and other documents into a structured
knowledge base, then writes reviews, papers, and presentations from that knowledge, and
optionally builds a self-correcting Wikipedia over the corpus. Model-agnostic (Claude, GPT-4,
DeepSeek, Ollama via litellm).

---

## High-Level System Diagram

```
Raw files (PDF, DOCX, PPTX, Markdown, HTML)
        |
        v
  INGEST PIPELINE (no LLM)
  ingest/service.py  ->  extract/  ->  store/ (SQLite + ChromaDB)
        |
        |-- BibTeX: data/library.bib
        |-- Vault:  data/vault/  (Obsidian notes)
        |-- Cache:  data/cache/precomputed/
        |
        v
  ENRICHED INDEX (data/papers.db + data/chromadb/)
        |
        +---------------------------+---------------------------+
        |                           |                           |
        v                           v                           v
  WRITING PIPELINE            WIKI PIPELINE              MCP SERVER
  (Pipeline A)                (Pipeline B)               (agent/tools.py)
                                                         25+ tools exposed
  generate/ + agent/          wiki/epoch.py [PLANNED]    to Claude Code
        |                     wiki/concepts.py [PLANNED]
        v                     wiki/concept_graph.py [PLANNED]
  data/output/                wiki/article.py [PLANNED]
  (.md, .docx, .pdf)                |
                              wiki/persona.py [done]
                              wiki/mapreduce.py [done]
                              wiki/maintenance.py [done]
                              wiki/builder.py [done]
                              wiki/linker.py [done]
                              wiki/sitemap.py [done, secondary]
                                    |
                                    v
                              data/wiki/  (concept articles, indexes)
```

---

## Module Map

```
src/wikify/
├── agent/                    # LLM agent loop, tools, workflows
│   ├── core.py               # ScholarForgeAgent, tool compaction, structured tool errors
│   ├── tools.py              # 25+ KB tools (read, search, gaps, citations, vibes)
│   ├── defaults.py           # Tool sets + prompt builders (explorer, writer)
│   ├── workflows.py          # generate_paper, explore_corpus, export_paper
│   ├── scripted.py           # Scripted pipeline (Python explore + LLM write)
│   ├── fast_generate.py      # One-shot pipeline (pre-compute + single LLM call)
│   ├── research_notes.py     # ResearchNotes + SourceSummary (explorer->writer handoff)
│   ├── run_context.py        # Run-scoped state (summaries, reading log, concept graph)
│   ├── concept_graph.py      # ConceptGraph (concept->paper edges, per-run, not persisted)
│   ├── reading_log.py        # File-backed reading trace for the active run
│   └── tool_schema.py        # fn -> litellm tool schema introspection
│
├── evaluate/                 # Quality metrics + exploration strategies (pure computation)
│   ├── quality.py            # 10-component composite + comprehensive_quality_report()
│   ├── coverage.py           # Semantic coverage, paper vibes
│   ├── strategies.py         # greedy_submodular, max_distance, spectral, hub_bfs
│   ├── frontier.py           # frontier_exploration_order (4-phase reading order)
│   └── pi_review.py          # LLM-as-PI evaluation
│
├── store/                    # SQLite + ChromaDB + pre-compute cache
│   ├── models.py             # All SQLite models (see Data Model section below)
│   ├── db.py                 # Engine + session management + auto-migration
│   ├── embeddings.py         # EmbeddingStore, paper vibes, science vibes
│   └── precompute.py         # Ingest-time cache (KMeans, gaps, links, vibes)
│
├── ingest/                   # File ingestion (no LLM)
│   ├── service.py            # PUBLIC BOUNDARY: ingest_file(), ingest_directory()
│   ├── corpus_refresh.py     # PUBLIC BOUNDARY: post-ingest refresh (topics, vault, BibTeX)
│   └── registry.py           # Legacy shim -- delegates to service.py
│
├── extract/                  # Parsing support (chunking, metadata, citations, figures)
├── graph/                    # NetworkX: citation-only PageRank, betweenness, centrality
├── generate/                 # Writing pipeline: planner, writer, verifier, references
├── export/                   # Output formatting: DOCX, PDF, PPTX, chemistry subscripts
├── vault/                    # Obsidian vault generation (enriched layer view)
├── zotero/                   # BibTeX library generation
├── llm/                      # litellm client + hooks
├── prompts/                  # Runtime prompt files (NOT documentation)
│   ├── style_guide.md        # Base academic writing style
│   ├── artifact_types/       # Per-document-type rules
│   └── fields/               # Per-field writing guides
│
└── wiki/                     # Wikipedia pipeline building blocks
    ├── persona.py            # Domain persona: generate_domain_persona(), get_or_create_persona()
    ├── mapreduce.py          # map_chunks_to_topic(), reduce_to_article(), record_coverage()
    ├── maintenance.py        # detect_contradiction(), additive_update(), revisionary_update(),
    │                         # structural_audit() -> StructuralReport
    ├── builder.py            # write_article(), read_article_frontmatter(), slugify(),
    │                         # generate_theme_index(), generate_domain_index(),
    │                         # generate_library_catalog(), append_unanswered_question()
    ├── linker.py             # cross_link_articles(), ensure_parent_backlinks()
    ├── sitemap.py            # SitemapEntry, WikiSitemap, generate_sitemap()
    │                         # (secondary role: optional user-directed topic focus)
    ├── agent.py              # build_wiki_from_sitemap(), build_article_from_entry(),
    │                         # build_wiki_article()
    │
    │   -- NOT YET IMPLEMENTED --
    ├── concepts.py           # [PLANNED] ConceptRecord, ConceptRelation, EpochLog + haiku discovery
    ├── concept_graph.py      # [PLANNED] co-occurrence graph, importance scoring
    ├── article.py            # [PLANNED] Wikipedia-format article writer (concept-aware)
    └── epoch.py              # [PLANNED] Epoch orchestrator (Passes 1-5, convergence)
```

---

## Data Flow: Pipeline A (Writing)

```
User: wikify generate "review on ALD memristors"
          |
          v
   agent/workflows.py  (selects route based on --strategy)
          |
    +-----------+-----------+-----------+-----------+
    |           |           |           |           |
  Skill      Hier-      Scripted    Two-Agent     Fast
  Route      archical   Route       Route        One-Shot
  (v1)       (v2)
    |           |           |           |           |
    |           |      Python        Explorer    Pre-compute
    |       4-level    explores,     LLM ->      10s, no LLM
    |      progressive  LLM just    Notes ->
    |       disclosure  writes      Writer LLM
    |
    +-----------+-----------+-----------+-----------+
          |
          v
   ResearchNotes  (canonical explorer->writer handoff)
   agent/research_notes.py
          |
          v
   generate/  (writer + planner + verifier + references)
          |
          v
   export/  (.md + .docx + .pdf + .pptx)
          |
          v
   data/output/
```

Progressive disclosure levels (all routes respect this):
1. `get_paper` (~200 chars) -- overview
2. `read_paper_digest` (~1.5KB) -- TOC + section summaries
3. `read_section` (~5KB) -- full text of one section
4. `deep_read` (~70KB) -- full paper (used sparingly)

---

## Data Flow: Pipeline B (Wikipedia/Epoch)

The epoch model is the authoritative design (see `docs/design/wiki-wikipedia-model.md`).
Four new modules must be built. The existing building-block modules (`persona.py`,
`mapreduce.py`, `maintenance.py`, `builder.py`, `linker.py`) are reused.

```
User: wikify wiki epoch
          |
          v
   wiki/epoch.py  [PLANNED]
          |
    +-----------+-----------+-----------+-----------+-----------+
    |           |           |           |           |           |
  Pass 1    Pass 2      Pass 3      Pass 4      Pass 5
  Discovery  Graph       Article     Cross-Ref   Index
  (haiku,   Construc-   Writing     (local)     Rebuild
  parallel)  tion        (sonnet,               (local)
             (local)     parallel)
    |           |           |           |           |
    v           v           v           v           v
  concepts.py concept_   article.py  linker.py  builder.py
  [PLANNED]  graph.py   [PLANNED]   (existing) (existing)
             [PLANNED]
    |
    v
  ConceptRecord table (SQLite)   <- new, in concepts.py [PLANNED]
  ConceptRelation table (SQLite) <- new, in concepts.py [PLANNED]
  EpochLog table (SQLite)        <- new, in concepts.py [PLANNED]
          |
          v
   data/wiki/  (one .md per concept, domain indexes, _index.md)
```

The sitemap pipeline (`wiki init`) remains available as an optional alternative
for user-directed topic focus. It is NOT the primary epoch-driven pipeline.

---

## Data Model

All SQLite models are in `src/wikify/store/models.py`.

### Core corpus tables

| Table | Key fields | Purpose |
|-------|-----------|---------|
| `Paper` | `id` (SHA256), `origin`, `doc_type`, `title`, `year`, `doi` | One row per ingested file |
| `Chunk` | `id` (UUID), `paper_id`, `section_path`, `section_type`, `content` | Section-aware text chunks |
| `Citation` | `paper_id`, `cited_paper_id`, `raw_text` | Citation cross-references |
| `FigureRef` | `paper_id`, `figure_key`, `caption_text` | Figure references (caption-first) |
| `PaperTopic` | `paper_id`, `topic` | Topic tags extracted at ingest |

`Paper.origin` is the isolation boundary:
- `"corpus"` -- ingested documents, used in all metrics and retrieval
- `"generated"` -- writing pipeline outputs, excluded from corpus metrics

### Project/output tables

| Table | Purpose |
|-------|---------|
| `Project` | Research project (groups papers + outputs) |
| `ProjectPaper` | Many-to-many: which papers belong to a project |
| `GeneratedOutput` | Tracks each writing run (strategy, cost, coverage, file paths) |
| `JournalTemplate` | DOCX/LaTeX templates (path, publisher, source URL) |

### Wiki tables

| Table | Key fields | Purpose |
|-------|-----------|---------|
| `WikiArticle` | `id` (slug), `status`, `domain`, `needs_update` | State machine for wiki articles |
| `DomainPersona` | `domain` (PK), `persona_text` | Cached expert persona per domain |
| `SourceCoverage` | `source_id`, `article_slug`, `extraction` | Which article each source contributed to |

### Planned wiki tables (epoch model)

| Table | Purpose |
|-------|---------|
| `ConceptRecord` | One row per discovered concept (name, type, importance, article status) |
| `ConceptRelation` | Directed edges between concepts (IS-A, ENABLES, CONTRASTS-WITH, etc.) |
| `EpochLog` | Per-epoch run log (concepts discovered, stubs upgraded, convergence flag) |

### ChromaDB collections

| Collection | Content | Used by |
|------------|---------|---------|
| `summaries` | Per-paper summary embeddings (corpus only) | Retrieval, vibe computation |
| `chunks` | Per-chunk embeddings (corpus only) | Semantic search, coverage metrics |
| `section_summaries` | Per-section summary embeddings | `read_section` progressive disclosure |

---

## File Layout

```
data/
├── papers.db                 # SQLite (all models above)
├── chromadb/                 # Embedding vectors (3 collections)
├── cache/precomputed/        # Ingest-time cache (vibes, KMeans, gaps, links)
│   ├── *.pkl                 # Per-batch cached signals
│   └── ...
├── library.bib               # Auto-generated BibTeX (regenerated on every ingest)
├── output/                   # Generated papers
│   ├── *.md                  # Markdown output
│   ├── *.docx                # DOCX output
│   ├── *.pdf                 # PDF output
│   └── reading_log_*.json    # Per-run reading trace
├── vault/                    # Obsidian vault (gitignored)
│   ├── Papers/               # One note per paper
│   ├── Authors/              # Author nodes
│   ├── Topics/               # Topic hub notes
│   └── Dashboard.md
└── wiki/                     # Curated wiki (gitignored)
    ├── _index.md             # Library catalog
    ├── _epoch.json           # [PLANNED] Epoch counter + convergence metrics
    ├── _unanswered.jsonl     # Open questions from wiki query escalation
    ├── _audit.md             # Structural audit report (wiki audit output)
    ├── _health.md            # Health check report (wiki health output)
    ├── domains/
    │   └── {domain}/
    │       ├── _index.md     # Domain master index
    │       ├── _index_{theme_slug}.md
    │       ├── concepts/     # One .md per concept article
    │       └── themes/       # Theme articles
    └── syntheses/            # Cross-domain synthesis articles
```

---

## Pre-Compute Cache

Built at ingest time by `store/precompute.py` and `ingest/corpus_refresh.py`. All load in <0.1s.

| Artifact | What | Where |
|----------|------|-------|
| Paper vibe vectors | Token-weighted chunk centroids | ChromaDB / `data/cache/precomputed/` |
| Science vibes | Centroids from results/discussion/conclusion only | `data/cache/precomputed/` |
| KMeans centroids | 12 clusters on all chunk embeddings | `data/cache/precomputed/` |
| Graph metrics | Citation-only PageRank, betweenness, degree | SQLite / cache |
| Topic embeddings | Normalized topic name vectors | `data/cache/precomputed/` |
| Boilerplate IDs | Chunks appearing in 5+ papers (k-NN detected) | `data/cache/precomputed/` |
| Divergent gaps | Coupled-but-divergent paper pairs | `data/cache/precomputed/` |
| Concept links | Section-filtered, IDF-labeled paper connections | `data/cache/precomputed/` |
| Section summaries | Extractive (first 1-2 sentences per section) | `Paper.section_summaries` (SQLite JSON) |

---

## Generation Routes

```
┌──────────────────────────────────────────────────────────────────┐
│              wikify generate                                │
├──────────┬──────────┬──────────┬───────────┬────────────────────┤
│  Skill   │ Hier-    │ Scripted │ Two-Agent │  Fast (exp.)       │
│  Route   │ archical │  Route   │  Route    │  One-Shot          │
│  (v1)    │ (v2)     │          │           │                    │
│          │          │          │           │                    │
│ LLM runs │ LLM uses │ Python   │ Explorer  │ Pre-compute all    │
│ the whole│ 4-level  │ explores,│ LLM ->    │ context (10s),     │
│ loop via │ progres- │ LLM just │ Notes ->  │ single LLM call    │
│ tool_use │ sive     │ writes   │ Writer    │ (~5 min)           │
│ deep+dig │ disclosure│          │ LLM      │                    │
│          │          │          │           │                    │
│ 25 min   │ ~10 min  │ 4 min    │ 8 min     │ 6 min              │
│ 133K tok │ ~80K tok │ 6K tok   │ 70K tok   │ 58K tok            │
└──────────┴──────────┴──────────┴───────────┴────────────────────┘
              All share: tools, export, quality metrics
```

---

## Run-Scoped State

`RunContext` (in `agent/run_context.py`) is the canonical mutable state for one run:
- Reading log (which papers read, with reasons)
- Paper summaries (distilled findings from `record_paper_summary`)
- Per-run concept graph (concept -> paper edges, not persisted to DB)
- Phase-level usage telemetry (token counts per phase)
- Non-fatal run warnings

`ScholarForgeAgent`, `workflows.py`, and `scripted.py` all bind tool calls to the active
`RunContext`. This is important: compaction and summary reinjection operate on run-local
state, not process globals.

---

## Quality Metrics

### Automated Composite (10 components)

| Metric | Weight | What it measures |
|--------|--------|------------------|
| Prose quality | 0.20 | Citation clustering, synthesis depth, discourse quality |
| Frontier shift | 0.14 | Push toward sparse embedding regions |
| Gap detection | 0.14 | Embedding voids + gap-claim phrases |
| Arg. coherence | 0.12 | Consecutive chunk pairs preserved in review |
| Factual specificity | 0.12 | Numbers + formulas per 1k words |
| Semantic coverage | 0.10 | Corpus chunks covered by review |
| Bridge vectors | 0.10 | Chunks connecting dissimilar papers |
| Semantic residual | 0.10 | Synthesis vs summarization (SVD) |
| Topic coverage | 0.10 | PaperTopic vocabulary in review |
| Centroid alignment | 0.08 | Review center vs corpus center |

The automated composite is a proxy. PI-style review (`wikify evaluate --pi`) captures
research judgment and contribution framing that no automated metric measures reliably.

---

## External Dependencies

| Dependency | Role |
|------------|------|
| litellm | Model-agnostic LLM client (Claude, GPT-4, DeepSeek, Ollama) |
| ChromaDB | Vector store for embeddings (summaries, chunks, section summaries) |
| SQLite (via SQLModel) | Relational store for all structured data |
| ONNX Runtime | Local embedding model (no LLM calls for embeddings) |
| NetworkX | Citation graph analysis (PageRank, betweenness, centrality) |
| pymupdf4llm / fitz | PDF parsing |
| python-docx | DOCX generation and template manipulation |
| typer | CLI framework |

All LLM calls go through `llm/client.py:complete()`. No provider-specific annotations
in any module. Haiku is used for cheap map-phase calls; sonnet for writing; opus is
reserved and not used in any current pipeline by default.
