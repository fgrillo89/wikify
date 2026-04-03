# /wiki-epoch — Run one epoch of the wiki-building pipeline

You are the orchestrator of a knowledge synthesis pipeline. Your job is to discover concepts from a corpus of scientific papers, build a concept graph, write wiki articles, and cross-link them.

**IMPORTANT: YOU are the LLM.** You spin up haiku subagents for batch extraction, and call Python tools for DB operations, graph computation, and file I/O. Do NOT look for API keys.

## Pipeline Overview

One epoch = 5 passes executed in order:

```
Pass 1: Concept Discovery (haiku agents extract from chunks)
Pass 2: Graph Building (Python — no LLM needed)
Pass 3: Article Writing (haiku/sonnet agents write wiki articles)
Pass 4: Cross-Linking (Python — no LLM needed)
Pass 5: Index + Loss (Python — no LLM needed)
```

## Arguments

The user may specify:
- `--path <folder>` — folder of PDFs to build from (default: all corpus papers)
- `--n <N>` — number of epochs to run (default: 1)
- `--domain <name>` — restrict to one domain

## Pass 1: Concept Discovery

### Step 1a: Get paper IDs and chunks

```python
# If --path specified, get paper IDs matching that folder
import os, json
from sqlmodel import select
from wikify.store.db import get_session
from wikify.store.models import Paper, Chunk

# Get paper IDs (filter by source_path if --path given)
with get_session() as s:
    papers = list(s.exec(select(Paper).where(Paper.origin == 'corpus')).all())
paper_ids = [p.id for p in papers]  # or filter by path

# Get extractable chunks
with get_session() as s:
    chunks = []
    for pid in paper_ids:
        cs = list(s.exec(select(Chunk).where(Chunk.paper_id == pid)).all())
        for c in cs:
            if c.section_type not in ('references','acknowledgments','appendix') and len(c.content) > 50:
                chunks.append({'id': c.id, 'paper_id': c.paper_id,
                              'content': c.content[:800], 'section_type': c.section_type})
```

### Step 1b: Batch and extract

Split chunks into N batches (10 recommended). Save each batch to a temp JSON file.
Launch N **haiku** subagents in parallel, each processing one batch.

Each agent prompt:
> Extract named scientific concepts from each chunk. For each concept: name, type (technique|material|phenomenon|method|theory|dataset), aliases, definition (max 25 words), evidence (exact quote, max 50 words). Also extract parameters (concept_name, parameter_name, value, unit, conditions) and gaps (description, suggested_type). Write results to the output file.

### Step 1c: Merge results into DB

```python
from wikify.wiki.concepts import merge_concept_records, store_evidence, store_gaps, store_parameters
from wikify.wiki.builder import slugify
from wikify.store.models import ConceptRecord
import json

# Load all result files, parse concepts into ConceptRecord objects
# Call merge_concept_records(records, epoch)
# Call store_evidence(rich_extractions, epoch)
# Call store_gaps(rich_extractions, epoch)
# Call store_parameters(rich_extractions, epoch)
```

## Pass 2: Graph Building

No LLM needed. Run via Python:

```python
from wikify.wiki.concept_graph import (
    build_concept_graph, score_importance, update_concept_importance,
    classify_node_roles, extract_relations, save_relations
)
from wikify.wiki.domains import discover_domains

graph = build_concept_graph(domain="", epoch=N)
scores = score_importance(graph)
update_concept_importance(scores)
classify_node_roles(graph, scores)
relations = extract_relations(graph, epoch=N)
save_relations(relations, epoch=N)
```

Note: `discover_domains` DOES need an LLM call (haiku) to name clusters. Use a single haiku agent for this.

## Pass 3: Article Writing

### Step 3a: Get concepts needing articles

```python
from wikify.wiki.concepts import list_concepts
concepts = list_concepts(min_importance=0.0)
concepts.sort(key=lambda c: c.importance, reverse=True)
# Filter to those with article_status == "none" or "stub"
```

### Step 3b: Batch and write

Split concepts into batches. Launch haiku agents, each writing articles for a batch.

Each agent prompt:
> Write a Wikipedia-style article for each concept. Use the concept's definition, related concepts (from the graph), and extracted parameters. Structure: ## What Is Known, ## Where the Field Disagrees, ## Open Questions. One concept per sentence. No em-dashes. Max 600 words per article.

### Step 3c: Save articles

```python
from wikify.wiki.builder import article_path, write_article, generate_parameter_table
from pathlib import Path

wiki_dir = Path("data/wiki")
# For each concept + article body:
#   fpath = article_path(wiki_dir, "concepts", concept.id)
#   param_table = generate_parameter_table(concept.id)
#   full_body = body + "\n\n" + param_table if param_table else body
#   write_article(fpath, concept.name, full_body, source_ids, tags, status, model)
```

## Pass 4: Cross-Linking

No LLM needed:

```python
from wikify.wiki.linker import cross_link_articles
cross_refs = cross_link_articles(wiki_dir, sitemap=None)
```

## Pass 5: Index + Loss

No LLM needed:

```python
from wikify.wiki.builder import generate_wiki_index, generate_all_domain_condensations
from wikify.wiki.epoch import compute_loss
from wikify.wiki.template import refine_template

generate_wiki_index(wiki_dir)
generate_all_domain_condensations(wiki_dir)
loss, delta = compute_loss(epoch=N)
```

Template refinement (refine_template) needs LLM — use a single haiku agent if there are gaps to process.

## After Each Epoch

Report to the user:
- Concepts discovered (new / total)
- Articles written / upgraded
- Loss score and delta
- Convergence status

## Convergence

Run multiple epochs if requested. Track loss L across epochs. Converged when:
1. New concepts < 2% of total
2. Stub ratio < 10%
3. No new contradictions
4. Loss delta < 0.01
