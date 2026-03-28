# ScholarForge — Project Status

## Implementation Phases

### Phase 1 — Foundation (CURRENT)
- [x] UV project created at `C:\Users\fgril\OneDrive\Documents\scholarforge\`
- [x] pyproject.toml with hatchling build
- [x] Add dependencies to pyproject.toml (chromadb instead of lancedb — no Windows wheels)
- [x] Python upgraded to 3.12 (uv python pin 3.12)
- [x] All dependencies installed via `uv sync`
- [x] Create full module scaffold (all `__init__.py` files + directories)
- [x] Implement `store/models.py` — Paper, Chunk, Figure, Citation + graph enums + PaperPlan
- [x] Implement `store/db.py` — SQLite engine + session
- [x] Implement `config.py` — pydantic-settings with all paths/defaults
- [x] Implement `cli.py` — Typer CLI with `ingest` and `stats` commands
- [x] Implement `ingest/pdf.py` — pymupdf4llm full pipeline
- [x] Implement `ingest/registry.py` — file extension dispatcher
- [x] Implement `extract/chunker.py` — section-aware semantic chunking
- [x] Implement `extract/metadata.py` — title/authors/abstract/DOI extraction
- [x] Implement `extract/figures.py` — content-addressed figure extraction
- [x] VSCode workspace file created
- [x] `.gitignore` configured
- [x] Git repo initialized, all files staged
- [ ] **NEXT: Run `gh auth login` (user email: fabio.grillo89@gmail.com)**
- [ ] **NEXT: Create GitHub repo and push initial commit**
- [ ] Test: ingest 5 papers, verify chunks + metadata in SQLite

### Phase 2 — Obsidian Vault + Graph
- [ ] `vault/writer.py` — generate paper notes from extracted data
- [ ] `vault/templates.py` — note templates (paper, concept, author, method, topic)
- [ ] `vault/linker.py` — incremental link detection + creation
- [ ] `vault/sync.py` — vault ↔ SQLite consistency
- [ ] `store/vectors.py` with ChromaDB
- [ ] `graph/builder.py` — build networkx graph from vault links
- [ ] `graph/serialize.py` and `graph/traverse.py`
- [ ] Evaluate LiteParse vs pymupdf4llm on 10 sample papers (see `docs/parser-evaluation.md`)
- [ ] Test: ingest 20 papers, verify vault notes + graph connectivity

### Phase 3 — Generation
- [ ] `llm/client.py` with litellm + caching
- [ ] `generate/planner.py` — TOC generation
- [ ] `generate/context.py` — token budget packing
- [ ] `generate/writer.py` — section generation
- [ ] Jinja2 prompt templates for lit review
- [ ] Test: generate 3-section lit review from 20 papers

### Phase 4 — Export + Polish
- [ ] `export/docx_export.py` and `export/latex_export.py`
- [ ] `ingest/zotero.py` and `export/bibliography.py`
- [ ] `generate/figures_gen.py`
- [ ] CLI (`cli.py`)
- [ ] Scale test: full 200-paper lit review end-to-end

## Setup Progress

- **UV project**: Created
- **Python**: 3.12.11 via UV (upgraded from 3.10)
- **Dependencies**: All installed via `uv sync`
- **Module scaffold**: All directories + Phase 1 code
- **Git repo**: Initialized, first commit on master
- **GitHub repo**: NOT YET CREATED — `gh auth login` needed first
- **VSCode workspace**: `scholarforge.code-workspace`

## User Info

- **Name**: Fabio Grillo
- **GitHub email**: fabio.grillo89@gmail.com
- **Work email**: f.grillo@altastechnologies.com
- **Platform**: Windows 11, bash shell
- **Python**: 3.12.11 via UV
- **Tools**: UV 0.7.13, git configured, gh CLI v2.89.0 installed (needs `gh auth login`)

## Resume Instructions

When resuming this project:
1. Read `CLAUDE.md` for working conventions
2. Read `docs/architecture.md` for project architecture
3. Read this file for current status
4. The immediate next step is: user runs `gh auth login` → then create GitHub repo + initial commit
5. After that: test the ingestion pipeline with sample PDFs
6. All Phase 1 code is written and ready — see files in `src/scholarforge/`
7. The `vectors.py` module references should use ChromaDB (not LanceDB)
