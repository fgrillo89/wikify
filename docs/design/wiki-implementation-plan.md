# Wiki Layer Implementation Plan

## Goal

Implement a production-quality wiki generation system with: expert domain personas,
map-reduce corpus coverage, graph-aware source weighting, hierarchical indexes,
source-type-adaptive article format, and three-tier maintenance.

The review article writing pipeline (`generate`, `evaluate`, `revise`) is unchanged.

---

## Architecture Overview

```
GENERATION PIPELINE
───────────────────
Corpus (papers + web + notes)
    │
    ├─► Graph analysis (hub/bridge/frontier/gaps) ──► source priority weights
    │
    ├─► Domain classification ──► DomainPersona (generated once, stored)
    │
    ├─► MAP phase (haiku) ──► per-chunk topic relevance extraction
    │
    └─► REDUCE phase (sonnet + persona) ──► article (three zones + Source Pointers)
                                                │
                                     SourceCoverage rows written

INDEX STRUCTURE
───────────────
data/wiki/
  _index.md                       ← library catalog (domains + recent + unanswered Qs)
  domains/
    {domain}/
      _index.md                   ← domain master index (themes table)
      _index_{theme_slug}.md      ← per-theme index (concept list + open Qs)
      themes/
      concepts/
      syntheses/
  syntheses/                      ← cross-domain synthesis articles
  queries/                        ← promoted Q&A answers

MAINTENANCE TIERS
──────────────────
Additive   → new source adds evidence      → append citation + sentence
Revisionary → new source contradicts claim → flag ⚠️, keep both, mark contested
Structural  → article scope wrong          → split/merge/deprecate candidate
```

---

## Data Model Changes (implement first — all agents depend on these)

### `src/scholarforge/store/models.py`

**Add `domain` field to `WikiArticle`:**
```python
domain: str = Field(default="")  # e.g. "material_science", "machine_learning"
```

**Add `DomainPersona` table:**
```python
class DomainPersona(SQLModel, table=True):
    domain: str = Field(primary_key=True)   # e.g. "material_science"
    persona_text: str                        # 200-word expert persona
    source_sample: str = Field(default="[]") # JSON list of source titles used
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    model: str = ""
```

**Add `SourceCoverage` table:**
```python
class SourceCoverage(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source_id: str = Field(index=True)      # Paper.id
    article_slug: str = Field(index=True)   # WikiArticle.id
    domain: str = ""
    extraction: str = ""                    # haiku-extracted sentence(s)
    covered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

Ensure both new tables are imported in `src/scholarforge/store/db.py` so they are
created on DB init.

---

## Phase 1: Domain Persona System

**File: `src/scholarforge/wiki/persona.py`** (new)

```python
def generate_domain_persona(domain: str, model: str | None = None) -> str:
    """
    One LLM call. Reads up to 20 source titles+summaries from the domain,
    produces a 200-word expert persona stored in DomainPersona table.

    Prompt instructs:
    - Technical register appropriate to the domain
    - What counts as a strong claim vs. practitioner opinion
    - How uncertainty is qualified in this field
    - The community's active debates and unsettled questions
    - What the reader of these wiki articles most needs (researcher / practitioner /
      designer / engineer — inferred from source types)

    Returns the persona text.
    """

def get_or_create_persona(domain: str, model: str | None = None) -> str:
    """
    Look up DomainPersona in DB. If absent, call generate_domain_persona().
    Returns persona_text.
    """

def invalidate_persona(domain: str) -> None:
    """Delete stored persona so it is regenerated on next use."""
```

**Persona prompt template:**

```
You are about to write wiki articles for a personal knowledge base on the domain: {domain}.

Here is a sample of sources in this knowledge base:
{source_sample}

Define the expert perspective from which all articles should be written.
Your response must address:
1. REGISTER: What technical vocabulary and level of precision is appropriate?
2. CLAIMS: What distinguishes a strong claim from an opinion or speculation in this field?
3. UNCERTAINTY: How is uncertainty qualified? (e.g., "not yet reproduced", "context-dependent",
   "practitioner consensus but no RCTs")
4. DEBATES: What are the active disputes in this field that should appear in "Contested" sections?
5. READER: Who reads this wiki — researcher, engineer, practitioner, designer?
   What do they most need from each article?

Write 150-200 words in second person ("You are a senior..."). Be specific to this domain,
not generic.
```

---

## Phase 2: Map-Reduce Corpus Coverage

**File: `src/scholarforge/wiki/mapreduce.py`** (new)

### Constants
```python
MAP_SIMILARITY_THRESHOLD = 0.35   # cosine similarity to topic query for pre-filter
MAP_MAX_SOURCES = 60              # max sources to map per article
HAIKU_MODEL = "claude-haiku-4-5-20251001"
```

### `map_chunks_to_topic(topic_query, scope, domain, model) -> list[SourceExtraction]`

```python
@dataclass
class SourceExtraction:
    source_id: str
    display_name: str
    doc_type: str
    graph_role: str           # "hub" | "bridge" | "frontier" | "standard"
    pagerank_score: float
    extraction: str           # haiku output: 1-3 sentences or "NO"
    is_relevant: bool
```

Steps:
1. **Graph enrichment first**: call `get_graph_metrics()` to get `{paper_id: {pagerank, role}}`
   for all papers in the domain. Store as a lookup dict.
2. **Embedding pre-filter**: embed `topic_query` using the ONNX model from `store/embeddings.py`.
   Query ChromaDB `summary_chunks` collection for top-`MAP_MAX_SOURCES` papers with
   cosine similarity > `MAP_SIMILARITY_THRESHOLD`.
   Also include ALL papers classified as `hub` or `bridge` by the graph (regardless of
   similarity) — these define the field's consensus and cross-connections.
3. **Haiku map**: for each source in the filtered set, call haiku with:

```
Source: {display_name} ({doc_type})
Summary: {digest_text or section_summaries first 800 chars}

Topic: {topic_query}
Scope: {scope}

Does this source contain information relevant to the topic and scope above?
If YES: extract the key claim(s) in 1-3 sentences, including any specific
numbers, mechanisms, or contested points. Prefix with YES:
If NO: respond with exactly: NO
```

4. Parse responses. Mark `is_relevant = response.startswith("YES")`.
5. Return list of `SourceExtraction` objects.

### `reduce_to_article(topic, scope, domain, extractions, persona, status, model) -> str`

Takes the list of `SourceExtraction` objects and writes the article body.

**Reduce prompt structure:**
```
{persona_text}

You are writing a wiki article titled: {topic}
Scope: {scope}
Target: {length_hint based on status}

The following evidence was extracted from the corpus.
Sources marked [HUB] are highly cited field-defining papers — treat their claims
as representing established consensus. Sources marked [BRIDGE] connect different
research communities — their claims often belong in the Contested or Synthesis zone.
Sources marked [FRONTIER] represent recent or peripheral work — use for Open Questions.

--- EVIDENCE ---
{for each extraction: "[ROLE] {display_name} ({doc_type})\n{extraction}\n"}
--- END EVIDENCE ---

Write the article using this structure. Adapt zone labels to the domain register:

## {established_label}
[Claims supported by multiple sources, especially HUBs. Inline citations [REF:display_name].]

## {contested_label}
[Where sources disagree or practitioners diverge. Cite both sides.]

## {open_label}
[What is unresolved. No citations — this is the absence of evidence.]

## Source Pointers
[For each claim you had to compress: annotated pointer to exact source + location.]

_Provenance: {N papers}, {M web articles}, {K notes}. 
{Primary evidence type sentence}._

Rules:
- One concept per sentence
- Inline citations immediately after the claim they support
- No em-dashes as separators
- No meta-commentary ("this article covers...")
- Do not invent claims not present in the evidence
```

### Zone label mapping by domain register:

```python
ZONE_LABELS = {
    "academic": ("What Is Known", "Where the Field Disagrees", "Unresolved Questions"),
    "practice": ("Practitioner Consensus", "Ongoing Debates", "What Depends on Context"),
    "mixed":    ("Established", "Points of Tension", "Open Territory"),
    "design":   ("Established Principles", "Aesthetic Debates", "Context-Dependent"),
}
```

Determine register from the mix of `doc_type` values in the extractions:
- >60% paper/report/thesis → "academic"
- >60% web_article/markdown/note → "practice"  
- Any "design" in domain name → "design"
- Otherwise → "mixed"

### Coverage recording

After `reduce_to_article` completes, write `SourceCoverage` rows for all
`is_relevant=True` extractions.

---

## Phase 3: Graph-Aware Sitemap Generation

**Update `src/scholarforge/wiki/sitemap.py`**

### Changes to `explore_corpus_for_sitemap()`

Before running the agent loop, call `get_graph_metrics()` and prepend this context
to the agent's system prompt:

```
Graph structure of this corpus:
HUB papers (highest PageRank / centrality — define field consensus):
{list of hub paper display_names, max 10}

BRIDGE papers (connect different topic clusters — cross-community insights):
{list of bridge paper display_names, max 10}

FRONTIER papers (sparse embedding regions — leading edge of the field):
{list of frontier paper display_names, max 10}

When planning the wiki structure:
- HUB papers should appear as key_source_ids in THEME articles (they define the domain)
- BRIDGE papers should appear in SYNTHESIS articles or in concept articles where
  two sub-fields meet
- FRONTIER papers should inform the Open Questions sections and stub/draft articles
  at the edges of the domain
```

Also call `find_corpus_gaps()` and `find_synthesis_opportunities()` and include
their output in the agent's first message. The agent uses these to identify:
- Sparse areas → concept articles with `depth="stub"` or candidates for synthesis
- Synthesis opportunities → synthesis article candidates

### Add domain support to `generate_sitemap()`

Add `domain: str = ""` parameter. When set:
- Filter corpus queries to that domain's sources only
- Pass domain to `explore_corpus_for_sitemap`
- Store `domain` on each `SitemapEntry`

Add a new function:

### `generate_multi_domain_sitemap(wiki_dir, model, max_explore_papers) -> dict[str, WikiSitemap]`

For corpora with multiple domains:
1. Classify sources into domains (from `Paper.topic_keys` prefix or LLM inference)
2. For each domain with ≥5 sources: call `generate_sitemap(domain=domain)`
3. Detect cross-domain synthesis: embed each domain's theme summaries, find
   pairs with cosine similarity > 0.5 across domains — these are synthesis candidates
4. Generate synthesis `SitemapEntry` objects for cross-domain pairs
5. Return `{domain: WikiSitemap}` dict

---

## Phase 4: Hierarchical Index Generation

**Update `src/scholarforge/wiki/builder.py`**

Replace `generate_wiki_index()` with a three-tier system:

### `generate_theme_index(wiki_dir, domain, theme_entry, concept_entries, model) -> Path`

Writes `domains/{domain}/_index_{theme_slug}.md`:

```markdown
# Theme: {theme_title}
_{N} concept articles | {M} sources | Domain: {domain}_

## Overview
{2-3 sentence theme summary from theme article frontmatter.summary}

## Concepts
| Article | Scope | Depth | Sources | Open Questions |
|---------|-------|-------|---------|---------------|
| [[HfO2 Growth Kinetics]] | GPC, saturation, temperature window | full | 12 | 2 |
| [[Precursor Chemistry]] | volatility, reactivity | draft | 6 | 1 |

## Open Questions in This Theme
{collected from concept article frontmatter.open_questions}

## Graph Highlights
Hub: {top hub paper in this theme}
Bridge: {bridge paper connecting this theme to another}
Frontier: {frontier paper at edge of this theme}
```

### `generate_domain_index(wiki_dir, domain, sitemap) -> Path`

Writes `domains/{domain}/_index.md`:

```markdown
# {domain_title} Knowledge Base
_{N} themes | {M} concepts | {K} sources_

## Themes
| Theme | Articles | Scope | Index |
|-------|----------|-------|-------|
| [ALD Fundamentals](_index_ald_fundamentals.md) | 8 | Growth mechanisms... | → |

## Domain Graph Summary
{hub paper count, bridge paper count, frontier paper count}
Top hub: {paper title}
Most connected bridge: {paper title}

## Open Questions Across Domain
{top 5 open_questions from all articles, deduplicated}
```

### `generate_library_catalog(wiki_dir, all_domain_sitemaps) -> Path`

Writes `data/wiki/_index.md`:

```markdown
# Personal Knowledge Base
_{N} domains | {M} articles | {K} sources | Updated {date}_

## Domains
| Domain | Articles | Sources | Last Updated |
|--------|----------|---------|-------------|
| [Material Science](domains/material_science/_index.md) | 34 | 206 | 2026-04-03 |

## Cross-Domain Connections
- [[ML for Materials Discovery]] — Machine Learning ↔ Material Science
- [[Color Theory: Art to Chemistry]] — House Decor ↔ Material Science

## Unanswered Questions
_Appended by wiki query when the wiki couldn't fully answer — these drive wiki expand_
{loaded from data/wiki/_unanswered.jsonl if exists}

## Recent Additions
{top 5 most recently updated articles across all domains}
```

### `append_unanswered_question(wiki_dir, question, domain) -> None`

Appends `{"question": question, "domain": domain, "date": iso}` to
`data/wiki/_unanswered.jsonl`. Called by `wiki query` when escalation reaches
Level 4 (source section) and still doesn't fully answer.

---

## Phase 5: Escalation Protocol in `wiki query`

**Update `wiki query` command in `src/scholarforge/cli.py`**

Implement explicit 5-level escalation:

```python
def _answer_with_escalation(question, wiki_dir, domain, model):
    """
    Level 0: Read _index.md (~3KB) → can this be answered from domain/theme info?
    Level 1: Read domain _index.md + relevant theme _index → is relevant article identified?
    Level 2: Read article(s) → is the claim present with sufficient precision?
    Level 3: Read source digest(s) cited in Source Pointers → is specific data present?
    Level 4: Read source section(s) → deepest available before admitting gap

    At each level, the LLM decides: ANSWER or ESCALATE.
    If ESCALATE after Level 4: append to _unanswered.jsonl and suggest wiki expand.
    """
```

The escalation prompt at each level includes:
- The question
- The current level's content
- "Can you answer this question fully and accurately from what you have read?
   If YES: answer it now. If NO: state exactly what is missing and what source
   or section would contain it."

**`--deep` mode**: before running escalation, first build an ephemeral mini-wiki
(3-5 articles) targeted at the question using the map-reduce pipeline, stored in
memory (not written to disk unless `--promote`). Use the ephemeral articles as
Level 2 instead of existing wiki articles.

---

## Phase 6: Three-Tier Maintenance

**File: `src/scholarforge/wiki/maintenance.py`** (new)

```python
def additive_update(article_path, new_extractions, persona, model) -> str:
    """
    Append new evidence to article. Fast path: used when new sources
    confirm existing claims or add new sub-claims without contradiction.
    Returns updated article body.
    """

def revisionary_update(article_path, new_extractions, persona, model) -> str:
    """
    Called when map phase finds a new source that contradicts an existing claim.
    Prompt instructs LLM to:
    - Mark the contradicted claim with ⚠️
    - Present both positions with both citations
    - Move the claim to the Contested zone if it was in Established
    - Do NOT resolve the contradiction — surface it
    Returns updated article body.
    """

def structural_audit(wiki_dir, domain, model) -> StructuralReport:
    """
    Identifies structural issues:
    - Split candidates: WikiArticle with >15 SourceCoverage rows
    - Merge candidates: 2+ articles with >80% overlapping source_ids
    - Deprecation candidates: WikiArticle with zero SourceCoverage rows in
      any Q&A (no query ever touched it) and <3 source_ids
    - Orphan sources: Paper rows with zero SourceCoverage rows across all articles

    Returns StructuralReport with lists of each category.
    """

def detect_contradiction(existing_body, new_extraction) -> bool:
    """
    Cheap check: embed existing Established section + new_extraction,
    compute cosine similarity. If < 0.3 (very dissimilar for same topic),
    flag as potential contradiction and route to revisionary_update.
    Otherwise route to additive_update.
    """
```

Update `wiki sync` in `cli.py` to:
1. For each `needs_update=True` article: run `map_chunks_to_topic` for new sources only
2. For each new extraction: call `detect_contradiction` → route to additive or revisionary
3. After sync: check split/merge thresholds, add to health report if triggered

---

## Phase 7: `wiki audit` CLI Command

**Add to `src/scholarforge/cli.py`:**

```
scholarforge wiki audit [--domain DOMAIN] [--fix]
```

Reports:
1. **Coverage**: sources with zero `SourceCoverage` rows (not referenced anywhere)
2. **Split candidates**: articles with SourceCoverage count > 15
3. **Merge candidates**: article pairs with >80% overlapping source_ids  
4. **Contradiction flags**: articles with ⚠️ in body text (unresolved)
5. **Stale personas**: DomainPersona older than 30 days or corpus has grown >20% since generation
6. **Graph drift**: hub/bridge papers not referenced in any theme/synthesis article
   (new hub identified by graph but wiki hasn't caught up)

`--fix`: automatically queue split/merge candidates as `needs_update=True`
and orphan sources as candidates for a new `wiki expand` run.

Writes `data/wiki/_audit.md`.

---

## Implementation Phases and Agent Assignments

### Step 0 (done before agents): Data model changes
Implement directly in main repo before launching agents:
- `SourceCoverage` + `DomainPersona` tables in `store/models.py`
- `domain` field on `WikiArticle`
- Register both in `store/db.py`
- Commit: "Add SourceCoverage, DomainPersona models; domain field on WikiArticle"

### Agent A: Persona system + map-reduce (Phase 1 + 2)
**Files:** `wiki/persona.py` (new), `wiki/mapreduce.py` (new), update `wiki/agent.py`
**Depends on:** Step 0 (DomainPersona model, SourceCoverage model)
**Tests:** `tests/test_wiki/test_persona.py`, `tests/test_wiki/test_mapreduce.py`

Key implementation notes:
- `get_graph_metrics()` in tools.py returns JSON — parse `hub_papers`, `bridge_papers`,
  `frontier_papers` lists from the result
- The haiku map call MUST use `HAIKU_MODEL = "claude-haiku-4-5-20251001"` explicitly,
  not the settings default
- `map_chunks_to_topic` pre-filter: import `_store` from `store/embeddings.py` and use
  `_store.model.encode([topic_query])` for the query embedding, then query
  `get_chunk_embeddings` or directly query ChromaDB collection
- `reduce_to_article` must prepend `persona_text` as the first line of the system prompt
- After successful reduce: write SourceCoverage rows in a single DB session
- `build_article_from_entry` in `wiki/agent.py`: replace `_fetch_evidence_for_entry`
  with `map_chunks_to_topic` + `reduce_to_article` pipeline
- Keep `build_wiki_article` (used by `wiki expand` fallback) unchanged except add persona

### Agent B: Graph-aware sitemap + hierarchical indexes (Phase 3 + 4)
**Files:** update `wiki/sitemap.py`, update `wiki/builder.py`
**Depends on:** Step 0 (domain field)
**Tests:** `tests/test_wiki/test_sitemap_graph.py`, `tests/test_wiki/test_indexes.py`

Key implementation notes:
- `get_graph_metrics()` returns a string (tool output) — parse JSON from it
- The graph context injected into the exploration agent's system prompt should be
  concise: 10 hubs, 10 bridges, 10 frontiers max
- `generate_multi_domain_sitemap`: domain classification from `Paper.doc_type` +
  `PaperTopic` rows — group papers by their top topic cluster, then LLM names the domain
- For hierarchical index: `generate_theme_index` writes into
  `wiki_dir/domains/{domain}/_index_{theme_slug}.md`
- `generate_library_catalog` reads `_unanswered.jsonl` if it exists; no crash if absent
- `append_unanswered_question` uses file append with JSON lines format

### Agent C: Maintenance + audit + escalation (Phase 5 + 6 + 7)
**Files:** `wiki/maintenance.py` (new), update `cli.py` (wiki sync, wiki audit, wiki query)
**Depends on:** Agent A (map-reduce, SourceCoverage), Agent B (indexes)
**Tests:** `tests/test_wiki/test_maintenance.py`, `tests/test_wiki/test_audit.py`

Key implementation notes:
- `detect_contradiction`: use `_store.model.encode` for cheap cosine check before LLM call
- `revisionary_update` must use the same persona as the original article (look up from
  DomainPersona table by domain field on WikiArticle)
- `wiki audit --fix` should not auto-write articles — it queues them by setting
  `WikiArticle.needs_update=True`; the user runs `wiki sync` to actually process
- `wiki query --deep`: call `generate_sitemap` with `max_explore_papers=15`,
  then `build_wiki_from_sitemap` writing to a `tempfile.mkdtemp()` directory,
  use those articles as the Level 2 context; `--promote` moves temp dir to
  `wiki_dir/queries/`
- Escalation: at Level 3, parse Source Pointers section of the article to find
  which specific source+section to read

---

## What Does NOT Change

- `scholarforge generate` / `evaluate` / `revise` commands — untouched
- All existing retrieval strategies and agent workflows
- The `explore_corpus` function in workflows.py
- The Obsidian vault (still auto-generated from enriched layer, separate from wiki)
- MCP server tools — all remain as-is

---

## Test Coverage Required

Each agent writes tests for their own work. Shared test utilities live in
`tests/test_wiki/conftest.py` (create if needed) with fixtures for:
- `sample_wiki_dir(tmp_path)` — creates a temp wiki dir with 3 pre-written articles
- `sample_sitemap()` — returns a WikiSitemap with 2 themes + 4 concepts
- `mock_graph_metrics()` — returns a sample graph metrics dict

No real LLM or DB calls in tests. Mock `complete`, `get_graph_metrics`,
`find_corpus_gaps`, `find_synthesis_opportunities`, `map_chunks_to_topic`.

---

## Commit Convention

- "Add SourceCoverage and DomainPersona models"
- "Add domain persona generation (wiki/persona.py)"
- "Add map-reduce corpus coverage (wiki/mapreduce.py)"
- "Update article building to use map-reduce + persona"
- "Add graph-aware sitemap generation"
- "Add hierarchical index generation (library catalog + domain + theme)"
- "Add three-tier wiki maintenance"
- "Add wiki audit command and escalation protocol"

---

## Definition of Done

- [ ] All sources in corpus are referenced in at least one wiki article
- [ ] Each domain has a generated persona stored in DB
- [ ] Library catalog + domain indexes + theme indexes all generated correctly
- [ ] `wiki query` escalates through 5 levels before admitting a gap
- [ ] `wiki sync` routes to additive vs. revisionary based on contradiction detection
- [ ] `wiki audit` reports coverage gaps, split/merge candidates, graph drift
- [ ] 394 existing tests still pass
- [ ] New tests cover all new modules
