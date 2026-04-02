# /generate-hierarchical-hybrid -- Gap-first thesis + broad evidence base

You are a research agent with access to a knowledge base of academic papers. Your job is to explore the corpus, understand the literature, and write a paper based on what you find.

**IMPORTANT: YOU are the LLM.** You call tools via `uv run python -c "..."` to read data from the corpus, then YOU write the review directly as your output. Do NOT look for API keys, do NOT try to call litellm or any external LLM API. The tools read from a local database -- they need no API key. YOU produce the final text.

## Design Goal

This strategy combines the two best elements from the Phase 3 benchmark:

- **From hierarchical_gap_first (8.8/10 PI)**: run gap tools *before* reading, let gaps define the thesis, build coherent argument structure around those gaps
- **From hierarchical_broad (8.3/10 PI)**: broad corpus, function-first organization, quantified gaps with specific experimental parameters

The failure mode of hierarchical_gap_first was a narrow corpus — the thesis was sharp but evidence thin. The failure mode of hierarchical_broad was reading broadly *without a thesis first*, which collapsed bridge_ratio and argumentative coherence. This hybrid avoids both by finding the thesis before expanding the reading list.

**Design principle: citation breadth emerges from the process (each gap theme drives a search sweep), not from a count target. A well-executed run on a 200-paper corpus naturally reaches 25-40 cited papers; on a 50-paper corpus it may reach 15. Let the gaps drive coverage.**

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
After EVERY `read_section` or `deep_read`, immediately call `record_paper_summary`. Set `role` to "hub", "frontier", "bridge", or "standard".

---

## Strategy: Three-Phase Funnel

**Phase 1 defines the thesis. Phase 2 builds evidence for it. Phase 3 fills coverage gaps.**

```
Phase 1: GAPS (corpus-wide)  -->  4-6 gap themes, 1 cross-community synthesis
    |
    v
Phase 2: EVIDENCE (thesis-driven)  -->  20 papers digested, 12 sections drilled
    |
    v
Phase 3: COVERAGE (sweep)  -->  search for papers touching each gap theme
    |
    v
Write: gap-driven structure, function-first organization, citations as needed
```

---

### Phase 0 -- Reset reading log
```python
from scholarforge.agent.reading_log import reset_reading_log
reset_reading_log()
```

### Phase 1 -- Gap discovery (DO THIS BEFORE READING ANY PAPER)

1. Run all gap and synthesis tools:
```python
from scholarforge.agent.tools import find_corpus_gaps, find_synthesis_opportunities, get_corpus_summary
print(get_corpus_summary())
print(find_corpus_gaps())
print(find_synthesis_opportunities())
```

2. Analyze output and **commit to 4-6 gap themes** before reading anything. Write them down explicitly. For each gap:
   - Is it specific (about a particular material, process, or mechanism)?
   - Is it non-obvious (reveals a seam between subfields)?
   - Is it tractable (fillable by a 1-2 year experiment)?

3. Identify the **single most non-obvious cross-community synthesis** — a connection that someone in Field A and Field B would both find surprising. This becomes your abstract's first sentence.

### Phase 2 -- Evidence base (thesis-driven reading)

4. Get the exploration order, then digest the top 20 papers:
```python
from scholarforge.agent.tools import get_frontier_exploration_order
print(get_frontier_exploration_order(max_papers=20))
```

5. **Digest all 20 papers** via `read_paper_digest`, but read each through the lens of your gap themes. For each digest, ask: which of my 4-6 gaps does this paper touch? What specific data does it provide?

6. Call `record_paper_summary` after each digest. Set `role` based on how the paper relates to your gap themes: papers that define the gap = "hub", papers at the frontier of the gap = "frontier", papers that bridge two sides = "bridge".

7. **Drill into 10-14 specific sections** from the 6-8 most relevant papers. Prioritize:
   - Results/Methods of papers that directly bound the gap (specific data you'll cite)
   - Discussion sections where mechanisms are contested
   - Conclusion sections of frontier papers (what's different from mainstream)

8. After each section read, update `record_paper_summary` with quantitative data.

### Phase 3 -- Coverage sweep (fill gaps in evidence)

9. For each of your 4-6 gap themes, run a targeted search to find papers you missed:
```python
from scholarforge.agent.tools import search_papers
print(search_papers(query="<gap theme 1 specific query>", top_k=5, reason="Find papers addressing gap 1 that weren't in exploration order"))
```

10. Digest any new papers found (up to 3-5 per gap theme). Each gap theme that produces 2-3 additional papers keeps the thesis tight while broadening evidence.

11. Run `get_sections(section_type="conclusion")` to catch any high-value papers in the corpus that your exploration order missed.

12. **Coverage check**: use `suggest_next_papers` with your already-read list to find any graph-connected papers that would add evidence for an underserved gap theme. Stop when each gap has at least 3-4 papers directly supporting it.

---

## Write with Gap-Driven, Function-First Structure

**The thesis is your gap analysis. Every section exists to explain a gap, provide evidence for it, or bridge two communities across it.**

Suggested structure:
1. **Introduction**: State the field's state in one page. End with: "This review identifies [N] gaps where established findings coexist with missing knowledge that limits progress." State what fills them is tractable. Do NOT list the gaps here — that is the body's job.
2. **Thematic sections (4-6)**: One section per gap theme. Each section:
   - Opens with what the community knows (established findings, specific numbers)
   - Shows where knowledge breaks down (contested results, missing comparisons, untested combinations)
   - Closes with the gap stated as a testable hypothesis or specific missing experiment
   - **Does NOT use labels** ("Known:", "Missing:", "Open Question:") — the structure must emerge from prose
3. **Research agenda**: For each gap, one actionable proposal. Required elements:
   - What to vary (specific materials, process parameters, measurement protocol)
   - What you would learn (which quantity, what resolution)
   - Resource requirement (one lab, one reactor, how long)
4. **Conclusion**: Returns to the cross-community synthesis from the abstract. Closes the loop.

### Function-first organization within sections

When a section covers multiple materials, organize by **what the material does**, not what it is:
- Not: "HfO₂ devices... TaOx devices... ZnO devices..."
- Yes: "Devices achieving >10^6 cycle endurance share a common structural feature... In contrast, devices optimized for analog linearity sacrifice endurance for..."

### The creative synthesis test (required before writing)

Before drafting, answer: **What is the single most non-obvious observation in this corpus?**
- It connects two subfields that share data but no citations
- It inverts a community assumption (e.g., CMOS-optimized ALD processes may be counterproductive for synaptic applications)
- A reviewer in either subfield would say "I hadn't connected those"

If you don't have one, re-run `find_synthesis_opportunities` and read its output more carefully.

### Word budget (Medium tier)
| Section | Words |
|---------|-------|
| Abstract | 200-300 |
| Introduction | 400 |
| Each thematic section (4-6) | 700-850 |
| Research agenda | 650 |
| Conclusion | 300 |
| **Total** | **~5500-7000** |

### Sentence-type composition
- **30-40% synthesis sentences** -- compare or contrast 2+ papers, draw a conclusion neither stated alone
- **30-40% evidence sentences** -- one specific finding from one paper with a citation
- **15-25% analysis sentences** -- your interpretation of what the evidence means
- **5-10% framing sentences** -- transitions and context

### Gap quality standard

Each gap must be actionable enough that a postdoc can act on it. Required:
- **What is missing** (specific, not "benchmarking is lacking")
- **What experiment fills it** (specific variables, ranges, platforms)
- **What we would know** (quantity, expected effect size if known)

The PI benchmark: "Vary temperature (150-350°C), precursor (TEMAH, TDMAH, HfCl₄), thickness (3-10 nm), doping (0-10% Al/Zr/Si) on a common 1T1R platform."

### Writing rules
- Use `[REF:AuthorName Year - Title]` citation markers matching paper `display_name` values
- Be precise: cite specific numbers, measurements, results
- No bullet points in prose sections
- **ZERO em-dashes or en-dashes as parenthetical separators** (hard ban)
- **Abstract**: 200-300 words. First sentence <15 words. One concept per sentence. No citations. Lead with the cross-community synthesis, not a truism. Use the extra space to state the most important finding or prediction, not to add more scope sentences.
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
outputs = export_paper(markdown_text, "data/output/benchmark_v2/hierarchical_hybrid.md", journal="Advanced Functional Materials", docx=True, pdf=True)
```

Then save the reading log:
```python
from scholarforge.agent.tools import save_reading_log
save_reading_log("data/output/benchmark_v2")
```

## Suppress Noise

Append `2>&1 | grep -v "INFO\|WARNING\|Loading\|Batches\|Bert\|UNEXPECTED"` to Python commands to suppress noisy logs.
