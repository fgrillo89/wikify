# Claude Code — Working Conventions

General rules for how Claude should operate in this project.

**What this project is**: Wikify turns any corpus (PDFs, notes, web articles, READMEs) into
a concept-first, self-correcting personal Wikipedia. It also contains a writing pipeline
(generate → evaluate → revise) for producing literature reviews and research papers.

**Key docs (read in order):**
1. `docs/project-status.md` — current state, what works, what is planned, known issues
2. `docs/architecture.md` — module layout, both pipelines, data model, file layout
3. `docs/design/wiki-wikipedia-model.md` — authoritative spec for the Wikipedia/epoch pipeline

**Runtime prompts** (loaded by code, NOT documentation):
- `src/wikify/prompts/style_guide.md` — base writing style
- `src/wikify/prompts/artifact_types/` — per-document-type rules
- `src/wikify/prompts/fields/` — per-field writing guides

**Design docs**: `docs/design/` — architecture decisions and design plans (reference only)

**Two pipelines — do not confuse them:**
- **Writing pipeline**: `wikify generate` → `wikify evaluate` → `wikify revise` (fully working)
- **Wikipedia pipeline**: `/wiki-epoch` → `/wiki-campaign` → `/wiki-ask` → `/wiki-maintain` (fully working)

**Skills** (`.claude/skills/`) — four wiki modes + two writing modes:

| Skill | Mode | When to use |
|-------|------|-------------|
| `/wiki-epoch` | Build | After ingest, grow the wiki encyclopedically |
| `/wiki-campaign` | Investigate | Thesis-driven research, opinionated epochs |
| `/wiki-ask` | Query | Answer questions, file answers back into wiki |
| `/wiki-maintain` | Maintain | Lint + auto-fix + self-enhance (find & fill gaps) |
| `/generate` | Write | Generate a paper from the corpus |
| `/ingest` | Ingest | Ingest PDFs into the knowledge base |

**The wiki cycle:** ingest → epoch → ask → maintain → (repeat). Each mode enriches the wiki. Campaigns are user-initiated research threads that layer on top.

## Architecture: Skills + Tools

**The LLM is the orchestrator.** Pipelines are skills, not Python scripts calling litellm.

- **Skills** describe what to do (markdown in `.claude/skills/`)
- **Tools** are Python functions for DB, graph, file I/O, embeddings (no LLM needed)
- **Agents** (haiku subagents) handle LLM-heavy batch work (extraction, article writing)
- **Python code** handles: DB operations, graph computation, file I/O, embedding search
- **The LLM** handles: extraction, synthesis, article writing, quality judgment, orchestration

This means no API key dependency for the primary workflow. The scripted path
(`epoch.py` via litellm) exists for automated/scheduled runs but is secondary.

## Agent Usage

- **Always prefer sub-agents** for parallelizable work — launch independent tasks simultaneously
- **Cost tier triaging** (model-agnostic):
  - **fast**: Bulk extraction, classification, yes/no checks (haiku, Codex mini, Gemini Flash)
  - **balanced**: Article writing, synthesis, code generation (sonnet, Codex, Gemini Pro)  
  - **deep**: Complex reasoning, structural audits, conflict resolution (opus, o3)
- Foreground agents only when results are needed before proceeding; background otherwise
- **Batch processing**: For LLM-heavy passes (extraction, article writing), split work into N batches and spawn N haiku agents in parallel

## Python Tooling

- **Package manager**: Always use `uv` (never pip/pip-tools)
- **Formatting/linting**: Use `ruff` for all Python files (`uv run ruff format .` and `uv run ruff check --fix .`)
- **Type checking**: Run `uv run ty` on all code after changes
- **Testing**: `uv run pytest`

## Code Quality

- Follow **SOLID principles** where applicable:
  - **S**ingle Responsibility — one reason to change per module/class
  - **O**pen/Closed — extend via new classes, not modifying existing ones
  - **L**iskov Substitution — subtypes must be substitutable for base types
  - **I**nterface Segregation — small, focused interfaces over fat ones
  - **D**ependency Inversion — depend on abstractions, not concretions

## Git Workflow

- **Commit and push regularly**: Commit and push after every meaningful progress — don't batch up changes. This includes code changes, doc updates, and bug fixes. Small frequent commits are better than large infrequent ones.

## Output Quality Review Criteria

When scoring or reviewing generated text (strategy comparisons, quality assessments, etc.), always evaluate these criteria alongside thematic organization, citation density, and style guide compliance:

| Criterion | What to measure |
|-----------|----------------|
| **Sentence simplicity** | Avg words per sentence; % of sentences > 30 words; are sentences parseable on first read? |
| **Concept density** | New concepts introduced per sentence (target: 1). Flag sentences that stack 2+ unfamiliar terms. |
| **Relative clause usage** | Count of which/that/who clauses; flag sentences with 2+ nested clauses. |
| **Subordinate clause frequency** | % of sentences opening with although/because/while/since (target: <20%). |
| **Abstract readability** | First sentence <15 words? One concept per sentence? Zero citations (unless foundational)? |
| **Em-dash violations** | Count of " -- ", " --- ", unicode em/en-dashes used as parenthetical separators (target: 0). |

## Corrections & Lessons Learned

When the user corrects a mistake or misinterpretation, **add an entry below** so the
same mistake is never repeated. Format: `- **Topic**: What went wrong → what to do instead.`

<!-- Add corrections below this line -->
- **Data libraries**: Always use polars over pandas. User strongly prefers polars.
- **Package installs**: Always use `uv add` (not `uv pip install`) to add dependencies. This keeps pyproject.toml in sync.
- **Commit messages**: Never include absolute paths or personal PC paths in commit messages. Use relative paths or project-relative references only.
- **Vault output location**: The Obsidian vault output goes under `data/vault/` (gitignored), NOT at project root. The vault Python *code* lives in `src/wikify/vault/` — don't confuse code with output.
- **No mock generation scripts**: Don't hardcode paper text in scripts. Use the MCP server + Claude Code agents to generate real papers from the knowledge base.
- **DOCX templates**: When using a publisher template, clone paragraph exemplars from the template XML. Never override template fonts/spacing programmatically — let the template's native styles handle formatting.
- **BibTeX is corpus-level**: `library.bib` is auto-generated on ingest/refresh at `data/library.bib`, not per-output.
- **Chemistry subscripts**: Apply Unicode subscripts (HfO₂) only to markdown output. DOCX gets raw text (HfO2) and the exporter renders native Word subscripts.
- **Unicode on Windows**: Avoid Unicode arrows/special chars in console print statements. Use ASCII alternatives.
- **No silent error swallowing**: NEVER use bare `except: pass` or `try/except` that hides failures. If something fails, log it or raise it. Silent swallowing is obfuscation by design. If ChromaDB fails, the user needs to know and fix it, not get silently degraded results.
- **Module-level instances are fine, mutable globals are not**: A module-level constant like `_db = DatabaseManager()` (assigned once, never reassigned) is acceptable — same pattern as `settings = Settings()`. The `global` keyword for mutation (`global _foo; _foo = ...`) is banned. Prefer dependency injection (pass the instance as a constructor/function argument) when the instance needs to be swapped in tests.
- **Performance-critical paths**: Consider whether hot paths (embedding, graph computation, chunk retrieval) should eventually be compiled (Rust via PyO3/maturin, or Cython). Python's GIL and startup cost are real bottlenecks for a responsive app.
- **Wikipedia pipeline is concept-first**: The wiki is built by discovering concepts from the corpus (haiku extraction), not from a pre-planned sitemap. `sitemap.py` is a secondary tool for user-directed focus, not the primary pipeline. Never make the sitemap the entry point for new wiki work.
- **Epoch model for convergence**: Wiki building is iterative. Each epoch runs 5 passes: discovery → graph → article writing → cross-reference → index rebuild. Convergence is tracked via a scalar loss L and per-concept information gradient. See `wiki-wikipedia-model.md` for formulas.
- **Model selection in epochs**: Use haiku for Pass 1 (extraction) always. Use haiku for Pass 3 (article writing) while L >= 0.3; switch to sonnet once L < 0.3. Reserve opus only for structural audit or complex synthesis.
- **Package name**: The Python package is `wikify` (renamed from `scholarforge`). CLI command is `wikify`. GitHub repo is `github.com/fgrillo89/wikify`. Project root directory is still `C:\dev\scholarforge`.
- **Skill-first architecture**: Pipelines are skills orchestrated by the LLM, not Python scripts calling litellm. The LLM spins up haiku agents for batch work and uses Python tools for DB/graph/file ops. The scripted path (litellm) is secondary, for automated/scheduled runs only. Never ask for API keys -- use subagents.
- **Extraction template evolves**: The extraction template (`data/wiki/_template.md`) is versioned per epoch. It grows via gap-driven refinement and shrinks via zero-yield pruning. An overfitting guard rejects corpus-specific sections.
