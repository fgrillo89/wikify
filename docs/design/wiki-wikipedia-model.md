# ScholarForge Wikipedia Model

## Core Idea

Given an unstructured corpus (PDFs, notes, web articles, code READMEs), build a
**concept-first, self-correcting Wikipedia** that converges over many epochs.

Unlike the sitemap-first approach (topic → outline → articles), this model is
**discovery-driven**: concepts emerge from reading the corpus. The agent doesn't plan
what to write — it reads and recognises what needs to be written.

---

## Mental Model

```
Corpus (raw)
    │
    ▼
Epoch 1 ── discover concepts ── write stubs ── cross-reference
    │
    ▼
Epoch 2 ── discover new concepts ── deepen stubs ── merge near-duplicates
    │
    ▼
Epoch N ── refine definitions ── resolve contradictions ── fill gaps
    │
    ▼
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

One to three sentences. Standalone — no assumed context.

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

### Pass 1 — Discovery (haiku, parallel)

For each corpus source not yet fully mined:
- Feed digest to haiku with prompt: "List every named concept, technique, material, phenomenon, dataset, or method mentioned. Return JSON list of `{name, type, aliases, one_line_definition}`."
- Merge results into `ConceptRecord` table (deduplicate by name + aliases)

Output: updated concept inventory

### Pass 2 — Graph Construction (local, no LLM)

Build a concept co-occurrence graph:
- Edge weight = how often two concepts appear in the same source/chunk
- Node degree = corpus frequency × source diversity
- Classify: **core** (high degree, many sources), **peripheral** (low degree, few sources), **bridge** (connects disparate domains)

Output: concept importance scores, relationship candidates

### Pass 3 — Article Writing (sonnet, parallel)

For each concept ranked by importance (core first):
- If no article exists → write stub or full article depending on evidence volume
- If article is a stub and new evidence exists → upgrade to draft/full
- If article exists and new evidence contradicts it → flag with ⚠️ and note both views

Each article is written using:
- All corpus extractions where this concept appears (from Pass 1)
- Graph neighbors (for the Relationships block)
- Domain persona (for consistent voice)

### Pass 4 — Cross-Reference (local)

Scan every article for mentions of other known concept names.
Replace plain mentions with `[[wikilinks]]`.
Add backlinks to referenced articles.

### Pass 5 — Index Rebuild (local)

Regenerate `_index.md` (library catalog), domain indexes, and theme indexes
from current article set. No LLM needed.

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

## Data Model

### `ConceptRecord` (SQLite)

```python
class ConceptRecord(SQLModel, table=True):
    id: str              # slugified name, PK
    name: str            # canonical display name
    aliases: str         # JSON list, e.g. ["ALD", "atomic layer dep."]
    concept_type: str    # technique | material | phenomenon | method | theory | dataset
    domain: str          # inferred from source distribution
    importance: float    # 0–1, computed from graph
    epoch_discovered: int
    epoch_last_updated: int
    article_status: str  # none | stub | draft | full
    article_path: str    # relative path to .md file, or ""
```

### `ConceptRelation` (SQLite)

```python
class ConceptRelation(SQLModel, table=True):
    id: int | None       # PK autoincrement
    source_concept: str  # FK → ConceptRecord.id
    target_concept: str  # FK → ConceptRecord.id
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
```

---

## File Layout

```
data/wiki/
  _index.md                    ← library catalog
  _epoch.json                  ← current epoch number + convergence metrics
  _unanswered.jsonl            ← open questions from articles
  domains/
    {domain}/
      _index.md
      concepts/
        {slug}.md              ← one file per concept
      themes/
        {theme_slug}.md        ← optional grouping index
  cross-domain/
    {slug}.md                  ← concepts that span multiple domains
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
```

---

## Relationship to Existing Infrastructure

The existing sitemap-first code (`sitemap.py`, `mapreduce.py`, `maintenance.py`,
`persona.py`, `linker.py`) remains valid and reusable:

| Existing module      | Role in Wikipedia model                              |
|----------------------|------------------------------------------------------|
| `mapreduce.py`       | Pass 3 extraction (map phase per concept)            |
| `persona.py`         | Domain voice, unchanged                              |
| `maintenance.py`     | Contradiction detection + ⚠️ flagging, unchanged    |
| `linker.py`          | Pass 4 cross-reference, extend to use concept index  |
| `builder.py`         | Article I/O helpers, unchanged                       |
| `sitemap.py`         | Optional: user-directed topic focus within an epoch  |

New modules needed:

| New module             | Purpose                                          |
|------------------------|--------------------------------------------------|
| `wiki/concepts.py`     | `ConceptRecord` + haiku discovery pipeline       |
| `wiki/concept_graph.py`| Relationship extraction + importance scoring     |
| `wiki/epoch.py`        | Epoch orchestrator (Passes 1–5)                  |
| `wiki/article.py`      | Wikipedia-format article writer (concept-aware)  |

---

## Implementation Order

1. **`wiki/concepts.py`** — `ConceptRecord`, `ConceptRelation`, `EpochLog` models + haiku extraction
2. **`wiki/concept_graph.py`** — co-occurrence graph, importance scoring, relation classification
3. **`wiki/article.py`** — Wikipedia-format article writer using concept record + graph neighbors
4. **`wiki/epoch.py`** — epoch orchestrator, convergence tracking, trigger hooks
5. **CLI** — `wikify wiki epoch` with flags above
6. **Ingest hook** — bump epoch counter when new files ingested, optionally auto-trigger epoch
