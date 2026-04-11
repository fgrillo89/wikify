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
   - **Specific** — about a particular concept, variable, claim, or mechanism (not "more research is needed")
   - **Non-obvious** — something the community has not yet named or addressed, not a restatement of the field's own agenda
   - **Tractable** — addressable by a targeted study, experiment, argument, or synthesis

   Find enough distinct themes to organize the review — typically 4-6, but let the corpus decide. If gaps cluster naturally into 3 topics, use 3. If 7, use 7.

3. **Look for a structural pattern in the corpus** — the strongest synthesizing move available to you. In order of preference:
   - Two subfields or communities share data but don't cite each other, or reach opposite conclusions from similar evidence → an inter-field observation
   - A community assumption is inverted by a minority of papers that the mainstream hasn't engaged with → a productive contradiction
   - Two lines of work address the same phenomenon from different angles without knowing it → a convergence
   - None of the above: the corpus is relatively monolithic → your synthesizing move is identifying the single most important unresolved tension within the field

   **Only use the inter-field framing if the corpus actually has inter-field structure.** Do not force it. If the synthesis is within a single community, state it within that community's terms. Whatever form the synthesis takes, state it as an observation about what the literature shows — not as a description of your process. If a synthesis exists, it becomes the abstract's **second** sentence (sentence 1 sets accessible context; sentence 2 delivers the insight).

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

## Write with Gap-Driven Structure

**Every section exists to explain a gap, build evidence for it, and close with the specific missing study, test, or argument.**

Suggested structure:
1. **Introduction**: State what the field currently knows and where it falls short. End by signaling that specific, addressable gaps exist — but do not list them here. That is the body's job.
2. **Thematic sections**: One section per gap theme. Each opens with what the community knows (established findings, specific numbers or claims), shows where knowledge breaks down (contested results, missing comparisons, unresolved tensions), and closes with the gap stated as a testable hypothesis, specific missing study, or unresolved question. Labels like "Known:", "Missing:", "Open Question:" must never appear — the structure must emerge from prose.
3. **Forward-looking section** (research agenda, open problems, or future directions — name it as appropriate for the field): One actionable proposal per gap. Required elements per proposal:
   - What would be studied or argued, and how
   - What we would learn (which question gets answered, what the resolution tells us)
   - What makes it tractable now (what capability, dataset, or method exists that didn't before)
4. **Conclusion**: Shows what has changed in the reader's understanding from the beginning to the end. If the review found a strong synthesizing observation (inter-field, contradiction, convergence), close by returning to it and stating its implication. If the corpus was monolithic, close by naming the single most important thing the field now needs to do.

### Organize by function, not by category

Within thematic sections, organize by what findings *do* or *enable* — not by what objects, methods, or authors *are*:
- NOT: "Group A's approach → Group B's approach → Group C's approach"
- YES: "Approaches that achieve outcome X share property P... Approaches optimized for outcome Y face a different constraint..."

This applies regardless of field: the principle is to group by explanatory power, not by taxonomy.

### Pre-writing checklist (required before drafting)

Before writing the first sentence, answer both questions explicitly:

**1. The synthesizing observation test**

What is the single most non-obvious observation in this corpus? It should be one of:
- A connection between two areas that share evidence but haven't cited each other
- An inversion of a community assumption — the mainstream says X, but the data supports not-X
- A convergence: two approaches solving the same problem from different angles without knowing it
- In a monolithic field: the single most important unresolved tension — where the community's own evidence contradicts its own consensus

State it as a fact about the literature, not as a description of what this review does. GOOD: "The same variable that community A minimizes is the variable community B needs to maximize." GOOD (monolithic): "The leading explanation for X predicts Y, but three independent studies report the opposite."

**Not every corpus has a cross-field synthesis.** If the corpus is within a single community, the strongest observation may simply be a productive contradiction or an overlooked implication. Use what the corpus actually offers. If `find_synthesis_opportunities` shows no meaningful inter-area connections, look for within-field inversions instead.

**2. The forward-looking claim (required — quantitative if evidence supports it)**

State one specific falsifiable prediction the evidence supports but the field has not yet made explicitly:

> "If [variable or condition], then [outcome] changes by [quantitative direction or magnitude] because [mechanism or reason] — and this is testable by [specific study, analysis, or experiment in the research agenda]."

**Quantitative first.** If the corpus contains numerical data for the relevant variables, the prediction must include a number or range (e.g., "retention improves by 40-60%" not "retention improves"). If numbers are absent, a directional claim with a stated mechanism is acceptable, but explain why a quantitative estimate is not yet possible.

For non-experimental fields (theoretical, computational, qualitative), the form is: "The evidence suggests that [claim], which implies [consequence] — testable by [argument, model, or dataset]."

This sentence must appear in the forward-looking section. Do not write the review until you have this sentence — the gap between a 8.9 and a 9.5 PI score is almost always whether the forward-looking section delivers a falsifiable prediction with a mechanism, or only a research suggestion.

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

Each gap must be specific enough to act on immediately:
- **What is missing**: a specific measurement, comparison, argument, or combination — not a field-level truism ("more research is needed")
- **What fills it**: a specific study design, theoretical argument, dataset, or experiment — whatever is appropriate for the field
- **What we would learn**: which question gets answered; what the resolution tells us about the field's broader debate

### Writing rules
- Use `[REF:AuthorName Year - Title]` citation markers matching paper `display_name` values
- Be precise: cite specific numbers, measurements, results
- No bullet points in prose sections
- **ZERO em-dashes or en-dashes as parenthetical separators** (hard ban)
- **Abstract** (200-300 words, one concept per sentence, no citations): follow the sentence order in the style guide — accessible context first, then the most important observation, then key evidence, then what the reader gains. Hard bans:
  - Never write any sentence whose subject is "this review" or "this section" and whose predicate describes what the document does. This ban covers all verbs.
  - Never use internal planning vocabulary in output ("cross-community synthesis", "inter-field", "gap-first", or similar process terms).
  - Never describe the review's method or structure.
  - Final sentence: state what the reader understands or can now do. BAD: "Each problem is specific enough to resolve within two years." GOOD: "Researchers can now identify which open problem carries the highest leverage, and why addressing it first changes the field's trajectory."
- **Section continuity**: each thematic section must open with a sentence naming something concrete from the previous section's closing, before introducing the new topic. BAD: "Method B addresses a related challenge." GOOD: "The variability problem described above compounds at scale — and Method B's precision advantage is precisely what makes it a candidate for addressing it."
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
