# Commit And Projections

Commit promotes a validated response to the committed wiki.

```bash
wikify wiki commit <slug>
wikify wiki build indexes
wikify wiki build graph
wikify wiki build vectors
wikify wiki check
```

`wiki commit` requires `validation.json.ok == true` and re-checks quote
grounding under the run lock. Successful commit writes the page under
`wiki/articles/` or `wiki/people/` and garbage-collects transient
attempt artifacts.

Derived projections are rebuildable. Workflows decide when freshness is
required.
