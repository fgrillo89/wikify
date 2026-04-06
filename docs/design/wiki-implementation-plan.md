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

## Implemented Modules (Epoch Model)

All modules listed below are fully implemented and tested. This section is retained as
a reference for the architecture and function signatures. For current project status,
see `docs/project-status.md`.

### 1. `wiki/concepts.py` -- ConceptRecord, haiku discovery pipeline (done)

Defines `ConceptRecord`, `ConceptRelation`, `EpochLog` SQLite models and the haiku-based
concept discovery pass (epoch Pass 1). Includes rich extraction with template system,
`ConceptEvidence` with fuzzy quote verification, `ExtractionGap` meta-probes, and
`ParameterExtraction` for auto-generated parameter tables.

### 2. `wiki/concept_graph.py` -- co-occurrence graph, importance scoring (done)

Builds concept co-occurrence graph, computes PageRank importance, Louvain communities,
and relation classification. Updates `ConceptRelation` and `ConceptRecord.importance`.

### 3. `wiki/article.py` -- Wikipedia-format article writer (done)

Writes/updates Wikipedia-format articles per concept using the concept's record, graph
neighbors, and domain persona. Routes to additive or revisionary update based on
contradiction detection.

### 4. `wiki/epoch.py` -- epoch orchestrator (done)

Runs all five passes, computes loss score, tracks convergence. Dual execution model:
skill-based (primary, via `/wiki-epoch`) and scripted (secondary, via litellm).

### 5. `wiki/dashboard.py` -- convergence and coverage dashboard (done)

FastAPI dashboard with convergence curve, concept graph, coverage heatmap, epoch log,
and gap cluster endpoints.

### 6. CLI -- `wikify wiki epoch` (done)

All epoch CLI commands implemented: `--n`, `--until-convergence`, `--status`,
`--domain`, `--on-ingest`.

### 7. Ingest hook (done)

`ingest/corpus_refresh.py` bumps epoch counter; optional auto-trigger via `--on-ingest`.

---

## Implementation Patterns (from related work)

### Pattern 1 -- Boolean Gating Agent (before Pass 3 article writes)

Before spending a sonnet call rewriting or upgrading an article in Pass 3, insert a cheap
haiku gate in `wiki/epoch.py`:

```python
def should_update_article(existing_article: str, new_extractions: list[SourceExtraction], model: str = HAIKU_MODEL) -> bool:
    """Return True only if new evidence meaningfully extends the existing article."""
    ...
```

Gate prompt: "Does this new evidence add facts, corrections, or context not already present
in this article? Return YES or NO only."

If NO: skip the article entirely for this epoch; do not call `upgrade_concept_article()`.
If YES: proceed to the Pass 3 article rewrite.

This gate is more semantically precise than the gradient threshold alone. The gradient
measures token volume, not semantic novelty -- two checks must both pass before a sonnet
call is issued:

1. Gradient threshold as fast pre-filter: skip if `new_evidence_tokens / existing_article_tokens < 0.05`.
2. Boolean gate as secondary semantic check: skip if haiku returns NO.

The haiku gate costs approximately $0.0003 per call versus $0.015 for a sonnet rewrite. The
gate pays for itself if it prevents even 1 in 50 sonnet calls. Place `should_update_article`
in `wiki/epoch.py` as a module-level helper; call it inside the Pass 3 loop immediately after
the gradient pre-filter passes.

### Pattern 2 -- Staging/Production ChromaDB Split for Pass 1 Output

Pass 1 (concept discovery) currently writes haiku extractions directly to `ConceptRecord`. A
mid-epoch failure in Pass 1 itself partially populates the concept table with no clean rollback
path. To make Pass 1 atomic, add a `concept_extractions` ChromaDB staging collection that is
per-epoch and ephemeral:

- Pass 1 writes all haiku extractions to `concept_extractions` first.
- After all sources in the epoch are processed, merge into `ConceptRecord` with deduplication.
- `concept_extractions` is cleared at the start of each new epoch.

This means either all extractions for an epoch are committed to `ConceptRecord`, or none are.
It also allows replaying Pass 2 and Pass 3 without re-running haiku if only the
article-writing step failed -- the staging collection retains the raw extractions until the
next epoch clears it.

Add the following three functions to `wiki/concepts.py`:

- `stage_extractions(epoch: int, extractions: list[dict]) -> None` -- writes raw haiku output
  to the staging ChromaDB collection, keyed by `(epoch, source_id)`.
- `commit_staged_extractions(epoch: int) -> int` -- reads all staging entries for the given
  epoch, merges into `ConceptRecord` via the existing deduplication logic, and returns the
  count of records committed.
- `clear_staged_extractions(epoch: int) -> None` -- deletes all staging collection entries
  for the given epoch; called at the start of `run_epoch()` before Pass 1 begins.

### Pattern 3 -- Cross-Chunk Context Threading in Pass 1

Pass 1 currently calls haiku independently for each corpus chunk. Concepts that build across
section boundaries -- a variable introduced in an Introduction and referenced by name in
Methods -- are invisible to haiku when it processes the later chunk in isolation. This
produces duplicate extractions (same concept under slightly different aliases) and broken
alias resolution.

In `wiki/concepts.py:extract_concepts_from_source()`, thread the concept name list forward
across chunks of the same source:

```python
def extract_concepts_from_source(
    source_id: str,
    chunks: list[Chunk],
    epoch: int,
    model: str = HAIKU_MODEL,
) -> list[ConceptRecord]:
    prior_concepts: list[str] = []  # names extracted from previous chunks
    results = []
    for chunk in chunks:
        extracted = _extract_from_chunk(chunk, prior_context=prior_concepts, model=model)
        prior_concepts = [c.name for c in extracted]  # carry forward
        results.extend(extracted)
    return results
```

The haiku prompt for `_extract_from_chunk` includes: "Previously extracted concepts from
earlier sections of this source: {prior_concepts}. Do not re-extract these unless this
section adds new information about them."

This reduces alias duplication within a single source and gives haiku enough context to
resolve cross-section references without requiring a larger context window or a second
consolidation pass. The `prior_concepts` list is reset to `[]` at the start of each new
source -- it does not carry across sources.

---

## Rich Media, People & HTML Layout (complete)

Five additional features layered on top of the epoch model. All implemented and tested.
Design spec: `docs/design/wiki-rich-media-people-layout.md`.

| Feature | Module | Description |
|---------|--------|-------------|
| Image/table extraction | `extract/media.py` | Unified pipeline; `Figure` gains `media_type`, `label`, `page_number`, `bbox`, `markdown_table`, `llm_description` |
| Equation extraction | `extract/equations.py` | LaTeX/chemical/inline detection; new `Equation` SQLite model |
| People identification | `wiki/people.py` | Name dedup, author cross-ref; `ConceptRecord` with `type=person` |
| Haiku vision | `llm/vision.py`, `wiki/figure_enrichment.py` | Structured figure descriptions; MCP tools `get_figure_details`, `get_paper_figures` |
| Wikipedia HTML layout | `wiki/html.py`, `wiki/templates/` | Static site, Wikipedia Vector skin, KaTeX, client-side search; CLI: `wikify wiki html [--serve]` |

---

## Next: Adaptive Knowledge Engine

The six phases planned for the next evolution of the Wikipedia pipeline. Full spec:
`docs/design/adaptive-knowledge-engine.md`.

| Phase | Description | Key deliverable |
|-------|-------------|-----------------|
| 1. Yield-based feedback | Track extraction yield per chunk; make the haiku Pass 1 prompt adaptive per epoch based on the corpus's actual concept-type distribution | `ChunkMiningLog` yield fields; `_mining_stats.json`; adaptive prompt context block |
| 2. UCB chunk scoring | Replace the flat tier-based mining frontier with a UCB1-style scorer combining section yield, paper yield, graph signal, contradiction bonus, novelty, and exploration bonus | `score_chunk()` in `concepts.py`; budget-allocation loop in `epoch.py` |
| 3. Contradiction-driven exploration | Boost mining priority for papers in the citation neighborhood of detected contradictions | `ContradictionRecord` model; expansion logic in `epoch.py` |
| 4. Hierarchical taxonomy | Extend Pass 1 extraction to detect IS-A parent-child relationships; add `parent_concept_id` to `ConceptRecord`; add Sub-topics sections to parent articles | `ConceptRecord.parent_concept_id`; hierarchy-aware dedup; IS-A edges in concept graph |
| 5. Schema evolution | Accumulate extraction gaps over epochs; cluster them; propose and auto-accept new concept types | `ExtractionGap` and `TypeProposal` models; `wikify wiki audit --schema` |
| 6. Conceptual Nexus Model | Formalize concept graph + embeddings + articles as a sparse tensor; add gap detection, analogy detection, and cluster coherence queries | `wiki/nexus.py`; Concept Card JSON; tensor query API |

Phases 1-3 are sequential. Phase 4 can start in parallel with Phase 3. Phase 5 depends on
Phase 1. Phase 6 requires all previous phases to be stable.

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

## Test Coverage (implemented)

All modules have tests. No real LLM or DB calls in tests -- mock `complete()`,
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
