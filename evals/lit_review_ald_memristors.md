# Eval: Literature Review on ALD Memristors

## Prompt

Write a literature review on ALD-based memristors for neuromorphic computing targeting Advanced Functional Materials.

## Agent Config

- **Artifact type**: lit_review
- **Journal**: Advanced Functional Materials
- **Tools**: all KB tools (list_papers, search_papers, deep_read, get_paper, get_graph_metrics, get_sections, list_topics, get_corpus_summary)
- **Model**: any (haiku for cost, sonnet/opus for quality)

## Acceptance Criteria

The agent should autonomously:

1. **Explore** the corpus before writing (call at least 3 different tools)
2. **Identify** hub papers and read the key ones in depth
3. **Plan** a thematic structure (not paper-by-paper)
4. **Write** a complete review (~2000-3000 words) with:
   - Abstract, Introduction, thematic body sections, Conclusion
   - [REF:...] citation markers matching real papers in the corpus
   - At least 12 citations from the 20 available papers
   - Specific numbers (voltages, temperatures, linearity coefficients)
   - No bullet points in prose, no LLM tells
5. **Output** valid markdown with # headings

## How to Run

```python
from wikify.agent import ScholarForgeAgent, get_default_tools
from wikify.agent.defaults import build_generation_prompt
from wikify.llm.hooks import CostTracker, TokenBudget

agent = ScholarForgeAgent(
    model="claude-haiku-4-5-20251001",
    tools=get_default_tools(),
    hooks=[CostTracker(), TokenBudget(150_000)],
    system_prompt=build_generation_prompt(
        artifact_type_id="lit_review",
        journal="Advanced Functional Materials",
        field_hint="ALD memristors neuromorphic computing",
    ),
)

result = agent.run(
    "Write a literature review on ALD-based memristors for neuromorphic computing "
    "targeting Advanced Functional Materials.",
    max_turns=20,
)
```

## What to Check

| Criterion | Pass | Fail |
|-----------|------|------|
| Called >= 3 different tools | Agent explored before writing | Jumped straight to writing |
| Output >= 2000 words | Substantial review | Stub or summary |
| >= 12 [REF:...] citations | Well-sourced | Under-cited |
| Has Abstract + Intro + body + Conclusion | Complete structure | Missing sections |
| Thematic organization | Grouped by concept | Paper-by-paper summary |
| Specific numbers cited | "NL = 1.4", "91% accuracy" | Vague qualifiers |
| No LLM tells | Clean prose | "delve", "crucial", em-dashes |
| Materials science conventions | Process params, characterization data | Generic language |
