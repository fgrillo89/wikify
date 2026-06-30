# Wikify documentation

Wikify turns a folder of source documents into an evidence-grounded
wiki: encyclopedic pages where every claim is backed by a verbatim quote
traceable to its source.

Start with the **overview**, then follow the branch you need. The
documentation is a tree rooted at the overview; each component document
expands one part of it.

## Read first

- **[overview.md](overview.md)** — what Wikify is, the core vocabulary
  (corpus, chunk, bundle, wiki, concept, dossier, evidence, maturity,
  data artifact), and the end-to-end agent loop
  (SENSE / DECIDE / DISPATCH / CONSOLIDATE / REASSESS / CURATE / EMIT /
  STOP). The top of this tree.

## System shape

- **[architecture.md](architecture.md)** — how the agent runtime, the
  deterministic CLI/MCP tools, and the on-disk bundle fit together; the
  three layers (corpus -> bundle -> site/metrics); citation grounding;
  the telemetry contract; the repository layout.
- **[filesystem-state-design.md](filesystem-state-design.md)** — the
  durable on-disk contract for a bundle: run state, the append-only
  event ledger, locks and claims, and the per-concept working files.

## Building a corpus (input)

- **ingestion.md** *(to write)* — how a PDF/DOCX/PPTX/HTML/Markdown file
  becomes corpus chunks, embeddings, and a graph; parser backends
  (docling default, lite); references and author resolution.
- **corpus.md** *(to write)* — the corpus store and its read surfaces:
  semantic search, full-text search, and graph traversal.

## Building a wiki (the agent loop)

- **investigate.md** *(to write)* — the `wikify` editor loop in depth:
  the eight per-round steps, the dispatch waves, the P1-P5 exploration
  patterns, the maturity score and bands, seeding, coverage, dedup,
  person pages, the DATA wave, and re-entry on an existing bundle.
- **writing.md** *(to write)* — how a mature dossier becomes a committed
  page: the draft/response/validation cycle, citation format, grounding
  validation, and the article/person style rules.
- **data-artifacts.md** *(to write)* — the data layer: harvesting
  verifiable numbers, the claim store, consolidating into `kind=data`
  tables, and why this layer is separate from the wiki page graph.

## Output and evaluation

- **[wiki-rendering.md](wiki-rendering.md)** — the static HTML site:
  structure and navigation, the article/person/data page types, figures,
  search, the references and graph views, and self-contained packaging.
- **[metrics.md](metrics.md)** — the evaluation metrics computed over a
  bundle (M1, M3, M5, M6 and related), what each measures, and how to
  read them.

## Surfaces

- **cli-mcp.md** *(to write)* — the command-line nouns and the
  agent-native MCP tool catalog, and how the read-side CLI and MCP
  mirror each other.

## Document tree

```
overview.md                  (root: concepts + the agent loop)
|-- architecture.md          system shape, grounding, telemetry
|   `-- filesystem-state-design.md   on-disk bundle contract
|-- ingestion.md             files -> corpus
|   `-- corpus.md            corpus store + search/traverse
|-- investigate.md           the wikify editor loop in depth
|   |-- writing.md           dossier -> committed page
|   `-- data-artifacts.md    the separate data layer
|-- wiki-rendering.md        bundle -> static site
|-- metrics.md               evaluation metrics
`-- cli-mcp.md               command + MCP surfaces
```

Documents marked *(to write)* are planned branches; the section outline
each must fill is tracked alongside this index.
