# Wikify -- Architecture

## What is Wikify?

A local-first Python pipeline with two distinct capabilities:

**A. Wikipedia Pipeline (primary focus):** Turns any corpus of PDFs, notes, and documents into
a concept-first, self-correcting personal Wikipedia via an epoch-driven discovery and
article-writing loop.

**B. Research Paper Writing:** Generates literature reviews, research papers, and presentations
from the same corpus via a generate -> evaluate -> revise pipeline. Will be enhanced later
by using the wiki as a structured knowledge layer.

Model-agnostic (Claude, GPT-4, DeepSeek, Ollama via litellm).

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
  PIPELINE A                  PIPELINE B                 MCP SERVER
  Research Paper Writing      Wikipedia / Epoch          agent/tools.py
                                                         15 MCP tools exposed
  generate/ + agent/          wiki/epoch.py              to Claude Code
        |                     wiki/concepts.py
        v                     wiki/concept_graph.py
  data/output/                wiki/article.py
  (.md, .docx, .pdf)          wiki/domains.py
                              wiki/dashboard.py
                                    |
                              wiki/persona.py
                              wiki/mapreduce.py
                              wiki/maintenance.py
                              wiki/builder.py
                              wiki/linker.py
                              wiki/sitemap.py (secondary)
                                    |
                                    v
                              data/wiki/  (concept articles, indexes)
                                    |
                    (future)        v
                    +-- feeds into Pipeline A for richer retrieval
```

---

## Module Map

```
src/wikify/
├── agent/                    # LLM agent loop, tools, workflows
│   ├── core.py               # WikifyAgent, tool compaction, structured tool errors
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
│   ├── media.py              # Unified image/table extraction pipeline (pymupdf4llm)
│   └── equations.py          # LaTeX/chemical/inline equation detection and extraction
│
├── graph/                    # NetworkX: citation-only PageRank, betweenness, centrality
├── generate/                 # Writing pipeline: planner, writer, verifier, references
├── export/                   # Output formatting: DOCX, PDF, PPTX, chemistry subscripts
├── vault/                    # Obsidian vault generation (enriched layer view)
├── zotero/                   # BibTeX library generation
├── llm/                      # litellm client + hooks
│   └── vision.py             # Haiku vision: send figures for structured description
│
├── prompts/                  # Runtime prompt files (NOT documentation)
│   ├── style_guide.md        # Base academic writing style
│   ├── artifact_types/       # Per-document-type rules
│   └── fields/               # Per-field writing guides
│
└── wiki/                     # Wikipedia pipeline (Pipeline B)
    ├── concepts.py           # ConceptRecord, ConceptRelation, EpochLog SQLite models
    │                         # + haiku discovery: discover_concepts, merge_concept_records
    ├── concept_graph.py      # Co-occurrence graph, PageRank importance, Louvain communities,
    │                         # relation classification: build_concept_graph, score_importance,
    │                         # classify_relations
    ├── article.py            # Wikipedia-format article writer (concept-aware):
    │                         # write_concept_article
    ├── epoch.py              # Epoch orchestrator: Passes 1-5, loss computation,
    │                         # convergence tracking, trigger hooks:
    │                         # run_epoch, run_until_convergence, check_convergence, compute_loss
    ├── dashboard.py          # FastAPI dashboard: /api/epochs, /api/concepts,
    │                         # /api/coverage, /api/gradient
    ├── domains.py            # DomainCluster discovery, domain membrane model,
    │                         # auto-domain routing: discover_domains, assign_concept_domain
    ├── routing.py            # Concept-to-domain routing, domain boundary enforcement
    ├── persona.py            # Domain persona: generate_domain_persona(),
    │                         # get_or_create_persona()
    ├── mapreduce.py          # map_chunks_to_topic(), reduce_to_article(),
    │                         # record_coverage()
    ├── maintenance.py        # detect_contradiction(), additive_update(),
    │                         # revisionary_update(), structural_audit() -> StructuralReport
    ├── builder.py            # write_article(), read_article_frontmatter(), slugify(),
    │                         # generate_theme_index(), generate_domain_index(),
    │                         # generate_library_catalog(), append_unanswered_question()
    ├── linker.py             # cross_link_articles(), ensure_parent_backlinks()
    ├── sitemap.py            # SitemapEntry, WikiSitemap, generate_sitemap()
    │                         # (secondary role: optional user-directed topic focus)
    ├── people.py             # Person discovery, name dedup, author cross-reference
    ├── figure_enrichment.py  # Batch Haiku vision enrichment for figures at scale
    ├── html.py               # Static HTML site generator (Wikipedia Vector skin, KaTeX)
    ├── templates/            # Jinja2 templates for HTML site (article, index, sidebar)
    └── agent.py              # build_wiki_from_sitemap(), build_article_from_entry(),
                              # build_wiki_article()
```

---

## Pipeline A: Research Paper Writing

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

## Pipeline B: Wikipedia / Epoch

The epoch model is the authoritative design. See `docs/design/wiki-wikipedia-model.md`.
Building-block modules (`persona.py`, `mapreduce.py`, `maintenance.py`, `builder.py`,
`linker.py`) are reused inside the epoch orchestrator.

```
User: wikify wiki epoch
          |
          v
   wiki/epoch.py
          |
    +----------+----------+----------+----------+----------+
    |          |          |          |          |          |
  Pass 1    Pass 2     Pass 3     Pass 4     Pass 5
  Discovery  Graph      Article    Cross-Ref   Index
  (haiku,   Construc-  Writing    (local)     Rebuild
  parallel)  tion       (haiku/            +  (local)
             (local)    sonnet,       Pass 5a:
                        parallel)  Obsidian
                                   dashboard
    |          |          |          |          |
    v          v          v          v          v
  concepts.  concept_  article.py linker.py  builder.py
  py         graph.py
    |
    v
  ConceptRecord table (SQLite)   -- in concepts.py
  ConceptRelation table (SQLite) -- in concepts.py
  EpochLog table (SQLite)        -- in concepts.py
  DomainCluster table (SQLite)   -- in domains.py
          |
          v
   data/wiki/  (one .md per concept, domain indexes, _index.md)
          |
          v
   wiki/html.py  ->  data/wiki/_site/  (static HTML, Wikipedia Vector skin)
```

Supporting modules:
- `wiki/people.py` -- person discovery, name dedup, author cross-ref (feeds Pass 1)
- `wiki/figure_enrichment.py` -- batch Haiku vision enrichment (feeds Pass 1/3)
- `extract/equations.py` -- equation detection (feeds Pass 1)
- `llm/vision.py` -- Haiku vision helper for figure descriptions

Model-selection schedule within Pass 3:
- While `L >= 0.3`: haiku for all article drafts
- Once `L < 0.3`: switch to sonnet for remaining articles

The sitemap pipeline (`wiki init`) remains available as an optional alternative for
user-directed topic focus. It is NOT the primary epoch-driven pipeline.

### Planned: Conceptual Nexus Model

The Adaptive Knowledge Engine plan (`docs/design/adaptive-knowledge-engine.md`) introduces
a **Conceptual Nexus Model** as Phase 6 of the Wikipedia pipeline's next evolution. It
unifies the three existing knowledge layers into a single queryable representation:

- **Concept graph** (`wiki/concept_graph.py`) -- NetworkX DiGraph of co-occurrence relations
- **Embedding layer** (ChromaDB `chunks` + concept definitions) -- semantic similarity
- **Article layer** (`data/wiki/`) -- human-readable Markdown articles per concept

The nexus model formalizes these as a sparse tensor `T[concept_i, concept_j, relation_k] =
evidence_strength`, where the `ConceptRelation` table IS the tensor's non-zero entries and
ChromaDB holds the vector projections. No new data store is added -- it is a computation
layer over existing tables that enables structured gap detection, analogy discovery, and
cluster coherence scoring.

Phases 1-5 of the Adaptive Knowledge Engine plan (yield feedback, UCB scoring, contradiction
exploration, hierarchical taxonomy, schema evolution) are prerequisites before Phase 6 is built.

### How Pipeline B Feeds Pipeline A (planned)

Once the wiki reaches convergence (L < threshold), Pipeline A retrieval will gain a
structured knowledge layer:

```
data/wiki/  (concept articles, domain indexes)
          |
          v  (future: wiki-aware retrieval tools)
   agent/tools.py  (search_concepts, get_concept_article, ...)
          |
          v
   ResearchNotes  (richer context for the writer)
          |
          v
   generate/  (better-cited, more synthesis-aware papers)
```

---

## Data Model

All SQLite models are in `src/wikify/store/models.py`.

### Core corpus tables

| Table | Key fields | Purpose |
|-------|-----------|---------|
| `Paper` | `id` (SHA256), `origin`, `doc_type`, `title`, `year`, `doi` | One row per ingested file |
| `Chunk` | `id` (UUID), `paper_id`, `section_path`, `section_type`, `content` | Section-aware text chunks |
| `Citation` | `paper_id`, `cited_paper_id`, `raw_text` | Citation cross-references |
| `Figure` | `id` (hash), `paper_id`, `media_type`, `caption`, `label`, `page_number`, `bbox`, `markdown_table`, `llm_description` | Extracted figures, tables, schemes with Haiku vision descriptions |
| `Equation` | `id` (hash), `paper_id`, `chunk_id`, `latex`, `equation_type`, `variables`, `concept_links` | Extracted LaTeX/chemical/inline equations |
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

### Wiki tables (building blocks)

| Table | Key fields | Purpose |
|-------|-----------|---------|
| `WikiArticle` | `id` (slug), `status`, `domain`, `needs_update` | State machine for wiki articles |
| `DomainPersona` | `domain` (PK), `persona_text` | Cached expert persona per domain |
| `SourceCoverage` | `source_id`, `article_slug`, `extraction` | Which article each source contributed to |

### Wiki tables (epoch model)

| Table | Key fields | Purpose |
|-------|-----------|---------|
| `ConceptRecord` | `name`, `type`, `importance`, `article_status`, `domains` | One row per discovered concept; `domains` is a JSON list of domain slugs the concept belongs to |
| `ConceptRelation` | `source`, `target`, `relation_type` | Directed edges (IS-A, ENABLES, CONTRASTS-WITH, etc.) |
| `EpochLog` | `epoch`, `loss_score`, `loss_delta`, `convergence_flag`, `model_tier` | Per-epoch run log; records haiku->sonnet transition epoch |
| `DomainCluster` | `slug`, `label`, `centroid_vector`, `concept_count` | Auto-discovered domain from corpus; basis of domain membrane model |
| `TopologySnapshot` | `epoch`, `node_count`, `edge_count`, `modularity`, `diameter` | Graph topology snapshot per epoch for convergence analysis |

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
│   ├── *.pkl
│   └── ...
├── library.bib               # Auto-generated BibTeX (regenerated on every ingest)
├── output/                   # Generated papers (Pipeline A)
│   ├── *.md
│   ├── *.docx
│   ├── *.pdf
│   └── reading_log_*.json    # Per-run reading trace
├── vault/                    # Obsidian vault (gitignored)
│   ├── Papers/               # One note per paper
│   ├── Authors/              # Author nodes
│   ├── Topics/               # Topic hub notes
│   └── Dashboard.md
└── wiki/                     # Curated wiki (gitignored; Pipeline B output)
    ├── _index.md             # Library catalog
    ├── _epoch.json           # Epoch counter + convergence metrics
    ├── _unanswered.jsonl     # Open questions from wiki query escalation
    ├── _audit.md             # Structural audit report (wiki audit output)
    ├── _health.md            # Health check report (wiki health output)
    ├── _site/                # Static HTML site (generated by `wikify wiki html`)
    ├── domains/
    │   └── {domain}/
    │       ├── _index.md     # Domain master index
    │       ├── _dashboard.md # Dataview dashboard (written by Pass 5, planned)
    │       ├── _index_{theme_slug}.md
    │       ├── concepts/     # One .md per concept article
    │       ├── people/       # Person biography articles
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
| Equations | LaTeX/chemical/inline equations per chunk | `Equation` table (SQLite) |

---

## Generation Routes (Pipeline A)

```
+------------------------------------------------------------------+
|                       wikify generate                            |
+----------+----------+----------+-----------+--------------------+
|  Skill   | Hier-    | Scripted | Two-Agent |  Fast (exp.)       |
|  Route   | archical |  Route   |  Route    |  One-Shot          |
|  (v1)    | (v2)     |          |           |                    |
|          |          |          |           |                    |
| LLM runs | LLM uses | Python   | Explorer  | Pre-compute all    |
| the whole| 4-level  | explores,| LLM ->    | context (10s),     |
| loop via | progres- | LLM just | Notes ->  | single LLM call    |
| tool_use | sive     | writes   | Writer    | (~5 min)           |
|          | disclosure|          | LLM      |                    |
|          |          |          |           |                    |
| 25 min   | ~10 min  | 4 min    | 8 min     | 6 min              |
| 133K tok | ~80K tok | 6K tok   | 70K tok   | 58K tok            |
+----------+----------+----------+-----------+--------------------+
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

`WikifyAgent`, `workflows.py`, and `scripted.py` all bind tool calls to the active
`RunContext`. This is important: compaction and summary reinjection operate on run-local
state, not process globals.

---

## Quality Metrics (Pipeline A)

### Automated Composite (10 components)

| Metric | Weight | What it measures |
|--------|--------|-----------------|
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
| FastAPI | Dashboard API server (wiki/dashboard.py) |
| Jinja2 | HTML template rendering (wiki/html.py) |
| pymdown-extensions | Math/chem markdown extensions for HTML site |
| typer | CLI framework |

All LLM calls go through `llm/client.py:complete()`. No provider-specific annotations
in any module. Haiku is used for cheap map-phase calls; sonnet for writing; opus is
reserved and not used in any current pipeline by default.
