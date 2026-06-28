# Docs audit — staleness and gaps vs real code

Scope: `README.md`, `CLAUDE.md`, `AGENTS.md`, `docs/architecture.md`,
`docs/metrics.md`, `docs/filesystem-state-design.md`, compared against
`src/wikify/`, `.claude/skills/`, and the CLI/MCP surface.

Verdict: the three `docs/` files plus `AGENTS.md` describe an earlier
loose-file architecture and a four-noun workflow. The shipped code has
moved to a SQLite-backed corpus store, added a whole data-artifacts
subsystem, an arxiv acquisition path, and an MCP tool catalog, and made
`wikify-investigate` the primary (and most complex) workflow. None of
that is in `docs/`. `README.md` is empty. For a public-OSS newcomer the
single biggest gaps are: no README, no investigate write-up, no
data/MCP/corpus-DB docs, and a stale package map.

---

## HIGH

### H1. README.md is empty (0 bytes)
`pyproject.toml` sets `readme = "README.md"`, so the packaged
description is blank. A public repo has no landing page: no one-line
pitch, install steps, quickstart, or end-to-end example
(`corpus build` -> `run init` -> investigate -> `render` -> `eval`).
This is the first thing a newcomer hits.
Fix: write a real README — what Wikify is, install via `uv`, a minimal
worked example, links to `docs/` and the skills.

### H2. `wikify-investigate` (active focus, most complex subsystem) is undocumented in `docs/`
`CLAUDE.md` names investigate the active track and the
`wikify-investigate` skill is a full editor/explorer architecture:
`SENSE -> DECIDE -> DISPATCH -> CONSOLIDATE -> REASSESS -> CURATE -> EMIT`,
P1-P5 recursive exploration patterns, composite maturity scoring and
bands, per-slug notebook dossiers, `chunk_coverage_ratio` vs
`addressable_coverage_ratio`, the `wikify run sense` snapshot command,
the claim store, and the DATA wave that emits `kind=data` artifact
tables. `docs/` mentions only the skill *name* (architecture.md L195-197).
The supporting code — `bundle/work/{maturity,notebook,coverage,chunk_ids}.py`
— is not in any doc.
Fix: add `docs/investigate.md` describing the editor loop, P1-P5,
maturity gate, coverage objective, DATA wave, and re-entry.

### H3. The `data/` artifacts subsystem and `wikify data` / `wikify arxiv` nouns are entirely absent from `docs/` and `AGENTS.md`
`src/wikify/data/` (`harvest`, `consolidate`, `artifact_page`, `store`,
`verify`, `models`) is a first-class subsystem with its own CLI noun
(`cli/data.py`) and MCP/skill support (`wikify-extract-data`,
`wikify-consolidate-data`). `src/wikify/sources/arxiv.py` + `cli/arxiv.py`
back the `wikify-arxiv` skill. Both docs claim "Seven workflow nouns
plus the MCP control noun" (architecture.md L50, AGENTS.md L128), but
`cli/__init__.py` registers ten: `corpus, arxiv, run, work, data,
draft, wiki, render, eval, mcp`.
Fix: update the noun list everywhere; document the data-artifact layer
(kind=data pages render+nav but are not in the wiki graph) and arxiv
acquisition.

### H4. Corpus storage model is stale — loose files vs SQLite store
`docs/architecture.md` (L14-25, L154-162) and `docs/filesystem-state-design.md`
describe a loose-file corpus (`chunks.py`, `vectors.npz`,
`vectors_meta.py`, `doc_markdown.py`, `topics.json`,
`images_index.py`). The real corpus is a single SQLite database under
`src/wikify/corpus/store/` (`schema.py` defines `documents`, `chunks`,
`authors`, `bib_entries`, `chunk_citations`, `assets`, `embeddings`
(BLOB vectors), `graph_edges`, `graph_views`, `node_metrics`, `topics`,
plus FTS5 virtual tables `chunks_fts` / `documents_fts`). This directly
breaks the "corpus/wiki databases" and "vector search" topics a
newcomer needs: vector search is BLOB embeddings + cosine in
`corpus/store/vectors.py`, text search is FTS5 in `corpus/store/fts.py`,
and the KG is `graph_edges` + `corpus/store/kg.py` — none documented.
Fix: rewrite the corpus section to describe the SQLite store, the
embedding/FTS/graph tables, and how `corpus find` (semantic) vs
`--text` (FTS5) vs `traverse` (graph) map onto them.

---

## MEDIUM

### M1. Dead/empty package directories still on disk
`src/wikify/_prototype/`, `src/wikify/citestore/`, `src/wikify/store/`,
and `src/wikify/serve/` contain only `__pycache__` (no `.py` sources).
They are leftovers from refactors. `pyproject.toml` per-file-ignores
still point at `tests/wikify/citestore/test_graph.py` and
`tests/wikify/store/test_wiki_graph.py`. For a public repo these are
confusing orphans.
Fix: delete the empty package dirs (and stale pyproject ignores if the
tests moved); confirm no import references remain.

### M2. `docs/architecture.md` "Repository layout" no longer matches the tree
The package-per-noun map (L153-182) omits: `corpus/store/` (the entire
SQLite backend), `corpus/{handles,lock,session}.py`;
`bundle/work/{chunk_ids,coverage,maturity,notebook}.py`;
`bundle/wiki/{navigation,relink,session,store,vectors}.py`;
`bundle/draft/{dossier,references}.py`; and the top-level `sources/`,
`util/`, `data/`, `grounding.py`, `schema.py` modules.
Fix: regenerate the layout from the current tree.

### M3. `AGENTS.md` and `architecture.md` disagree on the bundle layout
`AGENTS.md` Data Layout (L116-122) lists `derived/{index.json,
graph.json, vectors.npz}` and omits `wiki.db` and `navigation.json`.
`architecture.md` (L95-107) puts `wiki.db` at the bundle root and lists
`derived/{index.json, navigation.json, navigation_context.json,
vectors.npz, eval.json, site/}` with no `graph.json`. Two canonical
docs contradict each other; `graph.json` appears in neither code path I
checked.
Fix: pick one source of truth (the wiki query store is `wiki.db`;
projections live in `derived/`) and align both docs.

### M4. `CLAUDE.md` "Current Focus" uses terminology that does not exist in the code
It frames the science as `scripted` vs `guided` modes. There are no
`scripted` or `guided` skills; the shipped workflow skills are
`wikify-baseline`, `wikify-investigate`, `wikify-query`,
`wikify-refine` (plus the data/arxiv/evidence capability skills).
Fix: restate the focus in terms of the actual strategies (e.g.
baseline vs investigate) or rename, consistently.

### M5. MCP tool surface is undocumented
The stdio server (`mcp/server.py`) exposes ~14 agent-facing tools:
`context_set`, `context_show`, `corpus_find`, `corpus_show`,
`corpus_sample`, `corpus_traverse`, `corpus_citation_walk`,
`corpus_similarity_walk`, `corpus_image`, `corpus_schema`, `wiki_find`,
`wiki_show`, `wiki_traverse`, `wiki_schema`. Skills bind to these
heavily (see `wikify-investigate` `allowed-tools`). `docs/` only says
"stdio MCP server used by agent runtimes" with no catalog. A newcomer
cannot learn the agent-native surface from docs.
Fix: document the MCP tool catalog (and that it mirrors the read-side
CLI), pointing to `.claude/skills/wikify/references/mcp/`.

### M6. `docs/metrics.md` omits implemented metrics
`eval/metrics.py` implements `image_coverage_residual` (M1_image) and
`figure_reference_counts`, which `metrics.md` never mentions. The doc
otherwise tracks the code well (M1, M2 Heaps, M3 + M3b g_links, M5, M6,
GT-P `person_recall`, GT-C `concept_recall`), and M4 is correctly
described as out-of-core.
Fix: add an M1_image subsection (image/caption coverage residual) and
note `figure_reference_counts`.

### M7. `AGENTS.md` violates the project's own no-meta-references rule
It carries "(post-Phase-C layout)" (L56), "do not hand-maintain a
parallel `.agents/skills/` tree" (L155-156), and a pointer to
`tasks/skill-centric-redesign-plan.md` (L18-20). The repo's stated rule
bans plan/phase references in shipped docs. For public release these
read as internal scaffolding.
Fix: strip phase/plan framing; describe what the layout IS.

---

## LOW

### L1. No newcomer overview / quickstart path
`architecture.md` is deep-internal and `metrics.md` is research-framed.
There is no single "what is this, why, install, first run" doc and no
end-to-end worked example. A new contributor has no on-ramp.
Fix: add an overview + quickstart (can live in README + a short
`docs/overview.md`).

### L2. Ingestion/parsing is under-documented
The parser backends (docling default, marker, lite) live only in
`CLAUDE.md`. The ingest pipeline (`ingest/parsers/*`, `chunker`,
`hybrid_chunker`, `topics`, `citations`, `figures`, `equations`,
`section_classifier`, `manifest`) has no doc. A newcomer cannot follow
how a PDF becomes corpus chunks + embeddings + KG.
Fix: add `docs/ingestion.md` covering the parse->chunk->embed->graph
pipeline and parser selection.

### L3. References management is barely covered
`citations/` (BibTeX parse, DOI/Crossref/OpenAlex resolution via
`resolver.py`, `util/doi_resolver.py`, pyzotero) and how author
metadata feeds GT-P / `author_context` get one line. No doc explains
reference resolution flow.
Fix: short `docs/references.md` or a section in ingestion.

### L4. Wiki HTML rendering/structure is undocumented
`render/html/` (`render.py`, `citation.py`, `templates/`) produces
per-page HTML plus `references.html` and `graph.html`. Docs give only
the one-line CLI mention. No description of site structure, navigation
JSON, or templates.
Fix: add a render section describing the site output and navigation.

### L5. Public-facing description is inconsistent
`pyproject.toml` describes "self-improving personal wiki"; the docs
frame it as evidence-grounded strategy science; README is empty. The
three diverge.
Fix: settle one project description and reuse it in README + pyproject.
