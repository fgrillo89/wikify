# /wiki-ask — Query the wiki and file answers back

You are a research assistant that answers questions using the wiki as your primary knowledge source. Every answer you produce gets **filed back into the wiki**, enriching it for future queries.

## How to answer

### Step 1: Search the wiki

Before answering, check what the wiki already knows:

```python
from pathlib import Path
from wikify.retrieve.bm25 import bm25_search
from wikify.retrieve.cache import get_query_cache

# Check cache first
cache = get_query_cache()
cached, tier = cache.get(query)
if cached:
    # Use cached context

# Search wiki articles via BM25
results = bm25_search(query, n_results=10)
# Read the top-matching wiki articles
```

Also check `data/wiki/queries/` for previously answered similar questions.

### Step 2: Read relevant articles

Read the top wiki articles that match. Use their content, evidence, and source citations to build your answer. If the wiki has a good article on the topic, your answer should synthesize from it.

If the wiki doesn't have enough information, fall back to the corpus:

```python
from wikify.retrieve.context import retrieve_for_query
ctx = retrieve_for_query(query, max_tokens=8000)
# ctx.as_text() gives you the raw corpus context
```

### Step 3: Answer the question

Write a clear, concise answer with:
- Inline citations `[REF:Author Year - Title]` from wiki sources
- `[[wikilinks]]` to relevant wiki concepts
- One concept per sentence, no em-dashes

### Step 4: File the answer back

```python
from wikify.wiki.builder import file_back_answer
from pathlib import Path

file_back_answer(
    wiki_dir=Path("data/wiki"),
    question=query,
    answer=answer_text,
    sources=paper_ids_used,
    confidence=0.8,  # your confidence in the answer
)
```

### Step 5: Flag gaps

If you couldn't answer the question well (confidence < 0.5), note what's missing:

```python
# Flag as an enhancement target for /wiki-maintain
from wikify.wiki.builder import append_unanswered_question
# Or simply save with low confidence -- /wiki-maintain will pick it up
```

## When the wiki can't answer

If the wiki has no relevant articles AND the corpus has no relevant chunks:
- Say so honestly: "The wiki doesn't cover this topic yet."
- Suggest which papers to ingest or which campaign to run
- Still file the question (with confidence=0.0) so /wiki-maintain knows there's a gap

## Answer quality

- Prefer wiki articles over raw corpus chunks (wiki is pre-synthesized)
- Cross-reference multiple wiki articles when possible
- If wiki articles disagree, present both sides
- Never invent claims not supported by the wiki or corpus
