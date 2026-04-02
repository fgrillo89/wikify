# /generate-hierarchical-gap-first -- Gap-driven hierarchical review with actionable research agenda

You are a research agent with access to a knowledge base of academic papers. Your job is to explore the corpus, understand the literature, and write a paper based on what you find.

**IMPORTANT: YOU are the LLM.** You call tools via `uv run python -c "..."` to read data from the corpus, then YOU write the review directly as your output. Do NOT look for API keys, do NOT try to call litellm or any external LLM API. The tools read from a local database -- they need no API key. YOU produce the final text.

## Variation Goal

This variant addresses **vague gap identification** and **lack of synthetic connections** found in hierarchical_v1. The fix: run gap and synthesis tools **first**, use their output to **drive which papers to read**, and structure the review around **what is missing and why**.

The key insight from PI review: the best review identifies connections that everyone in the field has read separately but no one has synthesized -- like the ALD nucleation literature (decades of gate dielectric work) never being connected to the filament formation literature (memristor community). Gap-first discovery naturally surfaces these seams.

The gold standard for gaps: **specific enough that a postdoc could start Monday.** "Vary temperature (150-350°C), precursor (TEMAH/TDMAH/HfCl₄), thickness (3-10 nm), doping (0-10% Al/Zr/Si) on a common 1T1R test platform."

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
| `find_corpus_gaps()` | Find unexplored gaps: embedding voids between clusters + topical intersections | **START HERE** |
| `find_synthesis_opportunities()` | Find paper pairs with synthesis potential (related but different) | **START HERE** |
| `evaluate_coverage(review_text, threshold=0.5)` | Raw semantic coverage metric | Medium |
| `record_paper_summary(paper_name, key_findings, ...)` | Distill findings after reading -- builds working memory | Free |
| `get_session_context()` | Recall all paper summaries (replaces re-reading) | Free |
| `lookup_citation(pattern, max_results=5)` | Get display_name + BibTeX for citing (no abstract, very cheap) | **Free** |
| `get_reading_log_text()` | View the current reading trace | Free |
| `save_reading_log(output_dir="...")` | Save reading log (.md + .json) alongside output | Free |

Import from: `from scholarforge.agent.tools import <function_name>`

### Read-once-summarize pattern (MANDATORY)
After EVERY `read_section` or `deep_read`, immediately call `record_paper_summary` to distill findings. Set `role` to "hub", "frontier", "bridge", or "standard".

---

## Your Strategy: Gaps Drive Reading, Reading Fills Gaps

**KEY INVERSION: Most reviews read widely first, then notice gaps. This strategy identifies the seams in the literature FIRST, then reads specifically to understand and articulate each gap.**

### Phase 0 -- Reset reading log
```python
from scholarforge.agent.reading_log import reset_reading_log
reset_reading_log()
```

### Phase 1 -- Gap discovery FIRST (before any deep reading)

1. Run gap and synthesis tools immediately:
```python
from scholarforge.agent.tools import find_corpus_gaps, find_synthesis_opportunities, get_corpus_summary
print(get_corpus_summary())
print(find_corpus_gaps())
print(find_synthesis_opportunities())
```

2. **Analyze the gap output carefully.** Identify 4-6 gaps that are:
   - **Specific**: about a particular material, process parameter, or mechanism
   - **Important**: would change how the community designs devices or experiments
   - **Tractable**: could be filled by a 1-2 year research program
   - **Non-obvious**: not "benchmarking is lacking" -- something that reveals a seam between subfields

3. **List your 4-6 gaps explicitly** before reading anything. These become the skeleton of your review.

### Phase 2 -- Landscape: Hub papers define the known territory

4. Get the exploration order and digest the top hub/seed papers (top 15):
```python
from scholarforge.agent.tools import get_frontier_exploration_order
print(get_frontier_exploration_order(max_papers=15))
```

5. **Digest the top 15 papers** via `read_paper_digest`. For each paper, assess: which of your 4-6 gaps does this paper touch? What does it reveal about the gap?

6. Call `record_paper_summary` after each digest with `role="hub"`, `role="frontier"`, or `role="bridge"` as appropriate.

### Phase 3 -- Gap-targeted reading (targeted, not exhaustive)

7. For EACH of your 4-6 identified gaps:
   - Search for papers on both sides of the gap:
     ```python
     from scholarforge.agent.tools import search_papers
     print(search_papers(query="<gap-related query>", top_k=5, reason="Find papers that approach gap X from side A"))
     print(search_papers(query="<adjacent field query>", top_k=5, reason="Find papers from adjacent field that should connect to gap X"))
     ```
   - Digest any new papers found (if not already digested)
   - For the 1-2 most important papers per gap, drill into the specific relevant section:
     ```python
     from scholarforge.agent.tools import read_section
     print(read_section(pattern="...", section="results", reason="Get specific data on <gap parameter>"))
     ```

8. Target: 3-5 `search_papers` queries + 6-10 `read_section` calls total. The gap analysis drives what you need.

### Phase 4 -- Cross-community connections (the synthesis)

9. For each gap you identified, explicitly ask: **what would a researcher from an adjacent field contribute here?**

   The example from the best previous review: ALD nucleation physics (gate dielectric community) has never been connected to filament formation physics (memristor community) -- this is a field-level blind spot.

   Use `get_paper_vibes` and `find_synthesis_opportunities` to find papers from adjacent subfields:
   ```python
   from scholarforge.agent.tools import get_paper_vibes, suggest_next_papers
   print(get_paper_vibes(top_k=5))
   ```

10. **Document the connection explicitly**: "Community A knows X. Community B knows Y. The synthesis of X+Y would enable Z. No paper has done this."

### Phase 5 -- Coverage sweep (catch critical missed papers)

11. Survey conclusions across the corpus for any papers touching your gaps that weren't in top-15:
```python
from scholarforge.agent.tools import get_sections
print(get_sections(section_type="conclusion", reason="Catch any papers addressing my identified gaps that weren't in exploration order"))
```

---

## Write with Gap-Driven Structure

**Organize themes around what is missing and why, not around what exists.**

Each thematic section should follow this arc:
1. **What the community knows** (established findings, specific numbers)  
2. **Where the knowledge breaks down** (contradictions, limits, missing comparisons)
3. **What the gap is** (stated as a testable hypothesis or specific experiment)

This is NOT the "Known / Contradictions / Missing / Question" label format -- those labels should NEVER appear in the final text. The structure should emerge naturally through prose.

### Suggested structure
1. Introduction: The state of ALD memristors in 2025/2026 -- what's been achieved, what's still out of reach
2. Theme 1 (your highest-priority gap): What we know, where knowledge breaks down, specific missing experiment
3. Theme 2: Second gap, same structure
4. Theme 3: Cross-community synthesis -- connections that exist in the literature but haven't been made
5. Theme 4 (optional): Unconventional directions -- substrate, application, or mechanism gaps
6. Research agenda: Prioritized proposals for the 4-6 gaps -- near-term (1 lab, 1 year) and long-term (community-level)
7. Conclusion

**The research agenda section is where you earn your score.** A PI reading this should be able to identify a PhD project from it.

### Research agenda format (required structure)

For each gap, specify:
- **The experiment**: what materials, what process parameters, what measurement protocol
- **What we would learn**: what quantity would this study determine?
- **Why now**: what recent development makes this feasible that wasn't available 5 years ago?
- **Resource requirement**: is this one lab, one ALD reactor, one fabrication run?

### Word budget (Medium tier)
| Section | Words |
|---------|-------|
| Abstract | 150-200 |
| Introduction | 400 |
| Each thematic section (4-5) | 700-900 |
| Research agenda | 700 |
| Conclusion | 300 |
| **Total** | **~5000-6500** |

### Sentence-type composition
- **30-40% synthesis sentences** -- compare or contrast 2+ papers, draw a conclusion neither stated alone
- **30-40% evidence sentences** -- one specific finding from one paper with a citation
- **15-25% analysis sentences** -- your interpretation of the evidence
- **5-10% framing sentences** -- transitions and context

### The creative synthesis test

Before writing, ask yourself: **What is the single most non-obvious observation in this corpus?** An observation that:
- Someone in Field A and someone in Field B would both find surprising
- Could not have been generated by reading any single paper
- Would make a reviewer say "I hadn't thought of it that way"

If you don't have one, re-run `find_synthesis_opportunities` and `get_paper_vibes`. The corpus will have at least one such seam. Find it and put it in the abstract.

### Writing rules
- Use `[REF:AuthorName Year - Title]` citation markers matching paper `display_name` values
- Be precise: cite specific numbers, measurements, results
- No bullet points in prose sections
- **ZERO em-dashes or en-dashes as parenthetical separators** (hard ban)
- **Abstracts**: 150-200 words. First sentence <15 words. One concept per sentence. No citations. State the most non-obvious finding.
- **NEVER mention your exploration method or source counts**
- **No structural scaffolding visible to reader**
- **Include 3-5 figure placeholders with detailed captions**

## Loading Context

```python
from scholarforge.agent.defaults import build_generation_prompt
print(build_generation_prompt(artifact_type_id='lit_review', journal='...', field_hint='<topic>'))
```

## Export

```python
from scholarforge.agent.workflows import export_paper
outputs = export_paper(markdown_text, "data/output/benchmark_v2/hierarchical_gap_first.md", journal="Advanced Functional Materials", docx=True, pdf=True)
```

Then save the reading log:
```python
from scholarforge.agent.tools import save_reading_log
save_reading_log("data/output/benchmark_v2")
```

## Suppress Noise

Append `2>&1 | grep -v "INFO\|WARNING\|Loading\|Batches\|Bert\|UNEXPECTED"` to Python commands to suppress noisy logs.
