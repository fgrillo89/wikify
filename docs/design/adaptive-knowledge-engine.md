# Adaptive Knowledge Engine

## Motivation

The current epoch pipeline discovers concepts, builds a graph, and writes articles.
But it lacks the feedback loops, adaptive sampling, and unified knowledge
representation that would make it genuinely self-improving. This plan addresses
the five gaps identified by comparing Wikify against recent research (The Discovery
Engine, Schema-Adaptive KGC, MAB-enhanced RAG on Knowledge Graphs).

The goal: after N epochs, the system should have explored the corpus as thoroughly
as a careful human researcher would, surfacing buried concepts, resolving
contradictions, and building a hierarchical taxonomy that is simultaneously
machine-queryable and human-readable.

---

## Part 1: Yield-Based Feedback Loop

**Gap**: The extraction prompt is static. We never learn which chunks yielded good
concepts vs noise, and we never adapt the prompt or the schema.

### 1.1 Track extraction yield per chunk

Add fields to `ChunkMiningLog`:

```python
class ChunkMiningLog(SQLModel, table=True):
    # ... existing fields ...
    concepts_extracted: int = 0     # raw concepts found
    concepts_survived: int = 0      # concepts that passed dedup
    concepts_in_articles: int = 0   # concepts that appeared in a written article
    yield_score: float = 0.0        # survived / extracted (quality ratio)
```

After each epoch's Pass 1, backfill `concepts_extracted`. After Pass 3 (article
writing), backfill `concepts_in_articles`. Compute `yield_score` at epoch end.

### 1.2 Track yield by section type and paper

Aggregate `ChunkMiningLog` yield scores into a per-section-type and per-paper
historical average. This gives the system a learned prior: "methods sections in
this corpus yield 0.8 concepts/chunk on average, while body sections yield 0.3."

Store as a lightweight JSON cache (`data/wiki/_mining_stats.json`) updated after
each epoch.

### 1.3 Adaptive extraction prompt

After epoch 2, append a context block to the haiku extraction prompt:

```
Previously discovered concept types in this corpus (by frequency):
  technique: 45, material: 38, phenomenon: 22, method: 15, theory: 3, dataset: 1

Under-represented types to look for: theory, dataset
Over-extracted types to be selective about: technique, material

Common false positives from this corpus (skip these):
  "experiment", "sample", "device", "film" (too generic)
```

This is populated from the actual ConceptRecord distribution. The prompt evolves
each epoch based on what the system has learned about this specific corpus.

### 1.4 Representational gap reporting

Add a second pass to concept extraction: after haiku extracts concepts, ask it a
follow-up question:

> "Were there ideas, relationships, or phenomena in this text that you could not
> classify into the types [technique|material|phenomenon|method|theory|dataset]?
> If yes, describe what you saw and suggest a type name."

Store gap reports in a new `ExtractionGap` table. After 3+ epochs, if the same
gap pattern recurs across multiple chunks, surface it in `wikify wiki audit` and
consider adding the new type to the schema.

---

## Part 2: UCB-Based Chunk Scoring (Replace Tier System)

**Gap**: Fixed tier system (T0/T1/T2) with flat 5% exploration. No learning from
historical yield. No graph-informed prioritization.

### 2.1 Chunk value model

Replace the tier-based frontier with a UCB1-style scoring function:

```python
def score_chunk(chunk, epoch, graph, mining_stats):
    """Score a chunk for mining priority. Higher = mine first."""

    # Exploitation: how valuable is this chunk likely to be?
    section_yield = mining_stats.avg_yield_by_section.get(chunk.section_type, 0.5)
    paper_yield = mining_stats.avg_yield_by_paper.get(chunk.paper_id, 0.5)

    # Graph signal: papers connected to high-importance concepts score higher
    paper_concepts = get_concepts_from_paper(chunk.paper_id)
    graph_signal = max((graph_importance.get(c, 0) for c in paper_concepts), default=0)

    # Contradiction bonus: papers involved in contradictions are high-value
    contradiction_bonus = 1.0 if chunk.paper_id in papers_with_contradictions else 0.0

    # Novelty: embedding distance to nearest known concept (high distance = novel territory)
    novelty = 1.0 - max_similarity_to_known_concepts(chunk)

    # Exploration: UCB bonus for unvisited/rarely-visited chunks
    visits = times_mined(chunk.id)
    exploration_bonus = sqrt(ln(epoch) / max(visits, 1))

    return (
        0.25 * section_yield
      + 0.15 * paper_yield
      + 0.20 * graph_signal
      + 0.10 * contradiction_bonus
      + 0.15 * novelty
      + 0.15 * exploration_bonus
    )
```

### 2.2 Budget allocation

Each epoch has a token budget (configurable, default: process top 30% of unmined
chunks by score). The scorer ranks all unmined chunks, then processes from highest
score down until the budget is exhausted.

Crucially, the exploration bonus ensures that after enough epochs, every chunk
eventually rises to the top of the queue — guaranteeing full coverage.

### 2.3 Non-stationarity adaptation

After each epoch, the weights in the scoring function are adjusted based on which
factors best predicted actual yield. This is a simple linear regression:

```
actual_yield ~ w1*section_yield + w2*paper_yield + w3*graph_signal + ...
```

Fit on the previous epoch's data. Update weights for the next epoch. This mirrors
the non-stationary MAB approach from the KG-RAG paper: the system adapts as the
graph evolves.

---

## Part 3: Contradiction-Driven Exploration

**Gap**: Contradictions are flagged but don't influence mining priorities.

### 3.1 Contradiction registry

When Pass 3 flags a WARNING (contradiction between sources), record it:

```python
class ContradictionRecord(SQLModel, table=True):
    id: int | None       # PK
    concept_id: str      # which concept has the contradiction
    source_a: str        # Paper.id that asserts claim A
    source_b: str        # Paper.id that asserts claim B
    claim_summary: str   # what they disagree about
    epoch_detected: int
    resolved: bool = False
    resolution: str = "" # how it was resolved (if at all)
```

### 3.2 Citation neighborhood expansion

When a contradiction is detected between papers A and B:
1. Find all papers that cite A or B (from the Citation table)
2. Find all papers with high embedding similarity to A and B
3. Boost the UCB score of chunks in these papers by a contradiction multiplier
4. In the next epoch, these chunks are prioritized for mining

This is the most valuable exploration signal: disagreements in the literature are
exactly where new concepts and nuances hide.

### 3.3 Contradiction resolution tracking

After N epochs, if the contradiction persists (still flagged in the article),
surface it in `wikify wiki audit --contradictions` as an unresolved research
question. If new evidence from the expanded mining resolves it, update the
`ContradictionRecord` and remove the WARNING from the article.

---

## Part 4: Hierarchical Concept Taxonomy

**Gap**: Flat concept list. No parent-child relationships. Dedup at 0.85 may merge
concepts that should be a parent-child pair.

### 4.1 IS-A detection during extraction

Extend the haiku extraction prompt to ask for hierarchical relationships:

> "For each concept, if it is a specific type of a broader concept, indicate the
> parent. Example: 'Plasma-assisted ALD' parent: 'Atomic Layer Deposition'."

Store in a new field on ConceptRecord:

```python
parent_concept_id: str = ""  # FK -> ConceptRecord.id, or "" if top-level
```

### 4.2 Hierarchy-aware dedup

Change the dedup logic: instead of merging concepts with >0.85 similarity, check
if they have a parent-child relationship first:

```python
if similarity > 0.85:
    if is_parent_child(concept_a, concept_b):
        # Keep both — link as parent-child, don't merge
        set_parent(child=more_specific, parent=more_general)
    else:
        # True duplicate — merge as before
        merge(keep=higher_importance)
```

The `is_parent_child` check: if one concept's name is a substring of the other
(e.g., "ALD" in "plasma-assisted ALD"), or if the LLM classified one as a parent,
treat it as hierarchy, not duplication.

### 4.3 Hierarchical article structure

Parent concepts get a "Sub-topics" section in their article that links to children:

```markdown
## Sub-topics

- [[plasma_assisted_ald]] -- uses plasma instead of thermal energy
- [[thermal_ald]] -- standard water/ozone-based process
- [[spatial_ald]] -- atmospheric pressure, roll-to-roll compatible
```

Child concepts get a breadcrumb: `Parent: [[atomic_layer_deposition]]`

### 4.4 Hierarchy-informed graph

The concept co-occurrence graph gets a new edge type: `IS-A` (directed, from child
to parent). This affects PageRank: parent concepts accumulate importance from their
children, naturally rising to the top of the hierarchy.

---

## Part 5: Schema Evolution

**Gap**: Fixed concept types. No way to discover new types that emerge from the corpus.

### 5.1 Gap accumulation

From Part 1.4, the `ExtractionGap` table accumulates cases where haiku couldn't
classify a concept. After 3 epochs, cluster the gap descriptions by embedding
similarity.

### 5.2 Type proposal

For each cluster of 5+ similar gaps, generate a type proposal:

```python
class TypeProposal(SQLModel, table=True):
    id: int | None
    proposed_name: str       # e.g. "process_parameter"
    description: str         # what this type covers
    example_concepts: str    # JSON list of concept names that would fit
    gap_count: int           # how many extraction gaps triggered this
    epoch_proposed: int
    status: str = "proposed" # proposed | accepted | rejected
```

Surface proposals in `wikify wiki audit --schema`. The user can accept/reject.
Accepted types are added to `_VALID_CONCEPT_TYPES` and the extraction prompt.

### 5.3 Automatic acceptance threshold

If a proposed type has 10+ matching gaps across 3+ epochs and the user hasn't
explicitly rejected it, auto-accept it. This allows the schema to evolve without
requiring constant user intervention, while still giving the user veto power.

---

## Part 6: Conceptual Nexus Model (Knowledge Representation)

**Gap**: Concepts, graph, embeddings, and articles are separate systems. No unified
representation that is both machine-queryable and human-inspectable.

### 6.1 Design: Sparse Concept-Relation-Evidence Tensor

Inspired by The Discovery Engine's Conceptual Nexus Tensor, but practical for a
local-first tool. The core representation is a **sparse tensor** with three primary
modes:

```
T[concept_i, concept_j, relation_k] = evidence_strength
```

Where:
- `concept_i`, `concept_j` index into ConceptRecord
- `relation_k` indexes into relation types (IS-A, USED-IN, ENABLES, CONTRASTS-WITH, ...)
- `evidence_strength` is a float combining: co-occurrence count, citation overlap,
  embedding similarity, and number of supporting source passages

Additional contextual modes (stored as metadata, not tensor dimensions):
- Provenance: which papers/chunks support this tensor entry
- Temporal: which epoch established this relationship
- Confidence: how many independent sources confirm it

### 6.2 Storage: Sparse matrix + SQLite metadata

The tensor is too sparse for dense storage. Represent as:
- **ConceptRelation table** (already exists) for non-zero entries
- **Evidence strength** computed on-the-fly from SourceCoverage + graph metrics
- **Cached projections** for fast querying

This means the tensor is not a new data store — it's a **computation layer** over
existing tables. The `ConceptRelation` table IS the sparse tensor; we just add
richer query methods.

### 6.3 Projections (human-readable views)

**Knowledge Graph projection** (already have):
- NetworkX DiGraph in memory, computed per epoch
- Visualized in the D3 dashboard and Obsidian graph view
- Each node = concept, each edge = relation with type and weight

**Semantic Vector projection** (already have):
- ChromaDB embeddings for concept definitions
- Used for similarity search, concept-aware pre-filter, dedup

**Tabular projection** (new):
- For any concept, generate a "Concept Card" — a structured summary:

```json
{
  "concept": "Atomic Layer Deposition",
  "type": "method",
  "importance": 0.87,
  "domain": "ALD Process Engineering",
  "parent": null,
  "children": ["plasma_assisted_ald", "thermal_ald"],
  "enables": ["rram", "high_k_gate_dielectric"],
  "used_in": ["titanium_oxide", "nbo2"],
  "contrasts_with": ["pvd", "cvd"],
  "evidence_sources": 12,
  "contradiction_count": 0,
  "momentum": "stable",
  "last_updated_epoch": 3
}
```

This card is: machine-readable (JSON), human-inspectable (rendered in dashboard
and Obsidian), and derived entirely from the tensor (ConceptRelation + metadata).

### 6.4 Tensor operations for discovery

With the tensor formalized, implement operations that drive new discoveries:

**Gap detection**: Find concept pairs where `T[i, j, *] == 0` for all relation
types, but both concepts have high importance and share sources. These are
"structural holes" — important concepts that should be related but aren't yet
linked. Flag them for targeted extraction in the next epoch.

**Analogy detection**: Find concept pairs (A, B) and (C, D) where the relation
pattern is similar: `T[A, B, :] ~ T[C, D, :]`. These are structural analogies
(e.g., "ALD is to TiO2 as sputtering is to Pt" — same USED-IN relationship
pattern). Surface in articles as "See also: analogous relationship."

**Cluster coherence**: For each domain cluster, compute the density of the
sub-tensor restricted to that cluster's concepts. Low density = the domain is
poorly connected internally, suggesting it should be split or its concepts need
more extraction passes.

---

## Implementation Order

```
Phase 1: Yield tracking + reward signals          (foundations)
    1.1 ChunkMiningLog yield fields
    1.2 Mining stats aggregation
    1.3 Adaptive extraction prompt
    |
    v
Phase 2: UCB chunk scoring                        (replaces tier system)
    2.1 Score function with graph + yield signals
    2.2 Budget allocation
    2.3 Weight adaptation
    |
    v
Phase 3: Contradiction-driven exploration          (high-value signal)
    3.1 ContradictionRecord model
    3.2 Citation neighborhood expansion
    3.3 Resolution tracking
    |
    v
Phase 4: Hierarchical taxonomy                    (structural improvement)
    4.1 IS-A detection in extraction
    4.2 Hierarchy-aware dedup
    4.3 Hierarchical article structure
    4.4 Hierarchy-informed graph
    |
    v
Phase 5: Schema evolution                         (adaptive types)
    5.1 Gap accumulation (from 1.4)
    5.2 Type proposals
    5.3 Auto-acceptance
    |
    v
Phase 6: Conceptual Nexus Model                   (unified representation)
    6.1 Sparse tensor formalization
    6.2 Storage as computation layer
    6.3 Projections (graph, vector, tabular)
    6.4 Tensor operations (gap detection, analogy, coherence)
```

Phases 1-3 build on each other sequentially. Phase 4 can start in parallel with
Phase 3. Phase 5 depends on Phase 1. Phase 6 depends on all previous phases
being stable.

### Estimated effort per phase

| Phase | New code | Modified code | New models | Tests |
|-------|----------|---------------|------------|-------|
| 1     | ~200 LOC | concepts.py, epoch.py | ChunkMiningLog fields | ~15 |
| 2     | ~150 LOC | concepts.py (replace frontier) | none | ~10 |
| 3     | ~200 LOC | epoch.py, concepts.py | ContradictionRecord | ~10 |
| 4     | ~250 LOC | concepts.py, epoch.py, builder.py | ConceptRecord.parent | ~15 |
| 5     | ~200 LOC | concepts.py, cli.py | TypeProposal, ExtractionGap | ~10 |
| 6     | ~300 LOC | new nexus.py, dashboard.py | none (uses existing) | ~15 |

### Token impact

| Phase | Effect on token usage |
|-------|---------------------|
| 1     | +5% (gap reporting adds one haiku call per chunk) |
| 2     | -30% (smarter selection skips more low-value chunks) |
| 3     | +10% (contradiction expansion mines more chunks, but targeted) |
| 4     | +5% (hierarchy detection adds to extraction prompt) |
| 5     | Neutral (schema changes don't affect volume) |
| 6     | -10% (gap detection eliminates redundant extractions) |
| **Net** | **~-20% vs current, with significantly higher quality** |

---

## References

- [The Discovery Engine (2025)](https://arxiv.org/html/2505.17500v1) -- self-consistent
  refinement loop, Conceptual Nexus Tensor, representational gap reporting
- [MAB-Enhanced RAG on Knowledge Graphs (2024)](https://arxiv.org/html/2412.07618v2) --
  multi-armed bandit retrieval selection, non-stationary adaptation, multi-objective reward
- [Schema-Adaptable KGC (EMNLP 2023)](https://aclanthology.org/2023.findings-emnlp.425.pdf) --
  horizontal and vertical schema expansion, dynamic type evolution
- [Chunks as Arms (2025)](https://arxiv.org/abs/2508.13993) -- UCB-guided chunk sampling
  for LLM preference optimization
