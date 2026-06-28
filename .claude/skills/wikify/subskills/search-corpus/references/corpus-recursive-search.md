# Corpus Recursive Search

Recursive search means inspect, choose, traverse, inspect again. It
keeps model context small and preserves judgment at each hop.

## Handles And Output Formats

- The CLI accepts and emits **short handles**: ``doc:<12-hex>``,
  ``chunk:<8-hex>``. Full ids and unique suffixes also resolve.
- ``--format quiet`` prints one handle per line — pipe-safe.
- ``--format compact`` (default for TTY) is tab-separated. Column
  layout depends on the command and mode — see
  ``corpus-cli-patterns.md`` for the table. Quick legend:
  - ``score`` semantic cosine, higher is closer.
  - ``cites=N`` in-corpus citation count.
  - ``pr=X.XXXX`` PageRank.
  - ``n=K`` (paper rows) how many chunks of the paper matched.
- When stdout is a pipe, ``--format`` defaults to ``quiet`` automatically.

## Pattern

```text
1. Run a small list/find command.
2. Inspect handles, scores, docs, and previews.
3. Choose a promising handle.
4. Show or traverse from that handle.
5. Use the new context to refine the next query.
6. Stop when the workflow has enough evidence for its current decision.
```

## Examples

### Most Cited Paper About A Concept

One call, no piping:

```bash
wikify corpus find "atomic layer deposition" \
    --by paper --rank citation_count --top-k 3 --corpus <c>
```

### Concept To Evidence

```bash
wikify corpus find "conductive filament" --corpus <c> --top-k 8
wikify corpus show chunk:<short> --corpus <c> --full
wikify corpus find "conductive filament HfO2" --corpus <c> --top-k 8
```

### Find Chunk -> Who Cites Its Paper

```bash
wikify corpus find "atomic layer deposition" --by paper --top-k 1 \
    --format quiet --corpus <c> \
  | xargs -I {} wikify corpus traverse {} --to cited-by \
        --rank citation_count --top-k 5 --corpus <c>
```

### Find Chunk -> In-Corpus Citations Inside The Chunk

```bash
wikify corpus find "atomic layer deposition" --top-k 1 \
    --format quiet --corpus <c> \
  | xargs -I {} wikify corpus traverse {} --to cited-in-corpus \
        --rank citation_count --top-k 5 --corpus <c>
```

### Acronym To Expanded Concept

```bash
wikify corpus find "ALD" --corpus <c> --text
wikify corpus show doc:<short> --corpus <c> --full
wikify corpus find "atomic layer deposition" --corpus <c> --top-k 8
```

### Sample Documents To Concept Candidates

```bash
wikify corpus sample --corpus <c> --max 3
wikify corpus show doc:<sampled-short> --corpus <c> --full
```

The workflow decides whether to read the full document, abstract,
introduction, conclusion, or selected chunks.

## Traverse Relations

For a ``doc:`` handle:

- ``cited-by``     sources that cite this source
- ``references``   sources cited by this source
- ``chunks``       chunks belonging to this source

For a ``chunk:`` handle:

- ``source``           the doc this chunk belongs to
- ``cited-in-corpus``  in-corpus sources cited by markers inside the chunk's text

Add ``--rank citation_count`` or ``--rank pagerank`` (for source-typed
results) and ``--top-k N`` to focus the traversal.

## Stop Signals

- The selected chunk contains a verbatim quote suitable for evidence.
- Additional traversals return duplicate or lower-quality previews.
- The workflow's current sampling budget is exhausted.
- The next decision requires bundle state, not more corpus search.
