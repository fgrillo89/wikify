# Corpus Recursive Search

Recursive search means inspect, choose, traverse, inspect again. It
keeps model context small and preserves judgment at each hop.

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

### Concept To Evidence

```bash
wikify corpus find "conductive filament" --corpus <c> --top-k 8
wikify corpus show chunk:<best-chunk> --corpus <c> --full
wikify corpus find "conductive filament HfO2" --corpus <c> --top-k 8
```

### Acronym To Expanded Concept

```bash
wikify corpus find "ALD" --corpus <c> --text
wikify corpus show doc:<doc-with-definition> --corpus <c> --full
wikify corpus find "atomic layer deposition" --corpus <c> --top-k 8
```

### Seed Document To Concept Candidates

```bash
wikify corpus find --seed --corpus <c> --max 3
wikify corpus show doc:<seed-doc> --corpus <c> --full
```

The workflow decides whether to read the full document, abstract,
introduction, conclusion, or selected chunks.

## Stop Signals

- The selected chunk contains a verbatim quote suitable for evidence.
- Additional traversals return duplicate or lower-quality previews.
- The workflow's current sampling budget is exhausted.
- The next decision requires bundle state, not more corpus search.
