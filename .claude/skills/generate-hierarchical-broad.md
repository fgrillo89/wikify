# /generate-hierarchical-broad -- Comprehensive coverage with hierarchical retrieval

You are a research agent with access to a knowledge base of academic papers. Your job is to explore the corpus, understand the literature, and write a paper based on what you find.

**IMPORTANT: YOU are the LLM.** You call tools via `uv run python -c "..."` to read data from the corpus, then YOU write the review directly as your output. Do NOT look for API keys, do NOT try to call litellm or any external LLM API. The tools read from a local database — they need no API key. YOU produce the final text.

## When to use this skill

Use this when breadth of citation coverage matters more than thesis sharpness. It surveys the full corpus at digest level, runs targeted gap analysis, and targets the widest possible evidence base. Trade-off: argument coherence is lower than `/generate-hierarchical-hybrid`; citation count is higher.

Use `/generate-hierarchical-hybrid` when you want the best thesis. Use this skill when you want maximum field coverage (comprehensive reviews, survey articles).

## Available Tools

Call these via `uv run python -c "..."` (always set `PYTHONIOENCODING=utf-8`):

| Tool | Purpose | When to use |
|------|---------|-------------|
| `get_corpus_summary()` | Paper count, year range, top authors, hub/bridge/frontier papers, topic vocabulary | Phase 0: orient |
| `get_frontier_exploration_order(max_papers=N)` | Recommended reading order: greedy seeds + frontier + bridge papers | Phase 1: survey order |
| `read_paper_digest(pattern, reason)` | TOC + section summaries (~1.5KB). Best first pass for any paper. | Phase 1: survey all |
| `get_sections(section_type, reason)` | Cross-corpus section scan (e.g., all conclusions) | Phase 2: catch stragglers |
| `read_section(pattern, section, reason)` | Full text of ONE section (~5KB). Use for quantitative detail. | Phase 3: drill |
| `find_corpus_gaps()` | Divergent papers; embedding voids between clusters | Phase 4: gap analysis |
| `find_synthesis_opportunities()` | Paper pairs with moderate similarity; unconnected related work | Phase 4: gap analysis |
| `search_papers(query, top_k, reason)` | Semantic search. Use for any gap still thin after the survey. | Phase 4: targeted fill |
| `record_paper_summary(paper_name, key_findings, ...)` | Distill into working memory. Call after every read. | After every read |
| `get_session_context()` | Recall all recorded summaries. Use before writing. | Before writing |
| `suggest_next_papers(already_read, max_suggestions)` | Graph-connected + semantically orthogonal papers not yet read. | If coverage still thin |
| `lookup_citation(pattern, max_results)` | Get display_name + BibTeX key. Free. | While writing |
| `save_reading_log(output_dir)` | Save reading trace alongside output. | After writing |

Import from: `from scholarforge.agent.tools import <function_name>`

### Read-once-summarize (MANDATORY)
After every `read_paper_digest`, `read_section`, or `deep_read`, call `record_paper_summary`. Set `role` to "hub", "frontier", "bridge", or "standard".

---

## Strategy: Survey First, Then Organize

```
Phase 0: ORIENT   --> corpus summary
Phase 1: SURVEY   --> digest every paper in the exploration order (stop when diminishing returns)
Phase 2: SWEEP    --> cross-corpus conclusions scan to catch stragglers
Phase 3: DRILL    --> targeted section reads for quantitative evidence
Phase 4: GAPS     --> find synthesis opportunities; fill any under-evidenced territory
Write             --> function-first structure, organized by what findings enable, not by material class
```

### Phase 0 — Reset and orient

```python
from scholarforge.agent.reading_log import reset_reading_log
reset_reading_log()

from scholarforge.agent.tools import get_corpus_summary
print(get_corpus_summary())
```

### Phase 1 — Field-wide survey

Get the exploration order and digest papers:
```python
from scholarforge.agent.tools import get_frontier_exploration_order
print(get_frontier_exploration_order(max_papers=30))
```

Digest papers via `read_paper_digest`. For each paper: what function does it address? What specific numbers does it report? Stop when new digests stop adding evidence you don't already have — this is the stopping criterion, not a count.

Call `record_paper_summary` after each digest.

### Phase 2 — Cross-corpus conclusions scan

After the survey, run a conclusions sweep to catch papers that ranked below the cutoff but have important findings:
```python
from scholarforge.agent.tools import get_sections
print(get_sections(section_type="conclusion", reason="Catch papers with important findings not in top exploration order"))
```

Add any new papers to your reading notes.

### Phase 3 — Targeted section reads

For 6-12 papers where the digest wasn't specific enough, drill into relevant sections:
```python
from scholarforge.agent.tools import read_section
print(read_section(pattern="...", section="results", reason="Get quantitative data for comparison X vs Y"))
```

Organize by **function** (what the device or method does), not by material class. The goal is to be able to write comparative sentences like "Method A achieves X under condition C; Method B achieves Y under condition D because of mechanism Z."

### Phase 4 — Gap analysis and targeted fill

```python
from scholarforge.agent.tools import find_corpus_gaps, find_synthesis_opportunities
print(find_corpus_gaps())
print(find_synthesis_opportunities())
```

For each identified gap or synthesis opportunity not covered by your current evidence, run a targeted search:
```python
from scholarforge.agent.tools import search_papers
print(search_papers(query="<gap-specific query>", top_k=5, reason="Fill gap: <what is missing>"))
```

Digest any papers found that aren't already in your notes.

---

## Write with Function-First Structure

**Organize by what findings enable, not by what materials exist.** A section titled by material class produces a catalog; a section titled by function produces analysis.

- NOT: "Section 3: HfO₂ — Section 4: TaOx — Section 5: ZnO"
- YES: "Section 3: Devices achieving endurance > 10⁶ cycles share a structural feature... Section 4: Devices optimized for analog linearity face a different constraint..."

### Suggested structure

1. **Introduction**: What does the field currently achieve? What does it not yet achieve? End with a signal that the review will identify where progress is most tractable — but do not list the gaps here.
2. **Thematic sections** (organized by function or theme, not by object or author): 4-6 sections. Each opens with what the community knows (established findings with specific numbers or claims), shows where knowledge is contested or incomplete, and closes by naming what is still missing. These transitions must emerge from prose — never as labeled headings.
3. **Gaps and open problems** (or "future directions", "open questions" — name it as appropriate for the field): Specific, actionable problems. Each gap must state what is missing, what study or argument fills it, and what we would learn. Not field-level truisms.
4. **Conclusion**: If the review found a strong synthesizing observation (inter-field, contradiction, convergence), close by returning to it. If the corpus was monolithic, close by naming the single most important open problem and what resolving it would change. Shows what has changed in the reader's understanding from beginning to end.

### Gap quality standard

Each gap must be precise enough to act on:
- **What is missing**: a specific measurement, comparison, argument, or combination — not "more research is needed"
- **What fills it**: a specific study, model, dataset, or argument — whatever is appropriate for the field
- **What we would learn**: which question gets answered; what it settles in the broader debate

BAD: "Standardized benchmarking protocols remain an unmet need."
GOOD: "No study has compared approach X and approach Y under controlled conditions. Such a comparison would determine whether the observed performance difference is intrinsic to the method or an artifact of evaluation setup."

### Pre-writing checklist

Before drafting, answer:

**What is the single most non-obvious observation in this corpus?** This may be an inter-field connection, a productive contradiction, a convergence of two approaches, or (in a monolithic field) the most important unresolved tension within a single community. State it as a fact about the literature — not as a description of what this review does. BAD: "This review identifies a connection between X and Y communities." GOOD: "The evidence that community A uses to support claim P is the same evidence community B uses to argue against it." If you cannot find one, reread `find_synthesis_opportunities` output.

**What is one specific claim the evidence supports but the field has not yet made explicitly?** "If [variable or condition], then [outcome] changes by [direction or magnitude] because [mechanism or reason] — and this is testable by [study, analysis, or argument]." For non-experimental fields, a directional claim with a stated mechanism is acceptable. This must appear in the gaps/forward-looking section.

### Word budget
| Section | Words |
|---------|-------|
| Abstract | 200-300 |
| Introduction | 400 |
| Each thematic section | 650-800 |
| Gaps and open problems | 600 |
| Conclusion | 300 |
| **Total** | **~5000-6500** |

### Sentence-type composition (aspirational targets)
- **~35% synthesis** — compare or contrast 2+ papers, draw a conclusion neither stated alone
- **~35% evidence** — one specific finding from one paper with a citation
- **~20% analysis** — your interpretation of what the evidence means
- **~10% framing** — transitions, scope, context

### Writing rules
- Use `[REF:AuthorName Year - Title]` citation markers matching paper `display_name` values
- Be precise: cite specific numbers, measurements, results
- No bullet points in prose sections
- **ZERO em-dashes or en-dashes as parenthetical separators** (hard ban)
- **Abstract** (200-300 words, one concept per sentence, no citations): follow the sentence order in the style guide (accessible context → inter-field observation → key quantitative evidence → reader capability). Hard bans:
  - Never write a sentence whose subject is "this review" or "this section" where the predicate describes what the document does. This ban covers all verbs.
  - Never use internal planning vocabulary in output ("cross-community synthesis", "gap-first", "postdoc to begin Monday", or similar).
  - Final sentence: state what the reader understands or can now do. BAD: "Each section addresses one open problem." GOOD: "Researchers can now identify which parameter has the highest leverage on device behavior and why."
- **Section continuity**: each section must open by naming something concrete from the previous section's closing. BAD: "Array integration is the next challenge." GOOD: "The single-device precision described above degrades at array scale — cross-talk and non-uniform current paths reintroduce the variability that careful deposition eliminated."
- **Meta-commentary ban**: never write a sentence whose subject is "this review," "this section," or "the sections above," where the predicate describes what the document does rather than what the field shows. This applies everywhere, not just the abstract.
- **NEVER mention your exploration method or source counts**
- **No structural scaffolding visible to reader** (no "Known:", "Missing:", "Open Question:" labels in final text)
- **3-5 figure placeholders with detailed captions** (specific axes, expected data points, source papers)

## Loading Context

```python
from scholarforge.agent.defaults import build_generation_prompt
print(build_generation_prompt(artifact_type_id='lit_review', journal='...', field_hint='<topic>'))
```

## Export

```python
from scholarforge.agent.workflows import export_paper
outputs = export_paper(markdown_text, "data/output/<filename>.md", journal="<journal>", docx=True, pdf=True)
```

```python
from scholarforge.agent.tools import save_reading_log
save_reading_log("data/output/<dir>")
```

## Suppress Noise

Append `2>&1 | grep -v "INFO\|WARNING\|Loading\|Batches\|Bert\|UNEXPECTED"` to all Python commands.
