# /generate-hierarchical-broad -- Write a paper with broad corpus coverage + hierarchical retrieval

You are a research agent with access to a knowledge base of academic papers. Your job is to explore the corpus, understand the literature, and write a paper based on what you find.

**IMPORTANT: YOU are the LLM.** You call tools via `uv run python -c "..."` to read data from the corpus, then YOU write the review directly as your output. Do NOT look for API keys, do NOT try to call litellm or any external LLM API. The tools read from a local database -- they need no API key. YOU produce the final text.

## Variation Goal

This variant addresses the **narrow corpus problem** identified in hierarchical_v1: ~12 citations vs 54-80 in comparable reviews. The fix: survey 30 papers at digest level, use `get_sections` for cross-corpus conclusions scan, target **40+ unique citations**.

The risk in hierarchical retrieval is that focused reading produces brilliant depth on a small set but misses the field-level landscape. This variation preserves the progressive disclosure efficiency while dramatically expanding coverage.

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
Every read tool has a `reason` parameter. **Always provide it** -- explain in one sentence why you are reading this paper or running this search.

### Read-once-summarize pattern (MANDATORY)
After EVERY `read_section` or `deep_read`, immediately call `record_paper_summary` to distill findings. Set `role` to "hub", "frontier", "bridge", or "standard".

---

## Your Strategy: Broad Coverage + Selective Depth

**Key principle: survey the entire corpus at abstract/digest level first, THEN drill into key sections. You must reach 40+ unique citations to cover the field adequately.**

### Phase 0 -- Reset reading log
```python
from scholarforge.agent.reading_log import reset_reading_log
reset_reading_log()
```

### Phase 1 -- Field-wide survey (EXPANDED: 30 papers)

1. Get the precomputed exploration order -- use **max_papers=30** (not 15):
```python
from scholarforge.agent.tools import get_frontier_exploration_order
print(get_frontier_exploration_order(max_papers=30))
```

2. **Digest ALL 30 papers** via `read_paper_digest`. Section summaries give a structured overview of each paper's contributions in ~1.5KB each. Total: ~45KB for 30 papers.

3. After each digest, call `record_paper_summary` with findings. For hub/seed papers, set `role="hub"`. For frontiers, `role="frontier"`. For bridges, `role="bridge"`.

### Phase 2 -- Cross-corpus conclusions scan

4. After digesting 30 papers, run a cross-corpus conclusions survey to catch papers that ranked below 30 but have important findings:
```python
from scholarforge.agent.tools import get_sections
print(get_sections(section_type="conclusion", reason="Survey conclusions across all papers to catch findings not in top-30"))
```

5. From the conclusions survey, identify any additional papers with findings that should be cited. Add them to your notes.

### Phase 3 -- Targeted section reads (8-15 papers, function-first)

6. Based on the digests and conclusions survey, identify which **specific sections** contain evidence you need. Organize by **function** (what the device does), not by material class:
   - **Switching mechanisms**: which mechanisms explain filament formation, vacancy dynamics, interface effects?
   - **Synaptic functions**: LTP/LTD, STDP, multi-level states, analog linearity
   - **Array integration**: yield, crosstalk, scaling, 3D architecture
   - **Unconventional substrates**: flexible, textile, low-temperature

Use `read_section` to drill into 8-15 sections from 5-10 papers. Each call gives ~5KB of targeted content.

7. After each section read, update `record_paper_summary`.

### Phase 4 -- Gaps and synthesis

8. Run gap and synthesis tools:
```python
from scholarforge.agent.tools import find_corpus_gaps, find_synthesis_opportunities
print(find_corpus_gaps())
print(find_synthesis_opportunities())
```

9. For the 3-5 most important gaps, use `search_papers` to find any papers in the corpus that address those gaps (may not be in your top-30).

### Phase 5 -- Pre-write coverage check

10. **BEFORE writing, verify you have sufficient citations.** Count unique paper names in your session context. If fewer than 35, use `suggest_next_papers` with your already-read list to find missed papers, then digest them.

---

## Write with Function-First Structure

**DO NOT organize by material class (HfO₂ section → Al₂O₃ section → TaOx section).** This creates a catalog, not a synthesis.

**ORGANIZE BY FUNCTION.** Each thematic section should ask: "What does this material or process enable?"

Suggested structure (adapt to content):
1. Introduction: ALD as an enabling technology for memristors; why precision matters
2. Switching mechanisms: what controls filament formation and dissolution (process → mechanism → performance)
3. Synaptic function: which architectures achieve the best linearity, retention, and analog weight precision
4. Array-level integration: from single device to crossbar to 3D (where does ALD uniformity matter most?)
5. Unconventional applications: flexible, bio-integrated, opto-electronic, radiation-hard
6. Gaps and open questions: specific DOE proposals, not field-level truisms
7. Future directions: prioritized research agenda based on impact vs tractability
8. Conclusion

**Within each section, cross-compare materials quantitatively.** If HfO₂ gives 10^8 on/off ratio and TaOx gives 10^4, say so and explain why. The reader needs to choose.

### Word budget (Medium tier)
| Section | Words |
|---------|-------|
| Abstract | 150-200 |
| Introduction | 400 |
| Each thematic section (5-6) | 650-800 |
| Gap analysis | 600 |
| Future directions | 600 |
| Conclusion | 350 |
| **Total** | **~5500-6500** |

### Sentence-type composition
- **30-40% synthesis sentences** -- compare or contrast 2+ papers, draw a conclusion neither paper stated alone
- **30-40% evidence sentences** -- one specific finding from one paper with a citation
- **15-25% analysis sentences** -- your interpretation of what the evidence means
- **5-10% framing sentences** -- transitions, scope statements, context

### Gap identification standard (high bar)

Each gap must be specific enough that a postdoc could act on it Monday morning. Required format:
- **What is missing** (specific, not "benchmarking is lacking")
- **Why it matters quantitatively** (what would we know, and how much better would devices be?)
- **What experiment would fill it** (specific variables: materials, temperature ranges, measurement protocols)

Bad gap: "Standardized benchmarking protocols remain an unmet need."
Good gap: "No published study has swept ALD temperature (150-350°C), precursor (TEMAH, TDMAH, HfCl₄), and doping level (0-10% Al) in a full factorial design on a common 1T1R platform. Such a study would allow direct comparison of process-switching performance tradeoffs across the entire HfO₂ design space."

### Writing rules
- Use `[REF:AuthorName Year - Title]` citation markers matching paper `display_name` values
- Be precise: cite specific numbers, measurements, results
- No bullet points in prose sections
- **ZERO em-dashes or en-dashes as parenthetical separators** (hard ban)
- **Abstracts**: 150-200 words. First sentence <15 words. One concept per sentence. No citations.
- **NEVER mention your exploration method or source counts**
- **No structural scaffolding visible to reader** (no "Known:", "Missing:", "Open Question:" labels in final text)
- **Include 3-5 figure placeholders with detailed captions**

## Loading Context

```python
from scholarforge.agent.defaults import build_generation_prompt
print(build_generation_prompt(artifact_type_id='lit_review', journal='...', field_hint='<topic>'))
```

## Export

```python
from scholarforge.agent.workflows import export_paper
outputs = export_paper(markdown_text, "data/output/benchmark_v2/hierarchical_broad.md", journal="Advanced Functional Materials", docx=True, pdf=True)
```

Then save the reading log:
```python
from scholarforge.agent.tools import save_reading_log
save_reading_log("data/output/benchmark_v2")
```

## Suppress Noise

Append `2>&1 | grep -v "INFO\|WARNING\|Loading\|Batches\|Bert\|UNEXPECTED"` to Python commands to suppress noisy logs.
