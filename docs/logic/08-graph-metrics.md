# Graph Metrics

## What it computes
PageRank, degree centrality, betweenness centrality on the corpus graph. Classifies papers into hub / bridge / frontier roles.

## Graph construction
NetworkX DiGraph with three edge types:
- **Citations** (directed, weight 1.0): A -> B means A cites B
- **Similarity** (bidirectional, weight 0.3): k-NN neighbors from embeddings
- **Coupling** (bidirectional, weight 0.5): shared bibliography references

## Classification rules
- **Hubs**: Top 20% by PageRank. Most influential/connected papers.
- **Bridges**: Top 20% by betweenness centrality, excluding hubs. Connect different research clusters.
- **Frontier/Peripheral**: Bottom 20% by degree centrality. May cover emerging/niche topics.

## How it's used
1. **LLM generation**: Graph metrics summary included in planner prompts. LLM starts with hubs, explores frontiers.
2. **Deep read**: Top 3 hub papers get ALL chunks during retrieval (instead of first 3).
3. **CLI**: `scholarforge graph` displays full ranking table.
4. **MCP**: `get_graph_metrics` tool exposes metrics to external LLMs.

## Design note
Weights are tuned so citations dominate (1.0) > coupling (0.5) > similarity (0.3). This makes PageRank favor papers that are cited AND similar, not just one signal.

## Where the code lives
- `graph/metrics.py`
