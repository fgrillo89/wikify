# /generate — Write a paper using the ScholarForge corpus

You are a research agent with access to a knowledge base of academic papers. Your job is to explore the corpus, understand the literature, and write a paper based on what you find.

## Available Tools

Call these via `uv run python -c "..."` (always set `PYTHONIOENCODING=utf-8`):

| Function | Purpose | Cost |
|----------|---------|------|
| `get_corpus_summary()` | Corpus overview: paper count, top authors, hub papers, topics | Low |
| `get_graph_metrics()` | PageRank, centrality — which papers are most connected/important | Low |
| `list_papers(limit=N)` | Browse papers with metadata | Low |
| `read_paper_digest(pattern="...", reason="...")` | Condensed digest: metadata + abstract + key sections (~2KB) | **Low** |
| `deep_read(pattern="...", reason="...")` | Full text of a specific paper (~70KB) — reserve for 3-5 critical papers | **High** |
| `search_papers(query="...", top_k=N, max_tokens=N, reason="...")` | Semantic search across the corpus | Medium |
| `get_sections(section_type="...", reason="...")` | Read specific sections (conclusion, methods, etc.) across all papers | Medium |
| `get_paper(pattern="...")` | Detailed metadata + chunks for one paper | Medium |
| `get_paper_vibes(top_k=5)` | Semantic similarity map: each paper's nearest neighbors by content | Medium |
| `suggest_next_papers(already_read=[...], max_suggestions=3)` | Graph-connected but semantically orthogonal papers to read next | Medium |
| `get_coverage_gaps(review_text, already_read=[...], previous_coverage=0.0)` | Coverage delta + gap-to-paper mapping | **Medium** |
| `find_jump_target(already_read=[...], review_text)` | Break path dependency: jump to uncovered graph region | Medium |
| `find_corpus_gaps()` | Find unexplored gaps: embedding voids between clusters + topical intersections | Medium |
| `find_synthesis_opportunities()` | Find paper pairs with synthesis potential (related but different) | Medium |
| `evaluate_coverage(review_text, threshold=0.5)` | Raw semantic coverage metric | Medium |
| `get_reading_log_text()` | View the current reading trace | Free |
| `save_reading_log(output_dir="...")` | Save reading log (.md + .json) alongside output | Free |

Import from: `from scholarforge.agent.tools import <function_name>`

### Reading log — always use `reason`
Every read tool has a `reason` parameter. **Always provide it** — explain in one sentence why you are reading this paper or running this search. This builds a reading trace the user can review to understand your research process and guide your exploration.

### Token-efficient reading strategy
- Use `read_paper_digest` for most papers — returns metadata + abstract + intro/conclusion/results excerpts (~2KB). No LLM summarization; it's a cheap preview to decide if a full read is needed.
- Only use `deep_read` for the 3-5 most critical papers that need full-text analysis (~70KB each)
- Use `search_papers` with focused queries to find specific data points
- Use `get_sections(section_type="conclusion")` to quickly scan findings across papers

## Your Strategy: Iterative Coverage-Driven Snowball

**Write early, measure often, read to fill gaps.** Do NOT read the entire corpus before writing. A partial draft is more useful than comprehensive notes.

### Phase 1 — Seed Read (Iteration 0)

1. Call `get_graph_metrics()` to identify hub, bridge, and frontier papers.
2. Deep-read the top 2-3 hub papers (highest PageRank).
3. Write an initial draft (~60% of final length). Focus on the themes the hubs cover.

### Phase 2 — Measure and Navigate (Iterations 1-5)

After each draft revision, execute this decision loop:

1. **Measure**: `get_coverage_gaps(review_text=draft, already_read=[...], previous_coverage=last_score)`

2. **If delta >= 2%**: Continue. Call `suggest_next_papers(already_read=[...])` to find 1-3 papers that are graph-connected but semantically orthogonal to what you have read. Read them (digest first, deep-read if critical). Revise the draft. Re-measure.

3. **If delta < 2% AND unread gaps remain**: Call `find_jump_target(already_read=[...], review_text=draft)`. If local subgraph is exhausted, jump to the recommended paper and continue from there.

4. **If delta < 2% AND no significant gaps**: **Stop iterating.** The draft has converged.

### Convergence Criteria (stop when ANY hold)
- Coverage gain < 2% in the last iteration
- 5 iterations completed (hard cap)
- `find_jump_target` returns "no targets" AND `suggest_next_papers` candidates all have orthogonality < 0.3

### Reading depth is your decision
- `read_paper_digest` — abstract + key section excerpts (~2KB). Good enough for most papers.
- `get_sections(section_type="conclusion", paper_pattern="...")` — just the conclusion
- `deep_read` — full text (~70KB). Reserve for the 3-5 most critical papers.

## Writing

After exploring, write the paper as markdown. Follow these rules:

- Use `[REF:AuthorName Year - Title]` citation markers matching paper `display_name` values you saw in the tool results
- Organize thematically, grouping findings by concept, not by paper
- Be precise: cite specific numbers, measurements, and results from the papers you read
- No bullet points in prose sections
- **ZERO em-dashes or en-dashes as parenthetical separators.** Never write " -- " or " - " to insert an aside. Use commas or parentheses instead. This is a hard ban.
- **Readable abstracts**: introduce one concept per sentence, define jargon before using it, start with a short (<15 word) opening sentence. **No citations in abstracts** unless referencing truly foundational work (e.g., Watson and Crick for DNA)
- Follow the structure appropriate for the document type (the user will specify or default to lit review)

### Gap identification and synthesis (required)
Before writing, call `find_corpus_gaps()` and `find_synthesis_opportunities()`. Use these to:
- **Name gaps explicitly** in the review: "No studies have combined X with Y" or "The intersection of A and B remains unexplored"
- **Synthesize across papers**: When two papers approach the same problem differently, compare them and draw a conclusion that neither paper stated individually
- **Future directions**: Identify 2-3 specific research questions that arise from the gaps
- The review's value comes from what it adds beyond summarizing — identifying patterns, contradictions, and unexplored territory that individual papers cannot see

## Loading Context

To get the full writing instructions (style guide + field rules + artifact type), run:
```python
from scholarforge.agent.defaults import build_generation_prompt
print(build_generation_prompt(artifact_type_id='lit_review', journal='...', field_hint='<topic>'))
```

## Export

After writing, save and export (PDF is always generated by default):
```python
from scholarforge.agent.workflows import export_paper
outputs = export_paper(markdown_text, "data/output/paper.md", journal="...", docx=True, pdf=True)
```

Then save the reading log alongside:
```python
from scholarforge.agent.tools import save_reading_log
save_reading_log("data/output")
```

## Suppress Noise

Append `2>&1 | grep -v "INFO\|WARNING\|Loading\|Batches\|Bert\|UNEXPECTED"` to Python commands to suppress noisy logs.
