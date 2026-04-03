# /wiki-epoch — Run one epoch of the wiki-building pipeline

You are the orchestrator of a knowledge synthesis pipeline. Your job is to discover concepts from a corpus of documents, build a concept graph, write wiki articles, and cross-link them.

**IMPORTANT: YOU are the LLM.** You spin up subagents for batch work and call Python tools for DB operations, graph computation, and file I/O. Do NOT look for API keys.

## Cost Tiers

Use cost tiers instead of model names. The orchestrator (you) picks the right tier for each task:

| Tier | When to use | Agent param |
|------|-------------|-------------|
| **fast** | Bulk extraction, classification, yes/no checks | `model: "haiku"` |
| **balanced** | Article writing, synthesis, domain naming | `model: "sonnet"` |
| **deep** | Structural audit, complex reasoning, conflict resolution | (you, the orchestrator) |

- Pass 1 extraction: **fast** (high volume, simple task)
- Pass 2 domain naming: **fast** (short label generation)
- Pass 3 article writing: **balanced** (needs synthesis quality)
- Pass 5 template refinement: **fast** for proposals, **deep** (you) for acceptance decisions

## Pipeline Overview

One epoch = 5 passes executed in order:

```
Pass 1: Concept Discovery    (fast agents extract from chunks in batches)
Pass 2: Graph Building        (Python -- no LLM needed, except domain naming)
Pass 3: Article Writing       (balanced agents write wiki articles in batches)
Pass 4: Cross-Linking         (Python -- no LLM needed)
Pass 5: Index + Loss + Refine (Python + fast agent for template proposals)
```

## Arguments

The user may specify:
- `--path <folder>` — folder of PDFs to build from (default: all corpus papers)
- `--n <N>` — number of epochs to run (default: 1)
- `--domain <name>` — restrict to one domain

## Pass 1: Concept Discovery

### Step 1a: Get paper IDs and chunks

```python
import os, json
from sqlmodel import select
from wikify.store.db import get_session
from wikify.store.models import Paper, Chunk

# Get paper IDs (filter by source_path if --path given)
with get_session() as s:
    papers = list(s.exec(select(Paper).where(Paper.origin == 'corpus')).all())

# If --path specified, filter to papers whose source_path matches
# paper_ids = [p.id for p in papers if os.path.basename(p.source_path) in target_files]

# Get extractable chunks (skip references/acknowledgments, min length 50)
chunks = []
with get_session() as s:
    for pid in paper_ids:
        cs = list(s.exec(select(Chunk).where(Chunk.paper_id == pid).order_by(Chunk.chunk_index)).all())
        for c in cs:
            if c.section_type not in ('references','acknowledgments','appendix') and len(c.content) > 50:
                chunks.append({'id': c.id, 'paper_id': c.paper_id,
                              'content': c.content[:800], 'section_type': c.section_type})
```

### Step 1b: Batch and extract

Split chunks into N batches (10 recommended). Save each batch to a temp JSON file.
Launch N **fast-tier** subagents in parallel, each processing one batch.

Each agent extracts per chunk:
- **concepts**: name, type (technique|material|phenomenon|method|theory|dataset), aliases, definition (max 25 words), evidence (exact quote, max 50 words)
- **parameters**: concept_name, parameter_name, value, unit, conditions
- **gaps**: description, suggested_type (knowledge the template can't classify)

Output format: `{"results": [{"chunk_id": "...", "paper_id": "...", "concepts": [...], "parameters": [...], "gaps": [...]}]}`

### Step 1c: Merge results into DB

```python
from wikify.wiki.concepts import merge_concept_records
from wikify.store.models import ConceptRecord, ConceptEvidence, ParameterExtraction, ExtractionGap
from wikify.wiki.builder import slugify

# Parse all result files into model objects
# merge_concept_records(concept_list, epoch) -- handles dedup by slug + alias
# Store ConceptEvidence rows (with verified=True if evidence found in chunk)
# Store ParameterExtraction rows
# Store ExtractionGap rows
```

### Step 1d: Build co-occurrence relations

```python
from itertools import combinations
from wikify.store.models import ConceptRelation

# For each chunk's extraction, all concept pairs co-occur
# Save ConceptRelation(source, target, 'CO-OCCURS', weight=count, epoch)
# Only save pairs with weight >= 2
```

## Pass 2: Graph Building

Mostly pure Python. Run via `uv run python -c "..."`:

```python
import networkx as nx
from sqlmodel import select
from wikify.store.db import get_session
from wikify.store.models import ConceptRecord, ConceptRelation

# Build graph from ConceptRelation table
G = nx.Graph()
# Add nodes from ConceptRecord, edges from ConceptRelation
# Compute PageRank, update ConceptRecord.importance
```

Domain naming (if needed): spawn one **fast-tier** agent to label community clusters.

## Pass 3: Article Writing

### Step 3a: Prepare article briefs

For each concept needing an article (article_status == "none"), build a brief:
- concept name, type, definition, importance
- neighbor concepts from the graph (top 10)
- extracted parameters (top 5)
- **evidence quotes** with paper display names (top 10)

```python
from wikify.wiki.builder import build_evidence_brief

brief["evidence"] = build_evidence_brief(concept.id, max_evidence=10)
# Returns: [{"paper_id": "...", "paper_display": "Yang 2011 - Dopant Control...",
#            "quote": "exact text from source", "chunk_id": "..."}]
```

The writing agent uses these to write evidence-backed claims with inline `[REF:paper_display]` citations.

### Step 3b: Build type-specific prompts

Each concept gets a **type-adapted template** from `src/wikify/prompts/article_templates.py`:

```python
from wikify.prompts.article_templates import get_article_template, WRITING_RULES

prompt = get_article_template(
    concept_type=concept.concept_type,  # material, technique, phenomenon, etc.
    name=concept.name,
    parameters=brief["parameters"],
    evidence=brief["evidence"],
) + WRITING_RULES
```

This gives each concept type a different article structure (e.g. materials get Properties/Synthesis/Applications, techniques get Mechanism/Process Parameters/Variants). Templates are domain-agnostic.

### Step 3c: Batch and write

Split concepts into batches of 10. Launch **balanced-tier** subagents in parallel.

Each agent receives the type-specific prompt + writing rules for its batch. The structure adapts to the concept type automatically.

Rules: one concept per sentence, no em-dashes, no meta-commentary. Every factual claim must have a `[REF:...]` citation from the evidence provided.

### Step 3c: Save articles and resolve sources

```python
from wikify.wiki.builder import article_path, write_article, generate_parameter_table, resolve_all_article_sources
from pathlib import Path

wiki_dir = Path("data/wiki")
# For each concept + article body:
#   fpath = article_path(wiki_dir, "concepts", concept.id)
#   write_article(fpath, concept.name, body, [], [concept.concept_type], "full", "balanced")
#   Update concept.article_status = "full" and concept.article_path in DB

# After all articles are written, resolve [REF:] markers to paper IDs:
resolve_all_article_sources(wiki_dir)
# This scans all articles, finds [REF:Author Year - Title] markers,
# resolves them to paper IDs, and updates frontmatter sources: field.
```

## Pass 3d: Article Consolidation (orchestrator judgment)

Before cross-linking, review the concept list for merge candidates. This is a **deep-tier** task (you, the orchestrator) because it requires judgment about semantic equivalence.

**When to merge:** Two concepts should be merged when:
- They are synonyms or near-synonyms (e.g. "Pt" and "Platinum", "RRAM" and "Resistive RAM")
- One is a subset of the other (e.g. "Switching" is too generic, merge into "Resistive Switching")
- They cover the same topic from different angles and would be better as one article

**How to merge:**
1. Query concepts with articles: pick the higher-importance concept as the primary
2. Read both articles, combine the best content into the primary article
3. Delete the secondary article file
4. Update the secondary concept's `article_status` to `merged:<primary_id>` in DB
5. Add any unique aliases from the secondary to the primary

```python
from wikify.store.db import get_session
from wikify.store.models import ConceptRecord
import json

# For each merge pair (primary_id, secondary_id):
with get_session() as s:
    secondary = s.get(ConceptRecord, secondary_id)
    primary = s.get(ConceptRecord, primary_id)
    if secondary and primary:
        # Merge aliases
        p_aliases = set(json.loads(primary.aliases or '[]'))
        s_aliases = set(json.loads(secondary.aliases or '[]'))
        p_aliases.add(secondary.name)
        p_aliases |= s_aliases
        primary.aliases = json.dumps(sorted(p_aliases))
        secondary.article_status = f"merged:{primary_id}"
        s.add(primary)
        s.add(secondary)
        s.commit()
# Delete secondary article file
# Rewrite primary article with merged content (balanced-tier agent)
```

**Guideline:** Be conservative. Only merge when clearly redundant. When in doubt, keep separate and add a See Also link instead.

## Pass 4: Cross-Linking

No LLM needed:

```python
from wikify.wiki.linker import cross_link_articles
cross_refs = cross_link_articles(Path("data/wiki"), sitemap=None)
```

## Pass 5: Index + Loss + Refinement

```python
from wikify.wiki.builder import generate_wiki_index, generate_all_domain_condensations
from wikify.wiki.epoch import compute_loss

wiki_dir = Path("data/wiki")
generate_wiki_index(wiki_dir)
generate_all_domain_condensations(wiki_dir)
loss, delta = compute_loss(epoch=N)
```

Template refinement: if gaps exist, spawn one **fast-tier** agent to propose template additions. The orchestrator (you, **deep** tier) decides whether to accept using the overfitting guard.

## After Each Epoch

Report to the user:
- Concepts discovered (new / total)
- Articles written
- Loss score and delta
- Top concepts by importance
- Convergence status

## Multi-Epoch Runs

If `--n > 1`, repeat the pipeline. Each subsequent epoch:
- Extracts from chunks not yet mined (progressive frontier)
- Updates existing concepts with new evidence
- Upgrades stub articles to full
- Tracks loss convergence

Converged when: new concepts < 2%, stub ratio < 10%, loss delta < 0.01.
