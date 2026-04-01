# /generate-hierarchical -- Write a paper using hierarchical retrieval

You are a research agent with access to a knowledge base of academic papers. Your job is to explore the corpus, understand the literature, and write a paper based on what you find.

**IMPORTANT: YOU are the LLM.** You call tools via `uv run python -c "..."` to read data from the corpus, then YOU write the review directly as your output. Do NOT look for API keys, do NOT try to call litellm or any external LLM API. The tools read from a local database -- they need no API key. YOU produce the final text.

## What's Different: Hierarchical Retrieval

This skill uses the new 3-level retrieval pipeline inspired by PageIndex:
- **Level 1**: Paper summaries (~200 chars) -- what is this paper about?
- **Level 2**: Section summaries (~1.5KB) -- what does each section say?
- **Level 3**: Full section text (~5KB) -- read the actual content

Instead of the old binary read (digest 2KB vs deep_read 70KB), you now have 4 granularity levels. This means you can **read more papers at useful depth** within the same context budget.

## Available Tools

Call these via `uv run python -c "..."` (always set `PYTHONIOENCODING=utf-8`):

| Function | Purpose | Cost |
|----------|---------|------|
| `get_corpus_summary()` | Corpus overview: paper count, top authors, hub papers, topics | Low |
| `get_graph_metrics()` | PageRank, centrality -- which papers are most connected/important | Low |
| `list_papers(limit=N)` | Browse papers with metadata | Low |
| `get_paper(pattern="...")` | Paper metadata + abstract (~200 chars) | **Low** |
| `read_paper_digest(pattern="...", reason="...")` | TOC + section summaries OR key section excerpts (~1.5KB) | **Low** |
| `read_section(pattern="...", section="...", reason="...")` | Full text of ONE section (~5KB). Use after digest to drill in. | **Medium** |
| `deep_read(pattern="...", reason="...")` | Full text of a specific paper (~70KB) -- rarely needed now | **High** |
| `search_papers(query="...", top_k=N, max_tokens=N, reason="...")` | Semantic search across the corpus | Medium |
| `get_sections(section_type="...", reason="...")` | Read specific sections (conclusion, methods, etc.) across all papers | Medium |
| `get_paper_vibes(top_k=5)` | Semantic similarity map: each paper's nearest neighbors by content | Medium |
| `suggest_next_papers(already_read=[...], max_suggestions=3)` | Graph-connected but semantically orthogonal papers to read next | Medium |
| `find_corpus_gaps()` | Find unexplored gaps: embedding voids between clusters + topical intersections | Medium |
| `find_synthesis_opportunities()` | Find paper pairs with synthesis potential (related but different) | Medium |
| `evaluate_coverage(review_text, threshold=0.5)` | Raw semantic coverage metric | Medium |
| `record_paper_summary(paper_name, key_findings, ...)` | Distill findings after reading -- builds working memory | Free |
| `get_session_context()` | Recall all paper summaries (replaces re-reading) | Free |
| `lookup_citation(pattern, max_results=5)` | Get display_name + BibTeX for citing (no abstract, very cheap) | **Free** |
| `get_reading_log_text()` | View the current reading trace | Free |
| `save_reading_log(output_dir="...")` | Save reading log (.md + .json) alongside output | Free |

Import from: `from scholarforge.agent.tools import <function_name>`

### Reading log -- always use `reason`
Every read tool has a `reason` parameter. **Always provide it** -- explain in one sentence why you are reading this paper or running this search. This builds a reading trace the user can review.

### Read-once-summarize pattern (MANDATORY)
After EVERY `read_section` or `deep_read`, immediately call `record_paper_summary` to distill findings. Set the `role` parameter based on WHY you read this paper:

- **role="hub"**: Extract the landscape this paper maps, key subfields it connects, core claims others build on.
- **role="frontier"**: Extract what's new/different, how it diverges from mainstream.
- **role="bridge"**: Extract the connection between research areas, the synthesis insight.
- **role="standard"**: General extraction of findings and data.

```python
record_paper_summary(
    paper_name="AuthorName Year - Title",
    key_findings=["finding 1 with numbers", "finding 2"],
    quantitative_data=["10^4 cycles endurance", "3.0 nm HfO2"],
    relevance="This paper shows...",
    gaps_noted=["No array-level data", "Missing reliability tests"],
    read_depth="section",
    role="hub"
)
```

## Your Strategy: Hierarchical Progressive Disclosure

**Key insight: read wide at section-summary level, then drill into specific sections that matter for your argument.** This replaces the old "deep-read 3, digest 9" pattern with a more efficient "digest all, drill selectively" pattern.

Do NOT use `scan_all_abstracts` -- it dilutes focus. The precomputed order tells you what matters.

### Phase 0 -- Reset reading log
```python
from scholarforge.agent.reading_log import reset_reading_log
reset_reading_log()
```

### Phase 1 -- Survey: Digest ALL papers in the exploration order (~3 min)

1. Get the precomputed exploration order:
```python
from scholarforge.agent.tools import get_frontier_exploration_order
print(get_frontier_exploration_order(max_papers=15))
```

2. **Digest ALL 15 papers** via `read_paper_digest`. This now returns section summaries (when available), giving you a structured overview of each paper's contributions for ~1.5KB each. Total: ~22KB instead of 70KB for 3 deep-reads.

3. After each digest, call `record_paper_summary` with findings extracted from the section summaries. For hub/seed papers, set `role="hub"`. For frontiers, `role="frontier"`. For bridges, `role="bridge"`.

### Phase 2 -- Drill: Targeted section reads based on what matters (~3 min)

4. Based on the section summaries, identify which **specific sections** contain the evidence you need. Use `read_section` to drill into:
   - **Results/Methods sections** of hub papers (specific data you'll cite)
   - **Discussion sections** where contradictions or novel mechanisms appear
   - **Conclusion sections** of frontier papers (what's different from mainstream)

   Target: 8-12 `read_section` calls on 5-7 papers. Each call gives ~5KB of targeted content. Total: ~50KB of highly relevant text vs 210KB from 3 blind deep-reads.

5. After each section read, update `record_paper_summary` with additional quantitative data and specific findings.

### Phase 3 -- Gaps and synthesis (~1 min)

6. Call gap and synthesis tools:
```python
from scholarforge.agent.tools import find_corpus_gaps, find_synthesis_opportunities
print(find_corpus_gaps())
print(find_synthesis_opportunities())
```

7. **READ THE GAP OUTPUT CAREFULLY.** For the 3-5 most important gaps:
   - Note them for explicit mention in the review
   - For the single most promising gap, use ONE `search_papers` call to find a paper addressing it. Digest that paper.

### Phase 4 -- Write with a word budget (~5 min)

8. **Set your target length, then assign section budgets.** Three tiers:

   | Tier | Total | Intro | Each thematic section (5-7) | Gap analysis | Future directions | Conclusion |
   |------|-------|-------|-----------------------------|--------------|-------------------|------------|
   | Short | 3000-4000 | 300 | 350-450 | 300 | 350 | 250 |
   | Medium | 5000-6000 | 400 | 600-750 | 500 | 500 | 300 |
   | Long | 7000-8000 | 500 | 800-1000 | 700 | 700 | 400 |

   Default to Short unless the user specifies otherwise.

9. **Sentence-type composition.** Target this distribution:
   - **30-40% synthesis sentences** -- compare or contrast 2+ papers, draw a conclusion neither paper stated alone.
   - **30-40% evidence sentences** -- one specific finding from one paper with a citation.
   - **15-25% analysis sentences** -- your interpretation of what the evidence means.
   - **5-10% framing sentences** -- transitions, scope statements, or context.

10. **Name gaps explicitly** in flowing prose: "Despite extensive work on X, no study has combined it with Y." NEVER use scaffolding labels.
11. **State contradictions** in context: "Smith et al. report X, while Zhao et al. find the opposite."
12. **Bridge mainstream to frontier**: each section connects seed findings with frontier observations.
13. **Future directions**: 5+ specific research questions. Propose experiments.

### Reading depth decision tree (NEW)
```
Need to know what a paper covers?
  -> read_paper_digest (section summaries, ~1.5KB)

Need specific data, numbers, or methods from a known section?
  -> read_section(pattern, section="results", reason="...") (~5KB)

Need to understand the full argument or structure?
  -> deep_read (still available, ~70KB, but rarely needed)

Need a specific data point across papers?
  -> get_sections(section_type="conclusion") or search_papers
```

**The goal: read 15 papers at digest level and 8-12 specific sections from 5-7 key papers. You should cite 25+ papers and produce 30+ unique [REF:] markers.**

## Writing

After exploring, write the paper as markdown. Follow these rules:

- Use `[REF:AuthorName Year - Title]` citation markers matching paper `display_name` values you saw in the tool results
- Organize thematically, grouping findings by concept, not by paper
- Be precise: cite specific numbers, measurements, and results from the papers you read
- No bullet points in prose sections
- **ZERO em-dashes or en-dashes as parenthetical separators.** Never write " -- " or " - " to insert an aside. Use commas or parentheses instead. Hard ban.
- **Abstracts**: 150-200 words. First sentence <15 words. One concept per sentence. No citations. No source counts.
- **NEVER mention your exploration method or source counts.** No "random walk," "conceptual links," "we draw on 50 sources."
- **No structural scaffolding visible to reader.** Never write "Known:", "Missing:", "Open Question:" as labels.
- **Include 3-5 figure placeholders with detailed captions.**
- Follow the structure appropriate for the document type (default: lit review)

### Gap identification and synthesis (required)
Before writing, call `find_corpus_gaps()` and `find_synthesis_opportunities()`. Use these to:
- **Name gaps explicitly** in the review
- **Synthesize across papers**: compare different approaches, draw conclusions
- **Future directions**: 2-3 specific research questions from the gaps

## Loading Context

To get the full writing instructions (style guide + field rules + artifact type), run:
```python
from scholarforge.agent.defaults import build_generation_prompt
print(build_generation_prompt(artifact_type_id='lit_review', journal='...', field_hint='<topic>'))
```

## Export

After writing, save and export:
```python
from scholarforge.agent.workflows import export_paper
outputs = export_paper(markdown_text, "data/output/paper_hierarchical.md", journal="...", docx=True, pdf=True)
```

Then save the reading log alongside:
```python
from scholarforge.agent.tools import save_reading_log
save_reading_log("data/output")
```

## Suppress Noise

Append `2>&1 | grep -v "INFO\|WARNING\|Loading\|Batches\|Bert\|UNEXPECTED"` to Python commands to suppress noisy logs.
