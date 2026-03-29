# Evaluation Runs

These are not automated tests. They are evaluation scripts that exercise
the full agent loop with a real LLM against the real corpus.

## How to run an eval

1. Set `ANTHROPIC_API_KEY` in `.env`
2. Open the eval .md file, copy the Python code block
3. Run it: `uv run python -c "..."`
4. Check the output against the acceptance criteria

## Eval files

| File | What it tests |
|------|---------------|
| `lit_review_ald_memristors.md` | Full lit review generation on the ALD/memristor corpus |

## Adding new evals

Create a .md file with:
- **Prompt**: what to ask the agent (natural language, not hardcoded steps)
- **Agent config**: model, tools, hooks, artifact type
- **Acceptance criteria**: what good output looks like
- **How to run**: Python code block
- **What to check**: pass/fail table
