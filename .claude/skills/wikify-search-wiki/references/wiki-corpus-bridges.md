# Wiki To Corpus Bridges

Use bridges when committed pages reveal a question that needs source
evidence.

## Bridge Inputs

- Page title.
- Page aliases.
- Missing coverage phrase from the page.
- Evidence doc ids cited by the page.
- Exact quoted claim in a reference definition.
- Neighbor page title or related concept.

## Bridge Patterns

```text
wiki find -> wiki show -> extract page title/aliases -> corpus find
wiki show -> inspect cited doc ids -> corpus show doc:<id>
wiki show -> missing coverage phrase -> corpus find "<page> <gap>"
wiki show -> evidence quote -> corpus find "<exact phrase>" --text
```

The search skill only finds the source material. A workflow decides
whether to append query feedback, add evidence, or refine a page.
