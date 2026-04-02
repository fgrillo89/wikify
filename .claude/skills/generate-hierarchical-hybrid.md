# /generate-hierarchical-hybrid -- Gap-first thesis + broad evidence base

You are a research agent with access to a knowledge base of academic papers. Your job is to explore the corpus, understand the literature, and write a paper based on what you find.

**IMPORTANT: YOU are the LLM.** You call tools via `uv run python -c "..."` to read data from the corpus, then YOU write the review directly as your output. Do NOT look for API keys, do NOT try to call litellm or any external LLM API. The tools read from a local database -- they need no API key. YOU produce the final text.

## Design Goal

Find the thesis before reading any paper. Then read enough to support it — no more.

The failure mode of reading-first strategies is that the review centroid drifts toward the corpus center, collapsing bridge connections and frontier reach. The failure mode of gap-first without follow-up is a sharp thesis with thin evidence. This strategy avoids both:

1. **Gaps first** — commit to the thesis before opening a single paper
2. **Thesis-driven reading** — every paper read exists to serve a specific gap
3. **Coverage sweep** — for each gap that's under-evidenced, search specifically for it

Citation breadth emerges from the process. Let the gaps drive how many papers you read.

## Available Tools

Call these via `uv run python -c "..."` (always set `PYTHONIOENCODING=utf-8`):

| Tool | Purpose | When to use |
|------|---------|-------------|
| `find_corpus_gaps()` | Divergent papers: shared citations, different conclusions; embedding voids between clusters | **Phase 1: start here** |
| `find_synthesis_opportunities()` | Paper pairs with moderate similarity — related approaches that haven't been connected | **Phase 1: start here** |
| `get_corpus_summary()` | Paper count, year range, top authors, hub/bridge/frontier papers, topic vocabulary | Phase 1: orient |
| `get_frontier_exploration_order(max_papers=N)` | Recommended reading order: greedy seeds + frontier + bridge papers | Phase 2: reading order |
| `read_paper_digest(pattern, reason)` | TOC + section summaries (~1.5KB). Best first pass for any paper. | Phase 2: survey |
| `read_section(pattern, section, reason)` | Full text of ONE section (~5KB). Use when digest isn't specific enough. | Phase 2: drill |
| `record_paper_summary(paper_name, key_findings, ...)` | Distill findings into working memory. Call after every read. | After every read |
| `get_session_context()` | Recall all recorded paper summaries without re-reading. | Before writing |
| `search_papers(query, top_k, reason)` | Semantic search across corpus. Best for targeted gap evidence. | Phase 3: sweep |
| `suggest_next_papers(already_read, max_suggestions)` | Graph-connected + semantically orthogonal papers not yet read. | Phase 3: gaps in evidence |
| `lookup_citation(pattern, max_results)` | Get display_name + BibTeX key for citing. Free. | While writing |
| `save_reading_log(output_dir)` | Save reading trace alongside output. | After writing |
| `deep_read(pattern, reason)` | Full paper (~70KB). Rarely needed — prefer read_section. | Last resort only |

Note on similar tools:
- `find_corpus_gaps` finds *divergence* (papers that disagree or occupy separate embedding regions)
- `find_synthesis_opportunities` finds *convergence* (related papers that haven't been connected)
- `search_papers` is query-driven; `suggest_next_papers` is graph-driven (citation proximity + semantic orthogonality)

Import from: `from scholarforge.agent.tools import <function_name>`

### Read-once-summarize (MANDATORY)
After every `read_paper_digest`, `read_section`, or `deep_read`, call `record_paper_summary`. Set `role` to "hub", "frontier", "bridge", or "standard" based on how the paper relates to your gap themes.

---

## Strategy

```
Phase 1: GAPS    -->  thesis before any reading
    |
Phase 2: READ    -->  enough papers to support each gap; stop on diminishing returns
    |
Phase 3: SWEEP   -->  for each under-evidenced gap, search specifically for it
    |
Write            -->  gap-driven structure, function-first organization
```

### Phase 0 -- Reset reading log
```python
from scholarforge.agent.reading_log import reset_reading_log
reset_reading_log()
```

### Phase 1 -- Gaps first (before reading any paper)

1. Orient, then discover gaps:
```python
from scholarforge.agent.tools import get_corpus_summary, find_corpus_gaps, find_synthesis_opportunities
print(get_corpus_summary())
print(find_corpus_gaps())
print(find_synthesis_opportunities())
```

2. **Commit to your gap themes before reading anything.** Write them down. Each gap must be:
   - **Specific** — about a particular material, process parameter, or mechanism (not "more benchmarking is needed")
   - **Non-obvious** — reveals a seam between subfields that neither community has named
   - **Tractable** — fillable by a 1-2 year experimental program

   Find enough distinct themes to organize the review — typically 4-6, but let the corpus decide. If gaps cluster naturally into 3 topics, use 3. If 7, use 7.

3. Identify the **single most non-obvious inter-field connection**: a finding that someone in Field A and someone in Field B would each find surprising. State it as an observation about what the literature shows, not as a description of your synthesis process. This becomes the abstract's **second** sentence (sentence 1 sets context for non-specialists; sentence 2 delivers the surprise).

### Phase 2 -- Thesis-driven reading

4. Get the exploration order and begin digesting:
```python
from scholarforge.agent.tools import get_frontier_exploration_order
print(get_frontier_exploration_order(max_papers=25))
```

   Digest papers in the recommended order via `read_paper_digest`. For each paper, ask: which gap theme does this support? What specific data does it provide?

   **Stop when each gap theme has at least 2-3 papers bounding it and the new digests stop adding evidence you don't already have.** Don't read to a count — read to coverage.

5. After each digest, call `record_paper_summary` with `role` based on the paper's relationship to your gap themes.

6. **Drill into specific sections** with `read_section` where you need actual numbers, mechanisms, or contested claims — not just what the section is about. Drill when the section summary isn't specific enough for your argument. Stop drilling when you have the data, not when you've hit a count.

### Phase 3 -- Evidence sweep

7. For each gap theme that's under-evidenced (missing a mechanistic paper, missing quantitative data, or missing a cross-community reference), run a targeted search:
```python
from scholarforge.agent.tools import search_papers
print(search_papers(query="<specific gap query>", top_k=5, reason="<which gap this serves>"))
```

   Digest any new papers found. Stop when the gap feels adequately supported — not by count, but by whether you could write the gap paragraph with specific numbers and traceable claims.

8. For any gap still thin after searching, use graph-driven discovery:
```python
from scholarforge.agent.tools import suggest_next_papers
print(suggest_next_papers(already_read=[...], max_suggestions=3))
```

### Phase 4 -- Devil's advocate (challenge your framing)

This phase prevents the gap-first strategy from terminating before it encounters results that challenge its own framing.

9. For each gap theme you've committed to, ask: **What result would make this gap less important or already solved?** Then search for it explicitly:
```python
print(search_papers(query="<result that would undermine gap X>", top_k=5, reason="Challenge my framing of gap X"))
```

   If you find papers that partially address a gap you thought was open, update your gap formulation — either tighten the scope ("X is addressed for binary switching but not for analog") or replace it.

10. Ask: **What important territory does my current corpus not cover?** Use the corpus itself to find your blind spots — don't rely on domain intuition:
```python
print(find_synthesis_opportunities())
```
    Review the output: any synthesis opportunity that the tool identifies but that your current gap themes don't address is a potential blind spot. For each such territory, run one targeted search and digest any key papers found. Stop when you can answer: "I have read the papers that would most challenge my framing."

---

## Write with Gap-Driven, Function-First Structure

**Every section exists to explain a gap, build evidence for it, and close with the specific missing experiment.**

Suggested structure:
1. **Introduction**: State the field's current capabilities and limits. End by signaling that the review identifies specific gaps where progress is tractable. Do NOT list the gaps here — that is the body's job.
2. **Thematic sections**: One section per gap theme. Each opens with what the community knows (established findings, real numbers), shows where knowledge breaks down (contested results, missing comparisons), and closes with the gap as a testable hypothesis or specific missing experiment. Labels like "Known:", "Missing:", "Open Question:" must never appear — the structure must emerge from prose.
3. **Research agenda**: One actionable proposal per gap. Required elements per proposal:
   - What to vary (specific materials, process parameters, measurement protocol)
   - What you would learn (which quantity, what expected resolution)
   - Resource requirement (one lab, one reactor, approximate duration)
4. **Conclusion**: Returns to the inter-field observation introduced in the abstract. Closes the loop by showing what has changed in the reader's understanding from the beginning to the end.

### Function-first within sections

Organize by what materials *do*, not what they *are*:
- Not: "HfO₂ section → TaOx section → ZnO section"
- Yes: "Devices achieving >10^6-cycle endurance share a common structural feature... Devices optimized for analog linearity face a different constraint..."

### Pre-writing checklist (required before drafting)

Before writing the first sentence, answer both questions explicitly:

**1. The creative synthesis test**

What is the single most non-obvious observation in this corpus?
- It connects two subfields that share data but no citations
- It inverts a community assumption
- A reviewer in either subfield would say "I hadn't connected those"

State it as a fact about the world, not as a description of what this review does. BAD: "This review identifies a connection between X and Y communities." GOOD: "The precision engineers use to eliminate X is the same precision biologists need to create it."

If you don't have one, reread `find_synthesis_opportunities` output. The corpus almost always has at least one such seam.

**2. The falsifiable prediction (required)**

State one quantitative prediction from the evidence you have read:

> "If [specific variable or process parameter], then [specific outcome metric] changes by [magnitude or direction] because [mechanistic reason], and this prediction is falsifiable by [the specific experiment in the research agenda]."

This sentence must appear in the research agenda section. It distinguishes a review that generates hypotheses from one that merely summarizes findings. If the evidence does not support a quantitative estimate, a directional prediction with a stated mechanism is acceptable. Do not write the review until you have this sentence.

### Word budget
| Section | Words |
|---------|-------|
| Abstract | 200-300 |
| Introduction | 400 |
| Each thematic section | 700-900 |
| Research agenda | 650 |
| Conclusion | 300 |
| **Total** | **~5500-7000** |

### Sentence-type composition (aspirational targets)
- **~35% synthesis** — compare or contrast 2+ papers, draw a conclusion neither stated alone
- **~35% evidence** — one specific finding from one paper with a citation
- **~20% analysis** — your interpretation of what the evidence means
- **~10% framing** — transitions, scope, context

### Gap quality standard

Each gap must be specific enough that a postdoc can act on it Monday morning:
- **What is missing** (not "benchmarking is lacking" — what specific measurement, comparison, or combination)
- **What experiment fills it** (specific variables, parameter ranges, platform)
- **What we would know** (which quantity, expected effect size if the chain of evidence suggests one)

### Writing rules
- Use `[REF:AuthorName Year - Title]` citation markers matching paper `display_name` values
- Be precise: cite specific numbers, measurements, results
- No bullet points in prose sections
- **ZERO em-dashes or en-dashes as parenthetical separators** (hard ban)
- **Abstract** (200-300 words, one concept per sentence, no citations): follow the sentence order in the style guide (accessible context → inter-field observation → key quantitative evidence → reader capability). Hard bans:
  - Never write "This review maps/identifies/proposes/examines/organizes/structures" — or ANY sentence whose subject is "this review" or "this section" and whose predicate describes what the document does. This ban covers all verbs, not only the listed ones.
  - Never use "cross-community synthesis" or "cross-community" in output — these are internal planning terms. Use the actual observation.
  - Never use "postdoc to begin Monday" or similar informal shorthand from planning notes.
  - Never describe the review's process or method.
  - Final sentence: state what the reader now understands or can now do — not what the review contains. BAD: "Each gap is specific enough to resolve within a two-year program." GOOD: "Researchers now have a clear map of which experiments will have the highest marginal return, and why the order matters."
- **Section continuity**: each thematic section must open with a sentence naming something concrete from the previous section's closing, before introducing the new topic. BAD: "ALD's conformality on non-planar surfaces is its most distinctive property." GOOD: "The array-level variability described above all trace back to filament nucleation scatter — and ALD's conformality on non-planar substrates offers one direct route to controlling it."
- **Meta-commentary ban (all sections, not just abstract)**: never write a sentence whose grammatical subject is "this review," "this section," "the sections above," or equivalent, where the predicate describes what the document does rather than what the field shows. This applies to introductions, section openers, and conclusions.
- **NEVER mention your exploration method or source counts**
- **No structural scaffolding visible to reader**
- **3-5 figure placeholders with detailed captions** (specific axes, expected data points, source papers)

## Loading Context

```python
from scholarforge.agent.defaults import build_generation_prompt
print(build_generation_prompt(artifact_type_id='lit_review', journal='...', field_hint='<topic>'))
```

## Export

```python
from scholarforge.agent.workflows import export_paper
outputs = export_paper(markdown_text, "data/output/<filename>.md", journal="Advanced Functional Materials", docx=True, pdf=True)
```

```python
from scholarforge.agent.tools import save_reading_log
save_reading_log("data/output/<dir>")
```

## Suppress Noise

Append `2>&1 | grep -v "INFO\|WARNING\|Loading\|Batches\|Bert\|UNEXPECTED"` to all Python commands.
