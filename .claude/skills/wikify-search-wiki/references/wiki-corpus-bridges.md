# Wiki To Corpus Bridges

Use bridges when committed pages reveal a question that needs source
evidence.

## Bridge Inputs

- Page title.
- Page aliases.
- Missing coverage phrase from the page.
- Evidence chunk handles emitted by `wiki traverse ... --to evidence`.
- Exact quoted claim in a reference definition.
- Neighbor page title or related concept.

## Bridge Patterns

```text
wiki find -> wiki show -> extract page title/aliases -> corpus find
wiki traverse <slug> --to evidence -> corpus show chunk:<id>
wiki show -> missing coverage phrase -> corpus find "<page> <gap>"
wiki show -> evidence quote -> corpus find "<exact phrase>" --text
```

## Pipe-Friendly Bridge

The cleanest bridge uses short handles end-to-end:

```bash
# Pull every evidence chunk for a wiki page, dump into corpus show.
wikify wiki traverse "Atomic Layer Deposition" --to evidence \
    --format quiet --run <bundle> \
  | xargs -I {} wikify corpus show {} --corpus <c>

# Or chain into a corpus traversal — find papers that cite the same
# corpus docs the page is grounded in.
wikify wiki traverse "Atomic Layer Deposition" --to evidence \
    --format quiet --run <bundle> \
  | xargs -I {} wikify corpus traverse {} --to source --format quiet \
  | sort -u \
  | xargs -I {} wikify corpus traverse {} --to cited-by \
        --rank citation_count --top-k 3 --corpus <c>
```

The search skill only finds the source material. A workflow decides
whether to append query feedback, add evidence, or refine a page.
