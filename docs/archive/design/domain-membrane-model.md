# Domain Membrane Model

## Problem Statement

A user ingests a corpus that spans multiple topics: ALD process engineering,
memristive switching physics, neuromorphic computing architecture, and materials
characterization.  The current epoch pipeline treats all concepts as belonging to
a single flat namespace.  This causes three problems:

1. **Noisy extraction.**  Pass 1 asks haiku about every corpus chunk for every
   concept.  In a 500-concept wiki, most chunks are irrelevant to most concepts.
   The embedding pre-filter helps, but domain scoping would eliminate whole
   categories of false positives.

2. **Diluted personas.**  A single domain persona cannot speak authoritatively
   about both semiconductor physics and neural network architecture.  Articles
   sound generic when the persona tries to cover everything.

3. **No cross-domain synthesis.**  The most valuable wiki articles often sit at
   the intersection of two domains (e.g., "ALD-deposited HfO2 as a memristive
   switching layer").  These bridge concepts need material from both domains, but
   the current pipeline has no way to scope a search narrowly and then expand
   deliberately across a boundary.

## Design Principles

**Domains emerge from the graph, not from user input.**  The user should never
have to label their corpus.  Louvain communities on the concept co-occurrence
graph ARE the domains.  The LLM names and validates them; the graph discovers
them.

**Every classification is provisional.**  Community membership, domain labels,
bridge status, and membrane permeability are all re-evaluated each epoch.  A
concept that was peripheral in epoch 1 may become a bridge in epoch 3 as new
sources arrive.  Nothing is pinned.

**No hardcoded thresholds for semantic decisions.**  Numeric thresholds (e.g.,
"top 5 concepts") are brittle.  Where a judgment is semantic ("is this community
coherent?", "should these two communities merge?"), the LLM makes the call.
Where a judgment is statistical ("is this node a bridge?"), relative metrics
(percentiles, ratios) are used instead of absolute cutoffs.

**Let the LLM traverse autonomously.**  The system provides tools (graph metrics,
similarity search, chunk retrieval) but does not prescribe a traversal order.
The LLM decides which sources to read deeper, which communities to explore, and
when to cross a boundary.

**Compartmentalize for speed; permeate for synthesis.**  Intra-domain operations
(persona generation, article writing, index building) are scoped to one community
for focus and cost efficiency.  Cross-domain operations (bridge article writing,
synthesis queries, gap detection) deliberately span boundaries.  The membrane
metaphor: each domain has a semi-permeable boundary that lets related evidence
through while keeping noise out.

---

## Architecture

### Layer 1: Corpus Topology Discovery

After Pass 2 builds the concept co-occurrence graph, a new Pass 2b runs:

```
concept graph (nx.DiGraph)
    |
    v
Louvain community detection  -->  raw partition
    |
    v
Topology metrics  -->  modularity Q, spectral gap, bridge density, Gini
    |
    v
LLM community validation  -->  name, coherence check, merge/split proposals
    |
    v
DomainCluster table (SQLite)  -->  one row per community
    |
    v
ConceptRecord.domains updated (JSON list)
```

#### Community Validation (LLM-vetted)

After Louvain partitions the graph, the system does NOT just name communities
by their highest-degree concepts.  Instead, for each community:

1. **Collect evidence**: all concept names + definitions in the community,
   the top source titles that most frequently mention these concepts, and the
   inter-community edges (what this community connects to).

2. **LLM validation call** (haiku, one call per community):
   - "Here are N concepts grouped by co-occurrence.  Do they form a coherent
     domain?  If yes, provide: (a) a 2-5 word domain label, (b) a one-sentence
     scope statement, (c) which concepts are core vs peripheral to this domain.
     If no, explain what sub-domains you see and propose a split."

3. **Merge check** (haiku, one call per pair of adjacent communities where
   inter-community edge ratio > median):
   - "Community A: {label, scope}.  Community B: {label, scope}.  They share
     these bridge concepts: {list}.  Should they be a single domain?  Return
     MERGE or KEEP-SEPARATE with one sentence of reasoning."

4. **Apply decisions**: merge communities the LLM recommends merging, split
   communities the LLM flags as incoherent.  Write results to `DomainCluster`.

This runs every epoch.  Communities evolve as the corpus grows.

#### Topology Metrics

Computed once per epoch after community detection, stored on `DomainCluster`:

| Metric | Formula | What it tells you |
|--------|---------|-------------------|
| **Modularity Q** | NetworkX `modularity()` | 0-1; how cleanly the corpus separates into topics.  Q > 0.4 = well-compartmentalized. |
| **Inter-community edge ratio** | cross_edges / total_edges | What fraction of concept relationships span domain boundaries.  High = entangled corpus. |
| **Bridge density** | bridge_nodes / total_nodes | How many concepts serve as cross-domain connectors. |
| **Community Gini** | Gini coefficient of community sizes | 0 = equal sizes; 1 = one giant + dust.  High Gini suggests the dominant community should be split. |
| **Spectral gap** | lambda_2 - lambda_1 of graph Laplacian | Large gap = clean separation; small = fuzzy boundaries.  Used to decide if Louvain is giving meaningful communities or if the graph is too connected for compartmentalization. |

These metrics are logged per epoch and displayed on the dashboard.  They also
drive adaptive behavior:

- **Low modularity (Q < 0.3)**: the corpus is too entangled for meaningful
  domains.  Skip compartmentalization entirely; treat all concepts as one domain.
  Log a warning suggesting the user's corpus may be too narrow or too broad.
- **High Gini (> 0.6)**: the dominant community is likely too broad.  The LLM
  split-check for that community is promoted from optional to required.
- **High bridge density (> 0.3)**: many concepts span domains.  Widen the
  membrane permeability (include more cross-domain evidence in article writes).

### Layer 2: Domain Membrane

Each `DomainCluster` defines a semi-permeable boundary around a set of concepts.

**Interior concepts**: high intra-community edges, importance derived mostly from
within-domain sources.  These get a scoped persona and scoped article writes.

**Membrane concepts**: bridge nodes that sit on the boundary between two or more
communities.  They have edges in multiple domains.  Membrane concepts get:
- Multi-domain membership: `ConceptRecord.domains = ["domain_a", "domain_b"]`
- Evidence from all domains they touch (the membrane is permeable for them)
- A composite persona blended from their domains
- Placement in `cross-domain/` wiki directory

**Membrane permeability** is not a single threshold.  It is adaptive per concept
per epoch:

```
For a concept C in domain D, querying evidence from domain D':
    1. C is in D' membership list -> full access (C is a bridge)
    2. C has graph edges to concepts in D' -> include top sources by similarity
    3. C has no connection to D' -> exclude (membrane blocks)
```

This means the membrane's behavior is defined by the graph structure, not by a
tunable parameter.  If the graph says two concepts are related across domains,
evidence flows.  If not, it doesn't.

### Layer 3: Cross-Domain Query Routing

When a user queries the wiki (via `wikify chat`, MCP tools, or the search API):

```
Query
    |
    v
Embed query  -->  cosine similarity to community centroids
    |
    v
Route to top-1 community (fast, focused search)
    |
    v
Check: does the query mention bridge concepts?
    OR: does the top-1 result reference cross-domain concepts?
    OR: is similarity to top-2 community > 0.7 * similarity to top-1?
    |
    yes                              no
    |                                |
    v                                v
Expand to adjacent communities    Return scoped results
via bridge concept paths.
Merge and re-rank evidence
from multiple domains.
    |
    v
Synthesize cross-domain answer
```

**Community centroids**: the mean embedding of all concept definitions in a
community.  Computed once per epoch and cached.  This is cheap (no LLM call) and
gives a fast routing signal.

**Bridge path expansion**: when expanding across domains, don't search the entire
adjacent domain.  Follow the bridge concepts: find concepts in domain D' that are
direct graph neighbors of bridge concepts shared with domain D.  This keeps
expansion targeted.

**The LLM decides synthesis scope.**  The routing logic above proposes candidates.
The LLM (via the existing `map_chunks_to_topic` pipeline) decides which
cross-domain evidence is actually relevant.  The system never forces cross-domain
inclusion — it only widens the candidate pool.  The LLM's YES/NO extraction
gate is the final arbiter.

---

## Data Model Changes

### New: `DomainCluster` (SQLite)

```python
class DomainCluster(SQLModel, table=True):
    id: str = Field(primary_key=True)       # e.g. "cluster_0", or slug of label
    label: str                               # LLM-generated, e.g. "ALD Process Engineering"
    scope: str = ""                          # one-sentence scope statement
    epoch_created: int = 0
    epoch_last_updated: int = 0
    concept_count: int = 0
    core_concept_ids: str = Field(default="[]")  # JSON list of ConceptRecord.id
    bridge_concept_ids: str = Field(default="[]")  # JSON list
    centroid_embedding: str = Field(default="[]")  # JSON list[float], mean of concept embeddings
    modularity_contribution: float = 0.0     # this community's contribution to Q
    persona_text: str = ""                   # community-specific persona
    merged_from: str = Field(default="[]")   # JSON list of previous cluster ids (audit trail)
```

### Modified: `ConceptRecord`

```python
# Add field:
    domains: str = Field(default="[]")  # JSON list of DomainCluster.id values
    # The existing `domain: str` field is kept for backward compat but deprecated.
    # New code reads `domains` (list); old code reads `domain` (first element).
```

### New: `TopologySnapshot` (SQLite)

```python
class TopologySnapshot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    epoch: int = Field(index=True)
    modularity_q: float = 0.0
    inter_community_edge_ratio: float = 0.0
    bridge_density: float = 0.0
    community_gini: float = 0.0
    spectral_gap: float = 0.0
    community_count: int = 0
    total_concepts: int = 0
    total_edges: int = 0
```

---

## Module Design: `wiki/domains.py`

### Public Functions

```python
def discover_domains(
    graph: nx.DiGraph,
    epoch: int,
    model: str | None = None,
) -> list[DomainCluster]:
    """Full domain discovery pipeline for one epoch.

    Steps:
    1. Run Louvain community detection on the graph.
    2. Compute topology metrics (modularity, spectral gap, etc.).
    3. For each community: collect concept evidence and call LLM to validate
       coherence + generate label/scope.
    4. For adjacent community pairs: call LLM to decide merge/keep-separate.
    5. Apply merges/splits.
    6. Assign concepts to domains (multi-membership for bridges).
    7. Generate per-domain personas.
    8. Compute and cache community centroids.
    9. Persist DomainCluster rows and update ConceptRecord.domains.
    10. Persist TopologySnapshot row.

    Returns:
        List of DomainCluster rows written to DB.
    """

def assign_concepts_to_domains(
    communities: dict[str, int],
    roles: dict[str, str],
    clusters: list[DomainCluster],
) -> None:
    """Update ConceptRecord.domains based on community membership + bridge status.

    Interior concepts get a single domain.
    Bridge concepts get all domains they have edges into.
    """

def compute_topology_metrics(
    graph: nx.DiGraph,
    communities: dict[str, int],
) -> TopologySnapshot:
    """Compute all corpus topology metrics for one epoch."""

def validate_community(
    concept_names: list[str],
    definitions: list[str],
    source_titles: list[str],
    model: str,
) -> dict:
    """LLM call to validate a community's coherence.

    Returns: {
        "coherent": bool,
        "label": str,           # 2-5 word domain label
        "scope": str,           # one-sentence scope
        "core_concepts": [...], # concept names the LLM considers core
        "split_proposal": [...] | None  # sub-groups if incoherent
    }
    """

def check_community_merge(
    cluster_a_label: str,
    cluster_a_scope: str,
    cluster_b_label: str,
    cluster_b_scope: str,
    shared_bridges: list[str],
    model: str,
) -> bool:
    """LLM call to decide if two adjacent communities should merge.

    Returns True if the LLM recommends merging.
    """

def get_domain_for_query(
    query_embedding: list[float],
    clusters: list[DomainCluster],
) -> tuple[DomainCluster, list[DomainCluster]]:
    """Route a query to its primary domain + candidate expansion domains.

    Returns:
        (primary_cluster, expansion_candidates)
        where expansion_candidates are adjacent clusters whose centroid
        similarity to the query exceeds 0.7 * primary similarity.
    """

def expand_via_bridges(
    primary_cluster: DomainCluster,
    expansion_clusters: list[DomainCluster],
    graph: nx.DiGraph,
) -> list[str]:
    """Return concept IDs reachable from bridge paths between clusters.

    Follows bridge concepts shared between primary and expansion clusters,
    then returns their direct neighbors in the expansion cluster.
    """
```

---

## Integration with Epoch Pipeline

The epoch pass structure becomes:

```
Pass 1  -- Concept Discovery       (unchanged)
Pass 2  -- Graph Building          (unchanged)
Pass 2b -- Domain Discovery (NEW)
    - discover_domains(graph, epoch)
    - Updates DomainCluster, ConceptRecord.domains, TopologySnapshot
    - Generates per-domain personas
Pass 3  -- Article Writing         (modified: scoped per domain)
    - For each domain: process concepts in that domain
    - For bridge concepts: pull evidence from all domains
    - Article model selection per domain (independent loss per domain, future)
Pass 4  -- Cross-linking           (modified: domain-aware wikilinks)
Pass 5  -- Index Rebuild           (modified: per-domain indexes)
```

### Pass 3 Changes

Currently Pass 3 iterates all concepts in importance order.  With domains:

```python
for cluster in domain_clusters:
    persona = cluster.persona_text
    domain_concepts = [c for c in all_concepts if cluster.id in c.parsed_domains]

    for concept in sorted(domain_concepts, key=lambda c: c.importance, reverse=True):
        if concept is bridge:
            # Widen evidence scope to all domains this concept belongs to
            neighbor_domains = concept.parsed_domains
            extractions = []
            for d in neighbor_domains:
                extractions.extend(map_chunks_to_topic(..., domain=d.label))
        else:
            extractions = map_chunks_to_topic(..., domain=cluster.label)

        # Write article with domain-scoped persona
        write_concept_article(concept, neighbors, cluster.label, model)
```

---

## Local Model Feasibility

Pass 1 concept extraction is the highest-volume LLM call.  Replacing haiku with
a local model would eliminate the dominant cost center.  The requirements:

1. **Structured JSON output**: the model must reliably emit
   `[{name, type, aliases, definition}]`.
2. **Domain vocabulary**: the model must recognize technical terms (ALD, HfO2,
   memristor) without hallucinating definitions.
3. **Context threading**: the model must respect the "previously extracted"
   instruction to avoid duplicates.

### Evaluation Plan

Before committing to a local model, run a benchmark:

1. Select 20 representative chunks spanning all corpus domains.
2. Run haiku extraction on each (ground truth).
3. Run candidate local models (Qwen 2.5 7B, Llama 3.1 8B, Phi-3 mini) on each
   with the same prompt + constrained JSON decoding.
4. Measure:
   - **Precision**: fraction of extracted concepts that are real (not hallucinated)
   - **Recall**: fraction of haiku concepts also found by local model
   - **JSON validity**: fraction of responses that parse without error
   - **Latency**: time per chunk on available hardware
5. Threshold: precision >= 0.85, recall >= 0.75, JSON validity >= 0.95.

If a local model passes, add an `epoch_model` config option:
```python
# config.py
epoch_extraction_model: str = "claude-haiku-4-5-20251001"  # or "ollama/qwen2.5:7b"
```

Since all LLM calls go through `complete_json()` via litellm, switching is a
one-line config change.  Constrained decoding (via `outlines` or `llama.cpp`
grammar) can be added as a litellm custom provider if needed.

This evaluation should happen after the domain pipeline is working, not before.
Correctness first, then cost optimization.

---

## Implementation Order

```
1. DomainCluster + TopologySnapshot models
   + ConceptRecord.domains field + migration
       |
       v
2. wiki/domains.py
   (community validation, merge check, topology metrics,
    concept assignment, centroid computation, query routing)
       |
       v
3. Extend concept_graph.py with topology metric functions
   (modularity, spectral gap, Gini — pure graph math, no LLM)
       |
       v
4. Wire Pass 2b into epoch.py
   + scope Pass 3 per domain
   + update Pass 5 for per-domain indexes
       |
       v
5. Cross-domain query routing
   (extend search_papers / MCP tools with domain-aware routing)
       |
       v
6. Tests for all new code
       |
       v
7. Local model benchmark (separate, after pipeline works)
```

Steps 2 and 3 can run in parallel.  Step 4 depends on both.
Step 5 can start once step 4 is wired.

---

## Relationship to the Adaptive Knowledge Engine Plan

The `docs/design/adaptive-knowledge-engine.md` plan introduces two features that interact
directly with the domain membrane model:

- **Phase 2 (UCB chunk scoring)** will replace the current progressive mining frontier used
  to determine which corpus chunks are processed each epoch. The UCB scorer incorporates
  graph signals (which papers are linked to high-importance concepts) and novelty signals
  (embedding distance to the nearest known concept). The domain membrane's community
  centroids feed naturally into the novelty score, making the two designs complementary.

- **Phase 3 (contradiction-driven exploration)** expands the mining frontier around papers
  involved in detected contradictions. Bridge concepts -- which the domain membrane model
  identifies as spanning multiple communities -- are the most likely sites for contradictions
  (two domains may hold incompatible views on the same concept). Phase 3's citation
  neighborhood expansion will therefore disproportionately benefit cross-domain bridge
  concept detection and resolution.
