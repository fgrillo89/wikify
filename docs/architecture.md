# ScholarForge — Architecture

## What is ScholarForge?

A local-first Python pipeline that turns a folder of academic PDFs into a living
Obsidian knowledge graph, then uses that graph to write papers, reviews, and
presentations via MCP-connected LLM agents.

## Design Principles

1. **Vault-first**: Obsidian vault is the primary output. SQLite is a supporting index.
2. **MCP-native**: LLM agents interact via MCP tools — no hardcoded generation scripts.
3. **Incremental + async**: Adding one paper is O(1); corpus signals refresh in background.
4. **Graph-guided retrieval**: PageRank/centrality guide the LLM to start at hub papers.
5. **Local-first**: All parsing, embedding, and graph computation done locally.

## Module Layout

```
src/scholarforge/
├── cli.py                          # Typer CLI + template subcommands
├── config.py                       # pydantic-settings (.env support)
├── mcp_server.py                   # FastMCP server (9 tools)
│
├── ingest/                         # Document ingestion (no LLM)
│   ├── pdf.py                      # pymupdf4llm + OCR fallback
│   ├── docx.py                     # python-docx → markdown
│   ├── pptx.py                     # python-pptx → markdown
│   └── registry.py                 # Dispatcher + batch orchestration
│
├── extract/                        # Structured extraction (no LLM)
│   ├── chunker.py                  # Section-aware chunking (tiktoken)
│   ├── metadata.py                 # Title, authors, DOI, year
│   ├── figures.py                  # Binary figure extraction
│   ├── figure_refs.py              # Caption-first figure/table refs
│   ├── citations.py                # Bibliography extraction
│   ├── cite_match.py               # Fuzzy citation matching
│   └── section_classifier.py       # Heading → canonical type mapping
│
├── store/                          # SQLite + ChromaDB
│   ├── models.py                   # Paper, Chunk, Citation, FigureRef,
│   │                               #   PaperTopic, JournalTemplate, PaperPlan
│   ├── db.py                       # Engine + session management
│   └── embeddings.py               # ChromaDB embeddings + k-NN
│
├── vault/                          # Obsidian vault (no LLM)
│   ├── writer.py                   # Paper/author note generation + graph config
│   ├── linker.py                   # Topic extraction + topic hub notes
│   ├── templates.py                # Note templates (paper, author, topic)
│   └── coupler.py                  # Bibliographic coupling (threshold ≥ 3)
│
├── graph/                          # NetworkX graph analysis
│   └── metrics.py                  # PageRank, centrality, hub/bridge/frontier
│
├── retrieve/                       # Context assembly for generation
│   ├── context.py                  # RetrievedContext, SectionContext
│   └── strategies/                 # 5 retrieval strategies
│       ├── base.py                 # RetrievalStrategy ABC + StrategyConfig
│       ├── flat.py                 # Top-N hub deep-read (default)
│       ├── hub_spoke.py            # Parallel subagent hub traversal
│       ├── query_driven.py         # Per-section ChromaDB retrieval
│       ├── snowball.py             # BFS from top PageRank paper
│       └── topic_cluster.py        # Group by topic, deep-read reps
│
├── generate/                       # Content generation (LLM)
│   ├── planner.py                  # Structured outline from prompt
│   ├── writer.py                   # Section-by-section generation
│   ├── persona.py                  # System prompt: style + field + type
│   ├── references.py               # [REF:...] → [N] resolver
│   ├── chat.py                     # Interactive RAG Q&A
│   ├── figures.py                  # Figure placeholder extraction
│   ├── field_guide.py              # Field detection + guide loading
│   └── artifact_types/             # Document type definitions
│       └── registry.py             # ArtifactType + 7 types
│
├── export/                         # Output formatting
│   ├── docx_export.py              # DOCX with template cloning
│   ├── pdf_export.py               # HTML→PDF (xhtml2pdf)
│   ├── pptx_export.py              # PPTX with professional template
│   ├── chemistry.py                # Chemical formula detection + subscripts
│   ├── journal_profile.py          # JournalProfile model + loader
│   ├── journals/                   # JSON profiles (AFM, Nature, ACS, IEEE, arXiv)
│   └── templates/                  # Template registry + DOCX files
│       ├── registry.py             # SQLite-backed template management
│       └── docx/                   # Downloaded publisher .docx templates
│
├── zotero/                         # Reference management
│   ├── bibtex_builder.py           # Paper → BibTeX entry
│   ├── bibtex_library.py           # Corpus-wide library.bib maintenance
│   └── client.py                   # Zotero API client
│
└── llm/                            # LLM interface
    └── client.py                   # litellm + diskcache caching
```

## Writing Pipeline

When an agent writes a paper, the LLM receives layered instructions:

```
1. Base style guide (680 words)     ← docs/logic/academic_writing_style.md
2. Artifact type rules              ← docs/logic/artifact_types/{type}.md
3. Field-specific guide             ← docs/logic/fields/{field}.md (auto-detected)
4. Figure instructions              ← per-section, body sections only
5. Journal profile constraints      ← export/journals/{journal}.json
```

## Retrieval Strategies

| Strategy | LLM calls | Description |
|----------|-----------|-------------|
| `flat` (default) | 0 | Top-N hub deep-read, rest shallow |
| `hub-spoke` | 3-4 | Parallel subagents per hub, synthesize |
| `topic-cluster` | 0 | Group by topic, deep-read per cluster |
| `query-driven` | 0 | Per-section ChromaDB retrieval |
| `snowball` | 0 | BFS from top PageRank paper |

## DOCX Template System

Templates are tracked in SQLite (`JournalTemplate` table). Three sources:
1. **Publisher downloads**: `scholarforge templates download wiley_afm`
2. **User papers**: `scholarforge templates import my_paper.docx`
3. **Built-in fallback**: Programmatic styling from journal profile

The exporter clones paragraph exemplars from the template XML, preserving
exact spacing, fonts, headers, footers, and logos.

## Data Layout

```
data/
├── papers.db               # SQLite (papers, chunks, citations, templates)
├── chromadb/               # Embedding vectors
├── library.bib             # Auto-generated BibTeX (updated on ingest)
├── cache/                  # LLM response cache
├── corpus_vocabulary.json  # Topic vocabulary
├── output/                 # Generated papers (md, docx, pdf)
├── downloads/              # Downloaded publisher templates
└── vault/                  # Obsidian vault
    ├── papers/             # Paper notes (blue in graph)
    ├── authors/            # Author notes (green)
    ├── topics/             # Topic hubs (orange)
    └── Dashboard.md        # Entry point
```

## Key Dependencies

| Category | Libraries |
|---|---|
| PDF | pymupdf, pymupdf4llm, rapidocr-onnxruntime |
| Office | python-docx, python-pptx |
| Storage | sqlmodel (SQLite), chromadb, sentence-transformers |
| LLM | litellm, tiktoken, diskcache |
| Graph | networkx |
| References | bibtexparser |
| Export | jinja2, xhtml2pdf, docx2pdf |
| Scraping | scrapling, patchright (template downloads) |
| CLI | typer, pydantic-settings, rich |
