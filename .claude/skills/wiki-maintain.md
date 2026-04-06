# /wiki-maintain — Lint, fix, and enhance the wiki

You are a wiki maintainer. Your job is to find problems, fix them, and proactively enhance the wiki by finding and filling knowledge gaps.

This skill combines four functions:
1. **GC** — garbage collect orphaned DB rows, redirect merged references, clean staging
2. **Lint** — find structural problems (orphans, stale articles, broken links)
3. **Fix** — auto-repair what can be fixed
4. **Enhance** — generate questions the wiki should answer, test if it can, fill gaps

## Phase 0: Garbage Collection

Always run GC first. This fixes referential integrity issues that accumulate across epochs and merges.

```python
from wikify.store.gc import gc_run, integrity_check

# Check health
report = integrity_check()
# Run GC: redirect merged refs, remove orphans, clean staging
result = gc_run()
# Verify clean
assert integrity_check()["orphan_evidence"] == 0
```

## Phase 1: Lint (diagnose)

Scan the wiki for health issues:

```python
from pathlib import Path
from sqlmodel import select
from wikify.store.db import get_session
from wikify.store.models import ConceptRecord, ConceptRelation, ConceptEvidence

wiki_dir = Path("data/wiki")

# 1. Orphan concepts: in DB but no article file on disk
# 2. Ghost articles: file on disk but not in DB
# 3. Broken wikilinks: [[Target]] where target article doesn't exist
# 4. Stale articles: not updated in last N epochs
# 5. Stub articles: status != "full"
# 6. Missing evidence: concepts with zero ConceptEvidence rows
# 7. Disconnected concepts: not linked to any other concept (graph isolates)
# 8. Low-confidence queries: previously answered questions with confidence < 0.5
```

Report findings in a structured summary:

```
Wiki Health Report
  Orphan concepts:      3 (have DB record, no article)
  Ghost articles:       1 (file exists, no DB record)
  Broken wikilinks:    12
  Stale articles:       5 (not updated since epoch 1)
  Missing evidence:     8 concepts with zero source quotes
  Disconnected:         4 concepts with no graph edges
  Low-confidence Qs:    2 in queries/
```

## Phase 2: Fix (auto-repair)

For each category, apply the appropriate fix. Use your judgment on priority.

### Orphan concepts -> write articles
Spawn **balanced-tier** agents to write articles for orphan concepts. Use type-specific templates from `article_templates.py`.

### Ghost articles -> register in DB
Read the article frontmatter, create a `ConceptRecord` from it.

### Broken wikilinks -> fix or create
For each `[[Broken Link]]`:
- If a similar concept exists (fuzzy match), fix the link to point to it
- If no match, create a stub article for the target

### Stale articles -> check for new evidence
Query `ConceptEvidence` and `ParameterExtraction` for the concept. If new evidence exists since the article was last updated, refresh the article with a **balanced-tier** agent.

### Missing evidence -> backfill
For concepts with no evidence, search the corpus chunks for mentions of the concept name. Store any found quotes as `ConceptEvidence`.

### Disconnected concepts -> find links
Query the co-occurrence data to see if the concept appears alongside others. If so, create `ConceptRelation` rows.

### Low-confidence queries -> enhance
Read the low-confidence query articles. Determine what's missing. Either:
- Run a targeted extraction on relevant corpus chunks
- Create a mini-campaign to investigate the question
- Flag for the user if it's outside the corpus scope

## Phase 3: Enhance (self-questioning)

Generate questions the wiki SHOULD be able to answer, test if it can, and fill gaps.

### Step 3a: Generate questions

Based on the wiki's concept graph, generate 10-20 questions that a domain expert would expect the wiki to answer:

```
Given these concepts and their relationships:
  - ALD (technique) -> enables -> RRAM (technique)
  - HfO2 (material) -> used-in -> RRAM
  - Resistive Switching (phenomenon)

Generate questions a researcher would ask this knowledge base:
1. "What ALD parameters affect HfO2 memristor switching?"
2. "How does HfO2 film thickness affect ON/OFF ratio?"
3. "What are the alternatives to HfO2 for RRAM?"
...
```

Use a **fast-tier** agent for question generation.

### Step 3b: Test each question

For each generated question:
1. Search the wiki (BM25 + read top articles)
2. Can it be answered from existing articles? Rate confidence 0-1
3. If confidence < 0.5: this is a gap

### Step 3c: Fill gaps

For each identified gap:
- If the answer exists in the corpus but not in the wiki: run targeted extraction + write article
- If the answer requires cross-cutting synthesis: create a synthetic concept
- If the answer is outside the corpus: log as an unanswerable gap (suggests new papers to ingest)

## When to run

- **After each epoch**: quick lint + fix pass
- **Weekly/scheduled**: full enhance cycle (question generation + gap filling)
- **On demand**: user runs `/wiki-maintain` manually
- **Triggered by queries**: low-confidence answers from `/wiki-ask` feed into the next maintain cycle

## Output

After maintenance, report:

```
Maintenance complete:
  Fixed:     3 orphans (wrote articles)
  Fixed:     12 broken wikilinks
  Fixed:     2 stale articles refreshed
  Enhanced:  Generated 15 questions, 4 gaps found
  Filled:    2 gaps (wrote new articles)
  Flagged:   2 gaps need new papers (outside corpus)
```
