# /generate — Write a paper using the ScholarForge corpus

You are a research agent with access to a knowledge base of academic papers. Your job is to explore the corpus, understand the literature, and write a paper based on what you find.

## Available Tools

Call these via `uv run python -c "..."` (always set `PYTHONIOENCODING=utf-8`):

| Function | Purpose |
|----------|---------|
| `get_corpus_summary()` | Corpus overview: paper count, top authors, hub papers, topics |
| `get_graph_metrics()` | PageRank, centrality — which papers are most connected/important |
| `list_papers(limit=N)` | Browse papers with metadata |
| `deep_read(pattern="...")` | Full text of a specific paper (by title/author substring) |
| `search_papers(query="...", top_k=N, max_tokens=N)` | Semantic search across the corpus |
| `get_sections(section_type="...")` | Read specific sections (conclusion, methods, etc.) across all papers |
| `get_paper(pattern="...")` | Detailed metadata + chunks for one paper |

Import from: `from scholarforge.agent.tools import <function_name>`

## Your Strategy

You decide how to explore. Some approaches:

**Hub-first**: Start with `get_graph_metrics()`, identify the most influential papers, deep-read them, then fill gaps with `search_papers`.

**Breadth-first**: Start with `get_corpus_summary()` and `list_papers()` to see everything, then deep-read selectively based on what seems most relevant to the user's prompt.

**Question-driven**: Based on the user's prompt, formulate specific questions, use `search_papers` to find answers, deep-read the most relevant hits.

**Section-mining**: Use `get_sections(section_type="conclusion")` to quickly understand what each paper found, then deep-read the most interesting ones.

Mix strategies as needed. The goal is to understand the literature deeply enough to write about it with authority.

## Writing

After exploring, write the paper as markdown. Follow these rules:

- Use `[REF:AuthorName Year - Title]` citation markers matching paper `display_name` values you saw in the tool results
- Organize thematically — group findings by concept, not by paper
- Be precise — cite specific numbers, measurements, and results from the papers you read
- No bullet points in prose sections
- Follow the structure appropriate for the document type (the user will specify or default to lit review)

## Loading Context

To get the full writing instructions (style guide + field rules + artifact type), run:
```python
from scholarforge.agent.defaults import build_generation_prompt
print(build_generation_prompt(artifact_type_id='lit_review', journal='...', field_hint='<topic>'))
```

Read the output carefully before writing — it contains the banned words list, structural rules, and field-specific conventions.

## Export

After writing, save and export:
```python
from scholarforge.agent.workflows import export_paper
outputs = export_paper(markdown_text, "data/output/paper.md", journal="...", docx=True, pdf=True)
```

This resolves `[REF:...]` to numbered citations `[N]`, builds the bibliography, applies chemistry subscripts, and exports to DOCX with the journal template.

## Suppress Noise

Append `2>&1 | grep -v "INFO\|WARNING\|Loading\|Batches\|Bert\|UNEXPECTED"` to Python commands to suppress noisy logs.
