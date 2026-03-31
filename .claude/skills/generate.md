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

## Your Strategy: Hybrid Greedy-Frontier Exploration

**Write early, measure often, read to fill gaps.** Do NOT read the entire corpus before writing.

### Phase 1 — Dual Seed: Greedy + Frontier (target: <2 min)

1. Get the frontier exploration order (combines greedy seeds with frontier papers):
```python
from scholarforge.agent.tools import get_frontier_exploration_order
print(get_frontier_exploration_order(max_papers=15))
```
This gives you 3 greedy seeds (coverage baseline) + 12 frontier papers (density-ranked, dissimilar to seeds).

2. **Deep-read the 3 greedy seeds** (these cover ~90% of corpus semantic space).
3. **Read abstracts/conclusions of the top 5 frontier papers** via `get_sections(section_type="conclusion", paper_pattern="...")` and `read_paper_digest`.
4. **Decide from both ends**: based on what you read, pick 1-2 frontier papers for deep reading and 1-2 neighbors of the seeds for depth.

### Phase 2 — Gap-Aware Exploration

5. **Find gaps and synthesis opportunities**:
```python
from scholarforge.agent.tools import find_corpus_gaps, find_synthesis_opportunities
print(find_corpus_gaps())
print(find_synthesis_opportunities())
```

6. Read 2-3 papers that address the most promising gaps (use `search_papers` or `suggest_next_papers`).

### Phase 3 — Write with gap emphasis (~4000-5000 words)

7. Write the review with these priorities:
   - Name every gap you found
   - Synthesize across mainstream seeds and frontier papers
   - Future directions section with 3-5 specific research questions from the gaps
   - Contradictions between papers should be stated explicitly

### Reading depth is your decision
- `read_paper_digest` — abstract + key section excerpts (~2KB). Good for most papers.
- `get_sections(section_type="conclusion", paper_pattern="...")` — just the conclusion
- `deep_read` — full text (~70KB). Reserve for 3-5 critical papers.

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
