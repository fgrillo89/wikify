# Prompt: Implement Discovery Engine Alignment

Give this to Claude in a new session. It is self-contained.

---

## Context

You are working on Wikify (github.com/fgrillo89/wikify), a Python project at
`C:\dev\scholarforge` (package name: `wikify`, CLI: `wikify`). Wikify turns a
corpus of research papers into a concept-first, self-correcting personal Wikipedia
through iterative epochs.

**Read these files first, in this order:**
1. `CLAUDE.md` — working conventions, tools, corrections
2. `docs/project-status.md` — what works, what doesn't
3. `docs/design/discovery-engine-alignment.md` — the authoritative spec for this work
4. `docs/design/adaptive-knowledge-engine.md` — the broader plan this extends
5. `src/wikify/wiki/concepts.py` — current extraction pipeline (you'll modify this heavily)
6. `src/wikify/wiki/epoch.py` — epoch orchestrator (you'll wire new phases into this)
7. `src/wikify/store/models.py` — all SQLite models (you'll add new ones)
8. `src/wikify/store/db.py` — DB setup and migrations (register new models here)
9. `src/wikify/wiki/mapreduce.py` — map-reduce article writing (context for how extraction feeds into articles)

## What to implement

Implement the 7 phases described in `docs/design/discovery-engine-alignment.md`.
The phases build on each other — implement in order.

### Phase 0: Extraction Template Infrastructure

Create a template system that replaces the hardcoded extraction prompt.

**Create `src/wikify/wiki/template.py`:**
- `load_template(wiki_dir) -> str` — reads `data/wiki/_template.md`, returns content
- `save_template(wiki_dir, content, epoch)` — writes template, keeps versioned backups in `data/wiki/_template_versions/`
- `build_extraction_prompt(template, chunk_content, prior_concepts) -> list[dict]` — builds the LLM messages from template + chunk
- `get_default_template() -> str` — returns the initial template (v0) as a string

**Create `data/wiki/_template.md`** (the default template v0):
```markdown
# Extraction Template v0

Extract the following from the text below. Return as JSON with these sections.

## concepts
Array of objects: {name, type, aliases, definition, evidence}
- type: one of technique | material | phenomenon | method | theory | dataset
- evidence: exact quote from the text (max 50 words) supporting this concept
- definition: max 25 words

## parameters  
Array of objects: {concept_name, parameter_name, value, unit, conditions, evidence}
- Only extract explicitly stated quantitative values with units
- conditions: experimental conditions (temperature, pressure, etc.)
- evidence: exact quote containing the value

## mechanisms
Array of objects: {description, causes, effects, evidence}
- Causal or process mechanisms described in the text
- evidence: exact quote

## relationships
Array of objects: {source_concept, target_concept, relation_type, evidence}
- relation_type: IS-A | PART-OF | USED-IN | ENABLES | CONTRASTS-WITH
- Only extract relationships explicitly stated in the text

## gaps
Array of objects: {description, suggested_type}
- Knowledge in this text that does NOT fit the categories above
- suggested_type: what new category would capture it
```

**Modify `src/wikify/wiki/concepts.py`:**
- Refactor `_extract_from_chunk()` to use `build_extraction_prompt()` instead of the hardcoded prompt
- The function should now return a richer result dict (not just ConceptRecord list) containing concepts, parameters, mechanisms, relationships, and gaps
- Create a wrapper that extracts ConceptRecords from the rich result (backward compat)

### Phase 1: Source Evidence Linkage

Every extracted concept must have a source quote.

**Add to `store/models.py`:**
```python
class ConceptEvidence(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    concept_id: str = Field(index=True)    # FK -> ConceptRecord.id
    paper_id: str = Field(index=True)      # FK -> Paper.id  
    chunk_id: str = ""                      # FK -> Chunk.id
    evidence_quote: str = ""                # exact text from source
    epoch_extracted: int = 0
    verified: bool = False                  # True if quote found in source text
```

**Add verification in concepts.py:**
After extraction, grep the source chunk content for each evidence quote. Set `verified=True` if found (fuzzy match, not exact — allow minor whitespace/punctuation differences). Log unverified extractions as potential hallucinations.

Register `ConceptEvidence` in `store/db.py`.

### Phase 2: Meta-Probes and Gap Reporting

The `## gaps` section in the template serves as the meta-probe.

**Add to `store/models.py`:**
```python
class ExtractionGap(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    description: str = ""           # what the LLM couldn't classify
    suggested_type: str = ""        # proposed new type
    paper_id: str = ""
    chunk_id: str = ""
    epoch: int = 0
```

**In concepts.py:** After parsing the extraction result, store any items from the `gaps` section into ExtractionGap. 

**In dashboard.py:** Add `GET /api/gaps` endpoint returning gap clusters.

Register `ExtractionGap` in `store/db.py`.

### Phase 3: Self-Consistent Template Refinement

The template evolves based on aggregated gap feedback.

**Add to `src/wikify/wiki/template.py`:**
```python
def refine_template(wiki_dir, epoch, model=None) -> tuple[str, float]:
    """Revise the extraction template based on accumulated gaps.
    
    1. Load all ExtractionGap rows from last 3 epochs
    2. Cluster by embedding similarity
    3. For clusters with 5+ gaps: generate a proposed template addition
    4. Apply accepted additions to the template
    5. Save new template version
    
    Returns (new_template_content, template_delta)
    """
```

The refinement uses a haiku call per gap cluster to propose a new template section. The proposed section is tested on 5 sample chunks — if it extracts meaningful content from at least 3, it's accepted.

**Wire into epoch.py:** Call `refine_template()` at the end of Pass 5, before computing loss. Store `template_delta` in EpochLog (add field).

**Track convergence:** Template has converged when `template_delta < 0.01` for 3 epochs.

### Phase 4: Two-Pass Extraction

Publication-level overview first, then targeted chunk deepening.

**Add to `src/wikify/wiki/concepts.py`:**
```python
def extract_from_publication(paper_id, template, epoch, model=HAIKU_MODEL) -> dict:
    """Pass 1a: Extract from abstract + section summaries against full template.
    
    Gives a broad view of what the publication covers without reading every chunk.
    Returns the rich extraction dict (concepts, parameters, mechanisms, relationships, gaps).
    """
```

**Modify `discover_concepts()`:**
1. For each paper, run `extract_from_publication()` first (Pass 1a)
2. From the publication-level concepts, identify which sections need chunk-level deepening
3. Only run chunk-level extraction (existing `extract_concepts_from_source()`) on relevant chunks (Pass 1b)

This should reduce haiku calls by ~50-60% since most papers' concepts are discoverable from abstracts + summaries.

### Phase 5: Quantitative Parameter Extraction

Extract structured parameters, not just concept names.

**Add to `store/models.py`:**
```python
class ParameterExtraction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    concept_id: str = Field(index=True)
    paper_id: str = Field(index=True)
    parameter_name: str = ""        # e.g. "growth rate"
    value: str = ""                 # e.g. "1.0"  
    unit: str = ""                  # e.g. "A/cycle"
    conditions: str = ""            # e.g. "substrate temperature 250C"
    evidence: str = ""              # source quote
    epoch_extracted: int = 0
```

**In concepts.py:** After parsing the extraction result, store `parameters` section items into ParameterExtraction.

**In builder.py:** When writing wiki articles, auto-generate a "## Parameters" table from ParameterExtraction rows for that concept:
```markdown
## Parameters

| Parameter | Value | Unit | Conditions | Source |
|-----------|-------|------|------------|--------|
| Growth rate | 1.0 | A/cycle | 250C substrate | Yang 2011 |
```

Register `ParameterExtraction` in `store/db.py`.

### Phase 6: Structured Concept Vectors

Richer embeddings that encode structure.

**Modify concept embedding in concepts.py or a new helper:**
Instead of embedding just the concept name, embed a structured string:
```
"Atomic Layer Deposition | type:technique | enables:RRAM,HfO2 | params:growth_rate=1.0_A/cycle"
```

This is used for:
- Concept-aware pre-filter (better similarity with structure)
- Dedup (structural similarity, not just name similarity)
- The Conceptual Nexus Model queries

## Code patterns to follow

- **Package manager**: `uv` (never pip). `uv add` for dependencies.
- **Formatting**: `uv run ruff format .` and `uv run ruff check --fix .`
- **Testing**: `uv run pytest`. All new code needs tests. Mock LLM calls, don't use API keys.
- **DB**: `from wikify.store.db import get_session` with `with get_session() as session:`
- **LLM**: `from wikify.llm.client import complete, complete_json`
- **Haiku model**: `HAIKU_MODEL = "claude-haiku-4-5-20251001"`
- **All files**: `from __future__ import annotations` at top
- **Logging**: `logger = logging.getLogger(__name__)`
- **No bare except**: Always log errors
- **Lazy imports**: Inside functions, not at module level (for CLI commands)
- **New SQLite models**: Add to `store/models.py`, register in `store/db.py` imports
- **Git**: Commit and push after each phase. Small frequent commits.

## Agent usage

- Use haiku subagents for simple tasks (file search, formatting)
- Use sonnet subagents for code generation
- Reserve opus for complex reasoning only
- Parallelize independent work with multiple agents
- For any LLM calls needed in testing/benchmarks, use Claude Code subagents — never rely on API keys

## What NOT to change

- The writing pipeline (generate/evaluate/revise) is untouched
- Existing wiki modules (builder.py, linker.py, persona.py, sitemap.py, agent.py) stay as-is
- The epoch orchestrator structure (5 passes) stays — you're enriching what each pass extracts, not restructuring the pipeline
- MCP tools and existing CLI commands stay as-is (add new ones, don't modify existing)

## Validation

After each phase:
1. `uv run pytest` — all tests pass
2. `uv run ruff check .` — no lint errors  
3. Run a small-scale test: select 5 papers, extract with the new template, verify the output includes evidence quotes and parameters
4. Commit and push

After all phases:
- Run a real epoch on 5-10 papers and verify wiki articles contain structured parameter tables and evidence-linked concepts
- The extraction template should have evolved at least once if gap reporting is working
