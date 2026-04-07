# Wikify Wikipedia Model

## Core Idea

Given an unstructured corpus (PDFs, notes, web articles, code READMEs), build a
**concept-first, self-correcting Wikipedia** that converges over many epochs.

Unlike the sitemap-first approach (topic -> outline -> articles), this model is
**discovery-driven**: concepts emerge from reading the corpus. The agent doesn't plan
what to write -- it reads and recognises what needs to be written.

---

## Mental Model

```
Corpus (raw)
    |
    v
Epoch 1 -- discover concepts -- write stubs -- cross-reference
    |
    v
Epoch 2 -- discover new concepts -- deepen stubs -- merge near-duplicates
    |
    v
Epoch N -- refine definitions -- resolve contradictions -- fill gaps
    |
    v
Wikipedia (converged)
```

Each epoch is a full pass over the corpus. The wiki improves monotonically.
Epochs can be triggered by:

- User command (`wikify wiki epoch`)
- New files added to corpus (ingest hook)
- Schedule (cron-style, e.g. nightly)

---

## What a Wikipedia Article Contains

Every article is about **one named concept**. The format is fixed:

```markdown
---
concept: Atomic Layer Deposition
aliases: [ALD]
type: technique          # technique | material | phenomenon | method | theory | dataset
related: [HfO2, CVD, conformality, self-limiting reaction]
importance: 0.87         # derived from concept graph degree + corpus frequency
status: full             # stub | draft | full
epoch: 3                 # last modified in epoch N
domain: material_science
---

## Definition

One to three sentences. Standalone -- no assumed context.

## Mechanism

How it works. Quantitative where possible.

## Key Facts

- Fact 1 (cite corpus source inline)
- Fact 2

## In This Corpus

What papers/sources cover this concept and what angle each takes.

## Relationships

| Relation    | Concept            | Note                        |
|-------------|--------------------|-----------------------------|
| IS-A        | CVD variant        | shares precursor chemistry  |
| ENABLES     | HfO2 deposition    | primary deposition route    |
| CONTRASTS   | PVD                | ALD is conformal, PVD isn't |
| USED-IN     | RRAM fabrication   | gate dielectric layer       |

## Open Questions

Questions the corpus leaves unanswered. Drives next-epoch expansion.
```

---

## Epoch Structure

Each epoch runs these passes **in order**:

### Pass 1 -- Discovery (haiku, parallel)

For each corpus source not yet fully mined:
- Feed digest to haiku with prompt: "List every named concept, technique, material, phenomenon, dataset, or method mentioned. Return JSON list of `{name, type, aliases, one_line_definition}`."
- Merge results into `ConceptRecord` table (deduplicate by name + aliases)

Output: updated concept inventory

### Pass 2 -- Graph Construction (local, no LLM)

Build a concept co-occurrence graph:
- Edge weight = how often two concepts appear in the same source/chunk
- Node degree = corpus frequency x source diversity
- Classify: **core** (high degree, many sources), **peripheral** (low degree, few sources), **bridge** (connects disparate domains)

Output: concept importance scores, relationship candidates

### Pass 3 -- Article Writing (sonnet, parallel)

For each concept ranked by importance (core first):
- If no article exists -> write stub or full article depending on evidence volume
- If article is a stub and new evidence exists -> upgrade to draft/full
- If article exists and new evidence contradicts it -> flag with WARNING and note both views

Each article is written using:
- All corpus extractions where this concept appears (from Pass 1)
- Graph neighbors (for the Relationships block)
- Domain persona (for consistent voice)

### Pass 4 -- Cross-Reference (local)

Scan every article for mentions of other known concept names.
Replace plain mentions with `[[wikilinks]]`.
Add backlinks to referenced articles.

### Pass 5 -- Index Rebuild (local)

Regenerate `_index.md` (library catalog), domain indexes, and theme indexes
from current article set. No LLM needed.

After Pass 5 completes, compute the epoch loss score and write it to `EpochLog`
(see Convergence Tracking below).

---

## Convergence Signal

Track per epoch:
- New concepts discovered
- Stubs upgraded to draft/full
- Contradictions flagged
- Cross-references added

When all three of the following hold, the wiki is considered converged:
1. New concepts/epoch < 2% of total concept count
2. Stub ratio < 10%
3. No new contradictions flagged

---

## Convergence Tracking (ML Analogy)

The simple threshold criteria above tell you that the wiki has converged, but not
how fast it is converging or which parts of the corpus are driving residual change.
The following formalisation borrows the vocabulary of supervised learning to give
convergence a scalar, trackable quantity and to prioritise work within each epoch.

### Loss Function

Define a scalar wiki quality score computed once per epoch after Pass 5:

```
L = alpha * stub_ratio
  + beta  * orphan_concept_rate
  + gamma * contradiction_density
  - delta * cross_ref_density
```

where:

- `stub_ratio` = stubs / total_concepts
- `orphan_concept_rate` = concepts with 0 cross-references / total_concepts
- `contradiction_density` = flagged_claims / total_claims
- `cross_ref_density` = total_cross_references / total_articles
- alpha=0.3, beta=0.2, gamma=0.3, delta=0.2 (tunable; stored in project config)

The delta term is negative because higher cross-reference density decreases loss:
a well-linked wiki is a healthier wiki. The coefficients sum to 1.0 when delta is
treated as a positive weight, keeping L in a roughly [0, 1] range.

Convergence is declared when the absolute change across successive epochs falls below
a threshold: `|L(epoch_n) - L(epoch_n-1)| < epsilon`, where the default epsilon is 0.01.
This supplements the three threshold criteria above -- both must be satisfied
simultaneously before `converged` is set to True in `EpochLog`.

`loss_score` and `loss_delta` are stored as new fields on `EpochLog` (see Data Model).

### Information Gradient

For each concept, define a per-epoch gradient that measures how much new evidence has
arrived relative to what is already captured:

```
gradient(concept) = new_evidence_tokens(epoch) / existing_article_tokens
```

`new_evidence_tokens` is the token count of Pass 1 extractions for this concept that
were not present in the previous epoch. `existing_article_tokens` is the current article
body length in tokens. A gradient near 1.0 means the concept is growing as fast as it
is documented; a gradient near 0.0 means the article is stable relative to incoming
evidence.

The gradient is used in Pass 3 to determine evaluation order: concepts are processed
from highest gradient to lowest. Concepts below a minimum gradient threshold (default:
0.05) are skipped unless their article status is still `stub`. This focuses LLM budget
on the parts of the wiki that are actively changing, and skips stable articles where
a rewrite would produce negligible improvement.

### Learning Rate Decay Analog

In early epochs the wiki is thin and the priority is coverage: getting concepts to
at least stub status quickly. In later epochs the priority shifts to depth and accuracy.
This suggests a model-selection schedule analogous to learning rate decay:

- While L >= 0.3: use haiku for article drafting (fast, low cost, adequate for stubs).
- Once L < 0.3: switch to sonnet for deeper rewrites on all high-gradient concepts.

The threshold 0.3 is configurable. The model selected for Pass 3 is recorded in
`EpochLog` so the transition epoch is visible in the run history.

### Momentum

A concept exhibits momentum when it has maintained a high gradient across three or more
consecutive epochs. Persistent high gradient means the corpus keeps delivering new
evidence about this concept faster than articles can absorb it -- the concept is an
active research front. Such concepts are tagged with a `momentum: active` field in their
YAML frontmatter so they are visually distinct in Obsidian and queryable via Dataview.

Conversely, a concept with near-zero gradient across three or more consecutive epochs
is classified as stable. Stable concepts are skipped in Pass 3 unless a new corpus
source explicitly mentions them (detected in Pass 1 by a new extraction row for the
concept). This prevents wasted LLM calls on articles that have genuinely converged.

The epoch count thresholds (3 for both active and stable) are configurable.

### Regularization Analog

An article that is very long relative to how many concepts it cross-references is
likely drawing too heavily from one or two sources, which is the wiki equivalent of
overfitting to training data. The regularization condition is:

```
article_tokens / cross_ref_count > regularization_threshold (default: 500)
```

When this condition holds, the article is flagged in the structural audit output with
a note recommending conciseness review. The flag does not trigger automatic rewriting;
it surfaces in `wikify wiki audit` output and in the dashboard gradient leaderboard.

### Graph and Similarity Leverage

The concept co-occurrence graph built in Pass 2 is used for more than importance
ordering. Three additional analyses run after Pass 2 and influence Pass 3:

**PageRank importance.** Concept importance scores are computed using PageRank over
the concept co-occurrence graph. PageRank is the primary sort key for Pass 3, replacing
the simpler degree-based ordering described in the basic epoch structure above. This
gives higher priority to concepts that are referenced by many other important concepts,
not merely those that appear frequently.

**Embedding pre-filter.** Before running haiku extraction (Pass 1) for a new source,
embed the source digest and compare it against the embedding of each existing article.
Only run extraction for concepts whose article embedding has cosine similarity above 0.4
to the incoming source. This cheap pre-filter eliminates the majority of haiku calls
for large corpora where most sources are relevant to only a fraction of concepts.

**Community detection.** Apply the Louvain algorithm to the concept co-occurrence graph
after Pass 2 to partition concepts into communities. These communities become the wiki's
domain classification, replacing any manually specified domain list. Communities are
re-detected each epoch; concepts that migrate between communities are flagged as
cross-domain and written to `data/wiki/cross-domain/`. This allows the wiki's domain
taxonomy to emerge from the corpus rather than being imposed by the user.

**Bridge concept prioritisation.** Concepts with high betweenness centrality and low
degree are bridge concepts: they connect otherwise isolated clusters but are not
themselves high-frequency. These are the highest-value articles to write first because
they unlock cross-domain cross-references. After the PageRank sort, bridge concepts
are promoted to the top of the Pass 3 queue regardless of their raw importance score.

---

## Data Model

### `ConceptRecord` (SQLite)

```python
class ConceptRecord(SQLModel, table=True):
    id: str              # slugified name, PK
    name: str            # canonical display name
    aliases: str         # JSON list, e.g. ["ALD", "atomic layer dep."]
    concept_type: str    # technique | material | phenomenon | method | theory | dataset
    domain: str          # inferred from source distribution
    importance: float    # 0-1, computed from graph
    epoch_discovered: int
    epoch_last_updated: int
    article_status: str  # none | stub | draft | full
    article_path: str    # relative path to .md file, or ""
```

### `ConceptRelation` (SQLite)

```python
class ConceptRelation(SQLModel, table=True):
    id: int | None       # PK autoincrement
    source_concept: str  # FK -> ConceptRecord.id
    target_concept: str  # FK -> ConceptRecord.id
    relation_type: str   # IS-A | PART-OF | USED-IN | ENABLES | CONTRASTS-WITH
    weight: float        # co-occurrence strength
    epoch: int
```

### `EpochLog` (SQLite)

```python
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

---

## Visualization

The wiki produces two visualization layers: a zero-extra-work Obsidian layer that
works immediately from the generated markdown files, and a planned web dashboard for
quantitative convergence monitoring.

### Tier 1 -- Obsidian Layer

No additional tooling is required. The wiki `.md` files use `[[wikilinks]]` which render
natively in Obsidian's graph view. YAML frontmatter fields (`importance`, `epoch`, `type`,
`status`, `momentum`) enable Dataview plugin queries without any export step.

Each epoch's Pass 5 generates one `_dashboard.md` per domain alongside the domain
index. This file contains Dataview query blocks that produce:

- Stubs by domain, sorted by importance descending (highest-priority stubs first)
- Top 20 concepts by importance score (the core of the domain)
- Concepts updated in the most recent epoch (change summary)
- Concepts flagged as momentum-active (active research fronts)
- Concepts flagged for regularization review (overfit to one source)

Because these are live Dataview queries rather than static snapshots, they update
automatically whenever the wiki articles change, without regenerating the dashboard file.

### Tier 2 -- Web Dashboard

A planned module (`src/wikify/dashboard/`) will provide quantitative monitoring of
convergence progress and corpus coverage. The module exposes a single command:

```
wikify wiki dashboard
```

This launches a local FastAPI server and opens a minimal HTML/JS page in the default
browser. All data is read directly from SQLite -- no LLM calls occur. The page has
six panels:

**Convergence curve.** A Plotly line chart of L (loss score) versus epoch number.
Includes a horizontal reference line at L=0.3 (the haiku-to-sonnet transition threshold)
and a shaded region below epsilon showing the convergence band.

**Concept graph.** A force-directed D3.js graph of the concept co-occurrence network.
Nodes are coloured by article status (stub=red, draft=yellow, full=green) and sized
proportionally to importance score. Bridge concepts are outlined with a dashed border.
The graph is interactive: clicking a node opens the corresponding wiki article in
Obsidian (via the obsidian:// URI scheme).

**Coverage heatmap.** A sources-by-domains grid where each cell shows the percentage
of that source's evidence that has been incorporated into wiki articles, derived from
the `SourceCoverage` table. White cells indicate sources not yet mined for a domain;
dark cells indicate full coverage. This makes corpus blind spots immediately visible.

**Epoch log table.** A sortable table of all `EpochLog` rows, showing per-epoch counts
for concepts discovered, stubs upgraded, contradictions flagged, cross-references added,
L score, loss delta, and wall-clock duration.

**Gradient leaderboard.** A ranked list of the top 20 concepts by information gradient
-- the concepts most in need of update in the next epoch. Each row shows the concept
name, current article status, gradient value, and number of consecutive high-gradient
epochs (momentum indicator).

**Domain health.** A per-domain stacked bar chart showing the ratio of stub, draft,
and full articles within each domain. Domains where stubs dominate are the highest
priority for the next epoch's Pass 3.

Planned dependencies for the dashboard module: `fastapi`, `uvicorn`, `plotly`. The
D3.js graph is loaded from CDN at runtime. The dashboard does not depend on any LLM
client and is safe to run at any time without incurring API costs.

---

## File Layout

```
data/wiki/
  _index.md                    <- library catalog
  _epoch.json                  <- current epoch number + convergence metrics
  _unanswered.jsonl            <- open questions from articles
  domains/
    {domain}/
      _index.md
      _dashboard.md            <- auto-generated Dataview dashboard (per domain)
      concepts/
        {slug}.md              <- one file per concept
      themes/
        {theme_slug}.md        <- optional grouping index
  cross-domain/
    {slug}.md                  <- concepts that span multiple domains
```

---

## CLI

```
# Run one epoch (discovery + articles + cross-ref + index)
wikify wiki epoch

# Run N epochs
wikify wiki epoch --n 5

# Run until convergence
wikify wiki epoch --until-convergence

# Show epoch log
wikify wiki epoch --status

# Schedule epochs (writes a cron entry)
wikify wiki epoch --schedule "0 2 * * *"

# Trigger epoch on next ingest automatically
wikify wiki epoch --on-ingest

# Launch convergence/coverage/graph dashboard
wikify wiki dashboard
```

---

## Relationship to Existing Infrastructure

The existing sitemap-first code (`sitemap.py`, `mapreduce.py`, `maintenance.py`,
`persona.py`, `linker.py`) remains valid and reusable:

| Existing module      | Role in Wikipedia model                              |
|----------------------|------------------------------------------------------|
| `mapreduce.py`       | Pass 3 extraction (map phase per concept)            |
| `persona.py`         | Domain voice, unchanged                              |
| `maintenance.py`     | Contradiction detection + flagging, unchanged        |
| `linker.py`          | Pass 4 cross-reference, extend to use concept index  |
| `builder.py`         | Article I/O helpers, unchanged                       |
| `sitemap.py`         | Optional: user-directed topic focus within an epoch  |

New modules needed:

| New module             | Purpose                                                          |
|------------------------|------------------------------------------------------------------|
| `wiki/concepts.py`     | `ConceptRecord` + haiku discovery pipeline                       |
| `wiki/concept_graph.py`| Relationship extraction + importance scoring (PageRank, Louvain) |
| `wiki/epoch.py`        | Epoch orchestrator (Passes 1-5, loss computation, convergence)   |
| `wiki/article.py`      | Wikipedia-format article writer (concept-aware)                  |
| `wiki/dashboard.py`    | FastAPI app: convergence curve, concept graph, coverage heatmap  |

---

## Relationship to the Adaptive Knowledge Engine Plan

The `docs/design/adaptive-knowledge-engine.md` plan extends the epoch model in three ways
that are directly grounded in this document's design:

- **Phase 1 (yield-based feedback)** makes the Pass 1 extraction prompt adaptive per epoch.
  The static haiku prompt described in the Epoch Structure section becomes context-aware:
  it appends the corpus's actual concept-type distribution and a list of known false positives,
  so the prompt improves with each epoch rather than staying fixed.

- **Phase 4 (hierarchical taxonomy)** adds IS-A parent-child relationships to the concept
  model described in the Data Model section. `ConceptRecord` gains a `parent_concept_id`
  field. The Relationships table in each article gains a Sub-topics section for parent
  concepts. The concept co-occurrence graph described in the Graph and Similarity Leverage
  section gains a new directed IS-A edge type that lets parent concepts accumulate importance
  from their children via PageRank.

- **Phase 6 (Conceptual Nexus Model)** formalizes the tensor representation described
  informally in the Graph and Similarity Leverage section. The co-occurrence graph, embedding
  pre-filter, and community detection are three projections of a single sparse tensor
  `T[concept_i, concept_j, relation_k] = evidence_strength`. Phase 6 adds a query API over
  this tensor for gap detection (missing relations between high-importance concepts), analogy
  detection (similar relation patterns across concept pairs), and cluster coherence scoring.

## Implementation Order

1. **`wiki/concepts.py`** -- `ConceptRecord`, `ConceptRelation`, `EpochLog` models + haiku extraction
2. **`wiki/concept_graph.py`** -- co-occurrence graph, PageRank importance scoring, Louvain community detection, relation classification
3. **`wiki/article.py`** -- Wikipedia-format article writer using concept record + graph neighbors
4. **`wiki/epoch.py`** -- epoch orchestrator, loss function computation, convergence tracking, trigger hooks
5. **CLI** -- `wikify wiki epoch` with flags above
6. **Ingest hook** -- bump epoch counter when new files ingested, optionally auto-trigger epoch
7. **`wiki/dashboard.py`** -- FastAPI dashboard; depends on EpochLog and SourceCoverage being populated
