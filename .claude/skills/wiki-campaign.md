# /wiki-campaign — Run a directed research campaign

You are the orchestrator of a **research campaign** -- a multi-epoch, thesis-driven investigation that uses the wiki as its working memory.

Unlike undirected wiki epochs (which mine everything equally), a campaign has a **question to answer** and a **thesis to test**. Every extraction, article, and synthesis step is filtered through that lens.

## Commands

- `/wiki-campaign create "question"` — create a new campaign from a research question
- `/wiki-campaign run` — run one opinionated epoch for the active campaign
- `/wiki-campaign status` — show current campaign state, findings, gaps
- `/wiki-campaign synthesize` — produce the final synthesis article

## Creating a Campaign

When the user provides a research question or thesis:

1. **Parse the question** into a thesis statement and 3-5 extraction probes
2. **Create the Campaign record** in the DB
3. **Write the campaign file** at `data/wiki/campaigns/{slug}.md`
4. **Identify relevant papers** via embedding search against the thesis

```python
from wikify.store.db import get_session
from wikify.store.models import Campaign
from wikify.wiki.builder import slugify
import json

campaign = Campaign(
    id=slugify(name),
    name=name,
    thesis=thesis,
    status="investigating",
    extraction_probes=json.dumps(probes),
)
with get_session() as s:
    s.add(campaign)
    s.commit()
```

## Running a Campaign Epoch

A campaign epoch is a **focused** version of the wiki epoch that **builds on the existing wiki**. It reads what's already known, deepens it, and creates new synthetic concepts that emerge from the campaign's cross-cutting question.

### Pass 0: Read Existing Wiki Context

Before extracting anything, **read the wiki articles** relevant to the thesis:

1. Search existing wiki articles by BM25 + embedding against the thesis
2. Read the top 10-20 relevant articles -- these are the campaign's starting context
3. Identify gaps: "What does the wiki already cover? What's missing for our thesis?"
4. Update `campaign.concept_ids` with relevant existing concepts

```python
from wikify.retrieve.bm25 import bm25_search
from wikify.store.db import get_session
from wikify.store.models import ConceptRecord
from pathlib import Path

# Search existing wiki articles for relevance to thesis
wiki_dir = Path("data/wiki")
# Read top articles, summarize what's already known
# Identify what's MISSING -- this drives extraction
```

This means the campaign never re-discovers what the wiki already knows. It starts from the current knowledge frontier.

### Pass 1: Directed Extraction

Extract **only what the wiki doesn't already cover**:
1. **Score chunks by relevance** to the campaign thesis (use BM25 + embedding similarity)
2. **Filter out chunks already mined** by previous epochs
3. **Add campaign probes** to the extraction prompt
4. Focus on gaps identified in Pass 0

The extraction agents get the campaign context in their prompt:

```
You are extracting knowledge to answer this research question:
"{campaign.thesis}"

The wiki already covers these aspects:
{existing_knowledge_summary}

Focus especially on what's MISSING:
{probes}

Extract concepts, parameters, and evidence that help answer this question.
Ignore information unrelated to the thesis.
```

### Pass 2: Graph + Relevance Scoring

Build the concept graph as usual, but also compute **campaign relevance** for each concept:
- How often does this concept co-occur with campaign-relevant concepts?
- Does it appear in chunks that scored high for the thesis?

### Pass 3a: Refine Existing Articles

For concepts that already have wiki articles AND are relevant to the campaign:

- **Read the existing article**
- **Check if campaign evidence adds new information** not in the article
- If yes: **upgrade the article** with campaign-specific evidence, deeper analysis, new parameters
- If no: skip (don't rewrite for no reason)

The upgrade agent gets both the existing article and new evidence:

```
The existing wiki article for "{concept.name}" says:
{existing_article_body}

New evidence from this campaign ("{campaign.thesis}"):
{new_evidence}

Update the article by incorporating the new evidence. Keep the existing
structure. Add new facts, refine existing claims, add new citations.
Do NOT remove existing content unless it contradicts the new evidence.
```

### Pass 3b: Write New Articles

For newly discovered concepts (no existing article), write using the type-specific templates from `article_templates.py`. Campaign context shapes emphasis:

```
You are writing this article as part of a research campaign investigating:
"{campaign.thesis}"

Emphasize aspects of {concept.name} that are relevant to this question.
```

### Pass 3c: Create Synthetic Concepts

This is the campaign-unique step. **Synthetic concepts don't exist in any single paper** but emerge from cross-cutting analysis. The orchestrator (you, deep tier) identifies them:

Examples:
- "ALD-HfO2 Process Window" -- synthesizes growth rate, temperature, and switching data
- "Oxide Composition-Switching Correlation" -- connects material properties to device behavior
- "Neuromorphic Scaling Bottlenecks" -- aggregates limits from different device types

For each synthetic concept:
1. Create a `ConceptRecord` with `concept_type="synthesis"` (a new type)
2. Write the article using a **balanced-tier** agent with all relevant evidence
3. Link it to its constituent concepts via `ConceptRelation` (SYNTHESIZES)
4. Add to `campaign.concept_ids`

Synthetic articles have a different structure:
- ## Thesis -- what this synthesis addresses
- ## Contributing Evidence -- which concepts/papers feed into this
- ## Analysis -- the cross-cutting insight
- ## Implications -- what this means for the field
- ## Sources

### Pass 4-5: Cross-link + Update Campaign + Grow Wiki

After articles are written:
1. Cross-link as usual (new articles get wikilinked into the existing wiki)
2. **Resolve sources** for all new/updated articles
3. **Update the campaign file** with new findings and refined gaps
4. **Assess confidence**: Can we answer the question with what we know?
5. **Identify remaining gaps**: What's still missing?

The wiki is now enriched: existing articles refined, new articles added, synthetic concepts connecting them.

```python
# Update campaign state
campaign.epochs_run += 1
campaign.findings = json.dumps(new_findings)
campaign.open_gaps = json.dumps(remaining_gaps)
campaign.confidence = compute_campaign_confidence(campaign)
campaign.updated_at = datetime.now(timezone.utc)
```

## Campaign Confidence

Confidence is assessed by the orchestrator (you, **deep** tier) after each epoch:

- **0.0-0.3**: We barely understand the question. Need more extraction.
- **0.3-0.6**: We have partial answers but significant gaps remain.
- **0.6-0.8**: We can answer the question but with caveats.
- **0.8-1.0**: Strong answer with evidence from multiple sources.

Assess by asking yourself: "If I had to write a 1-page synthesis answering the thesis right now, how confident would I be in the answer?"

## Synthesis

When confidence >= 0.7 (or the user requests it):

1. **Gather all campaign-relevant articles** from the wiki
2. **Read the evidence** from ConceptEvidence for campaign concepts
3. **Write a synthesis article** (1000-2000 words) that directly answers the thesis
4. Structure: Abstract -> Background -> Evidence For -> Evidence Against -> Synthesis -> Conclusion -> Sources
5. Save to `data/wiki/campaigns/{slug}_synthesis.md`

The synthesis is a **balanced-tier** agent task (needs good writing quality).

## Campaign File Format

Stored at `data/wiki/campaigns/{slug}.md`:

```markdown
---
campaign: slug
status: investigating
confidence: 0.6
epochs_run: 2
---

# Campaign: {name}

## Thesis
{thesis statement}

## Findings (epoch 2)
- Finding 1 [REF:Author Year]
- Finding 2 [REF:Author Year]

## Open Gaps
- Gap 1: what's missing
- Gap 2: what's missing

## Extraction Probes
- "probe question 1"
- "probe question 2"

## Relevant Concepts
- [[concept1]] — why it matters
- [[concept2]] — why it matters
```

This file is human-readable in Obsidian and machine-readable by the orchestrator.
