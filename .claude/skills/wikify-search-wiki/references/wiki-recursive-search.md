# Wiki Recursive Search

Recursive wiki search inspects committed pages in small steps.

## Pattern

```text
1. Search for the user topic or candidate title.
2. Inspect the top handles and previews.
3. Show the best page.
4. Inspect links, evidence docs, overlap, or thinness if exposed.
5. Search related pages or move to corpus search.
```

## Examples

### Answer From Wiki

```bash
wikify wiki find "resistive switching mechanism" --run <b> --top-k 5
wikify wiki show "<page>" --run <b> --full
```

### Inspect Coverage Gap

```bash
wikify wiki find "chemical vapor deposition" --run <b> --top-k 5
wikify wiki show "<nearest-page>" --run <b>
wikify wiki check --run <b>
```

### Find Related Pages

Use relationship flags when available. If unavailable, search with page
titles, aliases, or evidence doc ids from the selected page.
