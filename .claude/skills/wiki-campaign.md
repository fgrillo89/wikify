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

A campaign epoch is a **focused** version of the wiki epoch. The key difference: everything is filtered by relevance to the thesis.

### Pass 1: Directed Extraction

Instead of extracting from all chunks equally:
1. **Score chunks by relevance** to the campaign thesis (use BM25 + embedding similarity)
2. **Only extract from top-scoring chunks** (e.g. top 200 most relevant)
3. **Add campaign probes** to the extraction prompt: "In particular, look for: [probe1], [probe2], ..."
4. **Tag extracted concepts** with the campaign ID

The extraction agents get the campaign context in their prompt:

```
You are extracting knowledge to answer this research question:
"{campaign.thesis}"

Focus especially on:
{probes}

Extract concepts, parameters, and evidence that help answer this question.
Ignore information unrelated to the thesis.
```

### Pass 2: Graph + Relevance Scoring

Build the concept graph as usual, but also compute **campaign relevance** for each concept:
- How often does this concept co-occur with campaign-relevant concepts?
- Does it appear in chunks that scored high for the thesis?

### Pass 3: Opinionated Article Writing

Article writing agents get the campaign context:

```
You are writing this article as part of a research campaign investigating:
"{campaign.thesis}"

Emphasize aspects of {concept.name} that are relevant to this question.
If this concept is tangential to the thesis, keep the article short (200 words).
If it is central, write a thorough article (500 words) with emphasis on
how it relates to the thesis.
```

### Pass 4-5: Cross-link + Update Campaign

After articles are written:
1. Cross-link as usual
2. **Update the campaign file** with new findings and refined gaps
3. **Assess confidence**: Can we answer the question with what we know?
4. **Identify remaining gaps**: What's still missing?

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
