# Wiki Implementation Plan

## Design Specification

The authoritative design for the Wikipedia pipeline is `docs/design/wiki-wikipedia-model.md`.
Read that document first. This plan translates the design into concrete implementation tasks,
listing what is already built and what still needs to be written.

The core idea: given an unstructured corpus, build a concept-first, self-correcting Wikipedia
that converges over epochs. The agent reads the corpus, discovers named concepts, writes
Wikipedia-style articles, and iterates. Concepts drive everything -- no upfront sitemap,
no topic planning, no manual topic hints required.

The writing pipeline (`generate`, `evaluate`, `revise`) is entirely separate and unchanged.

---

## What is Already Implemented

All modules in `src/wikify/wiki/` are implemented and tested. They form the building
blocks that the epoch orchestrator will coordinate.

### `wiki/persona.py`

Generates and caches a 150-200 word expert persona per domain. The persona is prepended to
all reduce-phase LLM prompts so article tone stays consistent across a domain.

Key functions:
- `generate_domain_persona(domain, model) -> str` -- one LLM call, stores in `DomainPersona`
- `get_or_create_persona(domain, model) -> str` -- returns cached or generates on demand
- `invalidate_persona(domain)` -- deletes stored persona, forces regeneration on next use

### `wiki/mapreduce.py`

The article-writing engine used by the epoch model's Pass 3.

Key components:
- `SourceExtraction` dataclass -- result of the haiku map call for one source
- `map_chunks_to_topic(topic_query, scope, domain, model, key_source_ids) -> list[SourceExtraction]`
  - Step 1: enriches with graph metrics (hub/bridge/frontier roles)
  - Step 2: pre-filters candidates via `search_papers()` (embedding similarity)
  - Step 3: calls haiku per candidate to extract relevant claims (YES/NO + 1-3 sentences)
  - Always includes hub and bridge papers regardless of embedding similarity
- `reduce_to_article(topic, scope, domain, extractions, persona, status, model) -> str`
  - Synthesizes extracted evidence into a structured article body
  - Determines register (academic/practice/mixed) from doc_type mix
  - Selects zone labels accordingly (What Is Known / Where the Field Disagrees / Unresolved)
- `record_coverage(article_slug, domain, extractions) -> int`
  - Writes `SourceCoverage` rows for all is_relevant=True extractions

### `wiki/maintenance.py`

Three-tier update system for the epoch model's Pass 3 (article revision).

Key functions:
- `detect_contradiction(existing_body, new_extraction) -> bool`
  - Cheap embedding-based check (cosine similarity < 0.30 = likely contradiction)
  - Used to route new evidence to additive vs revisionary update
- `additive_update(article_path, new_extractions, persona, model) -> str`
  - Extends article with new supporting evidence; does not restructure
- `revisionary_update(article_path, new_extractions, persona, model) -> str`
  - Flags contradicted claims with WARNING, presents both positions, moves to contested zone
- `structural_audit(wiki_dir, domain, model) -> StructuralReport`
  - Identifies split candidates (>15 SourceCoverage rows), merge candidates (>80% Jaccard
    overlap), deprecation candidates (0 coverage + <3 sources), orphan sources,
    contradiction flags, and graph drift (hub/bridge papers not in any article)

### `wiki/builder.py`

Article file I/O and hierarchical index generation. Used by the epoch model's
Passes 4 and 5.

Key functions:
- `write_article(path, title, content, sources, topics, status, model)` -- writes .md with YAML frontmatter
- `read_article_frontmatter(path) -> dict` -- parses frontmatter (python-frontmatter or regex fallback)
- `slugify(title) -> str` -- filesystem-safe slug
- `article_path(wiki_dir, category, slug) -> Path`
- `generate_theme_index(wiki_dir, domain, theme_slug, theme_entry, concept_entries) -> Path`
- `generate_domain_index(wiki_dir, domain, sitemap) -> Path`
- `generate_library_catalog(wiki_dir, all_domain_info) -> Path`
- `append_unanswered_question(wiki_dir, question, domain)` -- appends to `_unanswered.jsonl`
- `generate_wiki_index(wiki_dir) -> str` -- backward-compatible single-domain index
- `find_stale_articles(wiki_articles, cutoff) -> list`

### `wiki/linker.py`

Cross-reference pass used by the epoch model's Pass 4.

Key functions:
- `cross_link_articles(wiki_dir, sitemap | None) -> int`
  - If sitemap provided: uses `SitemapEntry.related_slugs`
  - If None: slug-matching fallback (searches for verbatim title mentions in body)
  - Adds or updates `## See Also` sections with `[[wikilinks]]`
  - Returns count of articles updated
- `ensure_parent_backlinks(wiki_dir, sitemap)` -- ensures concept articles appear in
  their parent theme's Concepts section

### `wiki/sitemap.py`

Data contracts and optional user-directed exploration. In the epoch model, the sitemap is
secondary -- it can be used to give the agent a topical focus, but it is not the primary
discovery mechanism.

Key classes:
- `SitemapEntry` dataclass -- one planned article (title, slug, category, scope, parent_slug,
  key_source_ids, related_slugs, depth, domain)
- `WikiSitemap` dataclass -- full plan (entries list, corpus_summary, model)
  - `themes()`, `concepts()`, `by_slug()`, `ordered_for_writing()`
  - `save(wiki_dir)`, `WikiSitemap.load(wiki_dir)` -- JSON persistence

Key functions:
- `generate_sitemap(wiki_dir, topic_hint, model, max_explore_papers, domain) -> WikiSitemap`

### `wiki/agent.py`

Article writing orchestration using the sitemap + mapreduce pipeline.

Key functions:
- `build_wiki_from_sitemap(sitemap, wiki_dir, model, resume)` -- writes all articles in
  dependency order (themes before concepts)
- `build_article_from_entry(entry, wiki_dir, model) -> (content, source_ids)` -- writes
  one article using `map_chunks_to_topic` + `reduce_to_article`
- `build_wiki_article(topic, scope, status, model) -> (content, source_ids)` -- fallback
  for no-sitemap expand

### SQLite data models (`store/models.py`)

- `WikiArticle` -- `id` (slug), `title`, `status`, `file_path`, `source_ids` (JSON),
  `topic_keys` (JSON), `domain`, `created_at`, `updated_at`, `model`, `needs_update`
- `DomainPersona` -- `domain` (PK), `persona_text`, `source_sample` (JSON), `generated_at`, `model`
- `SourceCoverage` -- `id` (auto PK), `source_id`, `article_slug`, `domain`, `extraction`, `covered_at`

### CLI commands (wiki, using sitemap pipeline)

- `wikify wiki init` -- bootstrap wiki using exploration agent + sitemap + mapreduce
- `wikify wiki expand [concept]` -- expand stub/draft; falls back to `build_wiki_article`
- `wikify wiki sync` -- update articles with `needs_update=True` using new corpus evidence
- `wikify wiki audit` -- structural health report; `--fix` queues candidates for sync
- `wikify wiki health` -- orphan, staleness, synthesis gap report

---

## What Needs to Be Implemented (Epoch Model)

### 1. `wiki/concepts.py` -- ConceptRecord, haiku discovery pipeline

**Purpose:** Define the three new SQLite models needed by the epoch model, and implement the
haiku-based concept discovery pass (epoch Pass 1).

**SQLite models to add to `store/models.py`:**

```python
class ConceptRecord(SQLModel, table=True):
    id: str              # slugified name, PK (e.g. "atomic_layer_deposition")
    name: str            # canonical display name (e.g. "Atomic Layer Deposition")
    aliases: str         # JSON list, e.g. '["ALD", "atomic layer dep."]'
    concept_type: str    # technique | material | phenomenon | method | theory | dataset
    domain: str          # inferred from source distribution
    importance: float    # 0-1, computed from concept graph (updated in Pass 2)
    epoch_discovered: int
    epoch_last_updated: int
    article_status: str  # none | stub | draft | full
    article_path: str    # relative path to .md file, or ""

class ConceptRelation(SQLModel, table=True):
    id: int | None       # PK autoincrement
    source_concept: str  # FK -> ConceptRecord.id
    target_concept: str  # FK -> ConceptRecord.id
    relation_type: str   # IS-A | PART-OF | USED-IN | ENABLES | CONTRASTS-WITH
    weight: float        # co-occurrence strength
    epoch: int

class EpochLog(SQLModel, table=True):
    id: int | None       # PK autoincrement
    epoch: int
    triggered_by: str    # "user" | "ingest" | "schedule"
    started_at: datetime
    completed_at: datetime | None
    concepts_discovered: int
    stubs_upgraded: int
    articles_written: int
    contradictions_flagged: int
    cross_refs_added: int
    converged: bool
    loss_score: float = 0.0   # L computed after Pass 5
    loss_delta: float = 0.0   # |L(epoch_n) - L(epoch_n-1)|
```

**Key functions in `wiki/concepts.py`:**

- `discover_concepts(paper_ids: list[str], epoch: int, model: str | None) -> list[ConceptRecord]`
  - For each paper: feed digest to haiku with prompt asking for named concepts
  - JSON response: `[{name, type, aliases, one_line_definition}]`
  - Returns deduplicated `ConceptRecord` list
- `merge_concept_records(new_records: list[ConceptRecord], epoch: int) -> int`
  - Merges new records into DB (deduplicating by name + aliases)
  - Returns count of new concepts added (not already in DB)
- `get_concept_by_name(name: str) -> ConceptRecord | None`
- `list_concepts(domain: str, min_importance: float) -> list[ConceptRecord]`

**Inputs:** list of `Paper.id` values (all corpus papers not yet fully mined)
**Outputs:** updated `ConceptRecord` table; returns list of new/updated records

### 2. `wiki/concept_graph.py` -- co-occurrence graph, importance scoring

**Purpose:** Build the concept co-occurrence graph from the `ConceptRecord` table and
`SourceCoverage` data. Assign importance scores. Classify node roles (core/peripheral/bridge).
Update `ConceptRelation` table. Used by epoch Pass 2.

**Key functions:**

- `build_concept_graph(domain: str, epoch: int) -> nx.DiGraph`
  - Edge weight = how often two concepts appear in the same source/chunk
  - Node degree = corpus frequency x source diversity
  - Returns a NetworkX graph
- `score_importance(graph: nx.DiGraph) -> dict[str, float]`
  - Returns `{concept_id: importance}` (0-1 normalized)
  - Based on: graph degree, source diversity, cross-domain edges
- `classify_node_roles(graph: nx.DiGraph, scores: dict) -> dict[str, str]`
  - Returns `{concept_id: "core" | "peripheral" | "bridge"}`
  - Core: high degree, many sources
  - Peripheral: low degree, few sources
  - Bridge: connects disparate concept clusters
- `extract_relations(graph: nx.DiGraph, epoch: int) -> list[ConceptRelation]`
  - Produces `ConceptRelation` rows from graph edges (IS-A, ENABLES, CONTRASTS-WITH, etc.)
- `update_concept_importance(scores: dict[str, float])` -- writes scores back to `ConceptRecord`

**Inputs:** `ConceptRecord` table, corpus chunk data
**Outputs:** `ConceptRelation` table updated, `ConceptRecord.importance` updated

### 3. `wiki/article.py` -- Wikipedia-format article writer

**Purpose:** Write or update a Wikipedia-format article for one concept, using the concept's
record, its graph neighbors, and the domain persona. This is the concept-aware alternative to
`build_article_from_entry`. Used by epoch Pass 3.

The article format is defined in `docs/design/wiki-wikipedia-model.md` (Definition,
Mechanism, Key Facts, In This Corpus, Relationships table, Open Questions).

**Key functions:**

- `write_concept_article(concept: ConceptRecord, neighbors: list[ConceptRecord], domain: str, model: str | None) -> str`
  - Calls `get_or_create_persona(domain)` for consistent voice
  - Calls `map_chunks_to_topic(concept.name, scope=concept.one_line_definition, domain=domain)`
  - Builds Relationships table from `neighbors` and `ConceptRelation` rows
  - Calls `reduce_to_article()` with the Wikipedia-format structure instead of
    the three-zone structure used by the sitemap pipeline
  - Returns article body markdown (no frontmatter)
- `upgrade_concept_article(concept: ConceptRecord, article_path: Path, new_extractions: list, domain: str, model: str | None) -> str`
  - For existing stubs/drafts: detects contradictions via `detect_contradiction()`,
    routes to `additive_update()` or `revisionary_update()` accordingly
  - Upgrades `article_status` (stub -> draft -> full) if new evidence is sufficient
- `should_write_full(concept: ConceptRecord, extractions: list[SourceExtraction]) -> bool`
  - Heuristic: >=3 relevant extractions AND concept.importance > 0.3 -> full article
  - Otherwise: stub

**Inputs:** `ConceptRecord`, list of neighbor `ConceptRecord` objects
**Outputs:** article body markdown string; `ConceptRecord.article_status` and `article_path` updated

### 4. `wiki/epoch.py` -- epoch orchestrator

**Purpose:** Run one complete epoch (Passes 1-5 in order), track convergence, expose trigger
hooks. The primary entry point for the Wikipedia pipeline.

**Key functions:**

- `run_epoch(triggered_by: str, domain: str, model: str | None) -> EpochLog`
  - Runs all five passes in order:
    1. `discover_concepts()` -- haiku, parallel per paper
    2. `build_concept_graph()` + `score_importance()` + `update_concept_importance()` -- local
    3. For each concept ranked by importance: `write_concept_article()` or `upgrade_concept_article()`
    4. `cross_link_articles()` -- local, scans all articles for concept name mentions
    5. `generate_library_catalog()` + domain/theme indexes -- local
  - Computes loss score after Pass 5 (see Convergence Algorithm below)
  - Writes `EpochLog` row on completion
  - Returns the completed `EpochLog`
- `run_until_convergence(domain: str, max_epochs: int, model: str | None) -> list[EpochLog]`
  - Calls `run_epoch()` repeatedly until `check_convergence()` returns True
  - Returns list of all `EpochLog` rows
- `check_convergence(recent_logs: list[EpochLog]) -> bool`
  - Convergence criteria (all must hold):
    1. New concepts/epoch < 2% of total concept count
    2. Stub ratio < 10%
    3. No new contradictions flagged in last epoch
    4. `loss_delta` < epsilon (default 0.01)
- `compute_loss(epoch: int) -> tuple[float, float]`
  - Reads current wiki state from SQLite; returns `(loss_score, loss_delta)`
  - See Convergence Algorithm below for the formula
- `get_epoch_status() -> dict` -- returns current epoch number + convergence metrics

**Inputs:** corpus (via `ConceptRecord` table + corpus embeddings)
**Outputs:** updated wiki articles in `data/wiki/`, `EpochLog` row in SQLite

#### Convergence Algorithm

After Pass 5 completes each epoch, `compute_loss()` evaluates the following formula
using current counts read from SQLite:

```
L = alpha * stub_ratio
  + beta  * orphan_concept_rate
  + gamma * contradiction_density
  - delta * cross_ref_density
```

where `stub_ratio = stubs / total_concepts`, `orphan_concept_rate = concepts_with_no_refs /
total_concepts`, `contradiction_density = flagged_claims / total_claims`, and
`cross_ref_density = total_cross_refs / total_articles`. Default weights: alpha=0.3,
beta=0.2, gamma=0.3, delta=0.2 (stored in project config, not hardcoded).

`loss_delta` is `|L(epoch_n) - L(epoch_n-1)|`. Both values are written to the `EpochLog`
row before it is committed. The convergence check fails if `loss_delta >= epsilon`
even when the three threshold criteria above are met.

The model selected for Pass 3 is also determined here: if the previous epoch's
`loss_score >= 0.3`, haiku is used for article drafting; if `loss_score < 0.3`,
sonnet is used. This transition is logged and visible in `wikify wiki epoch --status`.

### 5. `wiki/dashboard.py` -- convergence and coverage dashboard

**Purpose:** FastAPI application serving a local web dashboard for quantitative monitoring
of epoch convergence, concept graph state, and corpus coverage. All data is read from
SQLite -- no LLM calls are made. Launched via `wikify wiki dashboard`.

**Key components:**

- `app: FastAPI` -- module-level FastAPI instance
- `GET /api/epochs` -- returns all `EpochLog` rows as JSON (loss_score, loss_delta, counts, duration)
- `GET /api/concepts` -- returns all `ConceptRecord` rows (name, status, importance, domain)
- `GET /api/coverage` -- returns `SourceCoverage` aggregated as sources x domains matrix
- `GET /api/gradient` -- returns top-N concepts by information gradient (new_evidence_tokens / existing_article_tokens)
- `GET /` -- serves the single-page HTML/JS application

The frontend uses Plotly (via CDN) for the convergence curve and domain health charts,
and D3.js (via CDN) for the force-directed concept graph. No build step is required.

**Inputs:** SQLite database (EpochLog, ConceptRecord, SourceCoverage tables)
**Outputs:** local HTTP server; no files written

### 6. CLI -- `wikify wiki epoch` and `wikify wiki dashboard`

Add to `src/wikify/cli.py`:

```
wikify wiki epoch                        # run one epoch
wikify wiki epoch --n 5                  # run N epochs
wikify wiki epoch --until-convergence    # run until converged
wikify wiki epoch --status               # show epoch log
wikify wiki epoch --domain DOMAIN        # restrict to one domain
wikify wiki epoch --on-ingest            # auto-trigger on next ingest
wikify wiki dashboard                    # launch local convergence/coverage dashboard
```

### 7. Ingest hook

After `ingest/corpus_refresh.py` completes a refresh:
- Increment epoch counter (or mark epoch as stale) in `_epoch.json`
- If `--on-ingest` was previously configured, trigger `run_epoch()` automatically

---

## Implementation Order

The modules have hard dependencies in this order:

```
1. ConceptRecord / ConceptRelation / EpochLog models
   (add to store/models.py + register in store/db.py)
   Note: EpochLog must include loss_score and loss_delta fields
        |
        v
2. wiki/concepts.py
   (needs ConceptRecord model; uses haiku via llm/client.py)
        |
        v
3. wiki/concept_graph.py
   (needs ConceptRecord, ConceptRelation models; uses NetworkX)
        |
        v
4. wiki/article.py
   (needs ConceptRecord; reuses persona.py, mapreduce.py, maintenance.py)
        |
        v
5. wiki/epoch.py
   (orchestrates all four above + builder.py + linker.py;
    implements compute_loss() after Pass 5)
        |
        v
6. CLI: wikify wiki epoch
   (calls epoch.py; adds --n, --until-convergence, --status, --on-ingest flags)
        |
        v
7. Ingest hook
   (corpus_refresh.py bumps epoch counter; optional auto-trigger)
        |
        v
8. wiki/dashboard.py
   (depends on EpochLog + SourceCoverage being populated by at least one epoch)
```

Steps 1-4 can be worked in parallel by independent agents once the models are added.
Steps 5-7 depend on 1-4 being complete. Step 8 depends on step 5.

---

## What Does NOT Change

- `wikify generate` / `evaluate` / `revise` -- writing pipeline is untouched
- All existing retrieval strategies and agent workflows
- `explore_corpus` in `workflows.py`
- The Obsidian vault (still auto-generated from the enriched layer, separate from wiki)
- MCP server tools -- all remain as-is
- `wiki/sitemap.py` -- remains as an optional user-directed focus tool
- `wiki/agent.py`, `wiki/builder.py`, `wiki/linker.py`, `wiki/persona.py`,
  `wiki/mapreduce.py`, `wiki/maintenance.py` -- all reused, not modified

---

## Test Coverage Required

Each new module needs tests. No real LLM or DB calls in tests -- mock `complete()`,
`get_graph_metrics()`, `map_chunks_to_topic()`, `reduce_to_article()`.

Shared fixtures (add to `tests/test_wiki/conftest.py`):
- `sample_corpus_papers(tmp_db)` -- creates 5 Paper rows in a temp SQLite DB
- `sample_concept_records()` -- returns 3 ConceptRecord objects with known properties
- `sample_concept_graph()` -- returns a NetworkX graph with 4 nodes, 6 edges
- `sample_epoch_log()` -- returns a completed EpochLog with known counts

Per-module tests:
- `tests/test_wiki/test_concepts.py` -- discovery parsing, deduplication, merge logic
- `tests/test_wiki/test_concept_graph.py` -- importance scoring, role classification
- `tests/test_wiki/test_article.py` -- article format structure, upgrade routing
- `tests/test_wiki/test_epoch.py` -- pass ordering, convergence criteria, EpochLog writing
