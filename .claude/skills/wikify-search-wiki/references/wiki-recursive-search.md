# Wiki Recursive Search

Recursive wiki search inspects committed pages in small steps.

## Pattern

```text
1. Search for the user topic or candidate title.
2. Inspect the top handles and previews.
3. Show the best page.
4. Traverse one hop (links, linked-by, co-evidence) to find relatives.
5. Cross to corpus when the wiki answer is incomplete.
```

## Handles And Output Formats

- Slugs are natural titles (`"Atomic Layer Deposition"`). Case-
  insensitive unique prefixes work too.
- `--format quiet` emits one handle per line — pipe-safe.
- Default switches to `quiet` automatically when stdout is a pipe.

## Examples

### Answer From Wiki

```bash
wikify wiki find "resistive switching mechanism" --run <b> --top-k 5
wikify wiki show "<page>" --run <b> --full
```

### Walk The Link Graph

```bash
wikify wiki traverse "Atomic Layer Deposition" --to links \
    --rank n_links --top-k 10 --run <b>
wikify wiki traverse "Atomic Layer Deposition" --to linked-by \
    --top-k 5 --run <b>
```

### Find Pages That Share Evidence Sources

```bash
wikify wiki traverse "Atomic Layer Deposition" --to co-evidence \
    --top-k 5 --run <b>
```

### Inspect Coverage Gap

```bash
wikify wiki find "chemical vapor deposition" --run <b> --top-k 5
wikify wiki show "<nearest-page>" --run <b>
wikify wiki check --run <b>
```

### Bridge To Corpus Evidence

```bash
wikify wiki traverse "<page>" --to evidence --format quiet --run <b> \
  | xargs -I {} wikify corpus show {} --corpus <c>
```
