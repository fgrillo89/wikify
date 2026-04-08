# wikify_simple

A minimal restart. One sentence per layer, no shims, no legacy.

## What the system does

1. **Ingest** raw documents (pdf, docx, pptx, html, md) into a normalized
   corpus on disk: markdown text + extracted images + chunks + embeddings.
2. **Build a corpus graph** over the corpus (documents, chunks, similarity,
   optional citations) for navigation and sampling.
3. **Distill wikis** (concept pages + people pages) from the corpus by letting
   an agent sample a *fraction* of the corpus, guided by the corpus graph and
   small models. Wikis cross-link each other.
4. **Build a wiki graph** over the distilled wikis for navigation, telemetry,
   and benchmarking.
5. **Report metrics + telemetry** over runs and over the wiki graph.

The analogy: training a big model samples a fraction of data to distill into
weights. Here we sample a fraction of the corpus to distill into a network of
wiki pages.

## Core principle: three layers, one direction

```
raw files
   |
   v
[ Corpus ]            files on disk + vector store + corpus graph
   |
   v
[ Wikis ]             markdown files on disk (the source of truth)
   |
   v
[ Wiki graph + metrics ]
```

Hard rules:

- `ingest` never reads wikis.
- `distill` reads the corpus, writes wiki markdown files. Never mutates the
  corpus.
- `wikigraph` reads wiki files, never mutates them.
- `metrics`/`telemetry` read everything, write only into `runs/`.

That single arrow is the whole architecture.

## Source-of-truth rule (important)

Every artifact has exactly one source of truth, and it is the most inspectable
form possible:

| Artifact         | Source of truth                       | Derived from it                |
|------------------|---------------------------------------|--------------------------------|
| Document text    | `corpus/markdown/{doc_id}.md`         | chunks, embeddings             |
| Document images  | `corpus/images/{doc_id}/`             | (none)                         |
| Chunks           | `corpus/chunks/{doc_id}.jsonl`        | vector store rows              |
| Embeddings       | vector store (chroma / lancedb / ...) | similarity edges               |
| Corpus graph     | `corpus/graph.json` (or .parquet)     | sampling decisions             |
| **Wiki pages**   | **`wiki/concepts/{slug}.md` and `wiki/people/{slug}.md`** | wiki graph, metrics |
| Wiki graph       | `wiki/_graph.json`                    | metrics                        |
| Runs / telemetry | `runs/{run_id}/...`                   | reports                        |

The wikis are **markdown files on disk**. They are the product. There is no
database row that "really" holds the page — the file is the page. A SQLite db
is fine for *operational* state (run history, sample logs, vector index if we
use sqlite-vss) but it must never be the canonical home of a wiki page.

A wiki page file looks like:

```markdown
---
id: concept-photocatalysis
kind: concept
title: Photocatalysis
aliases: [photo-catalysis, light-driven catalysis]
links: [concept-tio2, person-fujishima]
provenance:
  run_id: 2026-04-07T12-00-00
  model: haiku
  sampled_chunks: [chunk_abc12, chunk_def34, ...]
---

# Photocatalysis

Photocatalysis is ... [^e1]

## History

First demonstrated by Fujishima and Honda in 1972 [^e2].

## Evidence

[^e1]: chunk_abc12 (doc_xyz, p.3) > "Photocatalysis refers to the
       acceleration of a photoreaction in the presence of a catalyst."
[^e2]: chunk_def34 (doc_uvw, p.1) > "In 1972 Fujishima and Honda
       reported ..."
```

The frontmatter is the structured part. The body is the human part. The
`[^eN]` footnotes are the evidence anchors. Both a human and a parser can
read this file with no extra context. That is the whole point.

## Data structures

These are the contracts. Everything else is implementation.

### Corpus side

- `Document` — `id`, `source_path`, `kind`, `title`, `metadata`,
  `markdown_path`, `image_dir`.
- `Chunk` — `id`, `doc_id`, `ord`, `text`, `char_span`, `section_path`.
  Embedding lives in the vector store, keyed by `chunk.id`.
- `CorpusGraph` — nodes are `Document` and `Chunk` ids; edges are typed:
  - `contains`: doc -> chunk
  - `similar`: chunk <-> chunk (kNN over embeddings; the *only* edge type
    we always have, because it needs nothing but the vector store)
  - `cites`: doc -> doc (only if the parser found citations; optional)
  - `co_section`: chunk <-> chunk (same section in same doc; cheap)

Notes on the corpus graph:

- The vector store is **not redundant** with the graph. The vector store
  answers "what is closest to this query vector"; the graph answers "starting
  from this chunk, what is reachable in N hops along which edge types". The
  graph edges are *materialised* from the vector store (and from citations
  when present) so that sampling strategies can walk them cheaply without
  re-querying vectors every time. Different sampling strategies want
  different graphs (similarity-only, citation-backbone, hybrid), so the
  corpus graph is built once per ingest and re-buildable.
- We do not put `Entity` nodes in the corpus graph at ingest time. (Earlier
  draft mentioned them; dropping. "Entity" was a vague catch-all for
  "named things noticed during parsing" — authors, cited works, chemicals.
  We do not need them as graph nodes: authors live in `Document.metadata`,
  cited works become `cites` edges if available, and everything else is
  better discovered during distillation, where the agent decides what is
  actually a concept worth a page.)

### Wiki side

- `WikiPage` (in-memory representation of a `.md` file)
  - `id`, `kind` (`concept` | `person`), `title`, `aliases`
  - `body_markdown` (the human prose)
  - `evidence: list[Evidence]`
  - `links: list[str]` (other wiki page ids)
  - `provenance: dict` (run_id, model, sampled_chunks)
- `Evidence`
  - `marker`: the footnote label used in the body, e.g. `e1`
  - `chunk_id`: the corpus chunk this claim came from
  - `doc_id`: redundant but convenient for display
  - `quote`: the exact span of text from the chunk that supports the claim
  - `locator`: optional human-readable locator (page, section, slide)

  An `Evidence` entry is the bridge between a sentence in a wiki page and a
  specific piece of the corpus. It is what makes the wiki *anchored*: every
  factual claim in `body_markdown` should reference an evidence marker, and
  every marker resolves to a real chunk we actually sampled. Evidence is
  serialised as the `[^eN]: ...` footnote block in the page file, so it is
  inspectable in the same file as the prose.

- `WikiGraph` — nodes are wiki page ids; edges:
  - `links_to` (explicit cross-links from `links`)
  - `co_evidence` (two pages cite the same chunk)
  - `same_domain` (clustering over page bodies)

### Run side

- `Run` — `id`, `started_at`, `finished_at`, `config_hash`, `stages`,
  `sampled_chunks`, `pages_touched`, `metrics`.
- `Stage` — `name`, `t_start`, `t_end`, `counters`, `cost`.

## People are not concepts

Concepts and people are separate `kind`s with separate directories
(`wiki/concepts/`, `wiki/people/`) and separate templates. They share the
`WikiPage` shape but the prompts, the evidence patterns, and the link
semantics differ:

- a concept page is built from chunks that *describe an idea*
- a person page is built from chunks that *attribute work to a name* plus
  document metadata (authors, affiliations)

Keeping them separate avoids the agent conflating "Fujishima" with
"Fujishima-Honda effect".

## Layout

```
src/wikify_simple/
  __init__.py
  config.py            # one dataclass, loaded from a single yaml
  paths.py             # where things live on disk; the only place that knows

  models.py            # the dataclasses above

  ingest/
    __init__.py        # ingest_path(path) -> Document
    parsers.py         # one function per kind, returns (markdown, images)
    chunker.py         # markdown -> [Chunk]
    embedder.py        # [Chunk] -> writes to vector store
    corpus_graph.py    # builds CorpusGraph from chunks + similarity (+ cites)

  store/
    __init__.py
    vectors.py         # thin wrapper over the vector db
    files.py           # read/write corpus markdown, images, chunk jsonl
    wiki_files.py      # read/write wiki page .md files (frontmatter + body)
    runs.py            # operational state for runs (sqlite is fine here)

  distill/
    __init__.py        # distill(run_cfg) -> Run
    sampler.py         # picks which chunks to feed the agent next
    agent.py           # AgentExtractor / AgentWriter protocols
    steps.py           # the explicit pipeline steps (see below)
    merge.py           # candidate -> canonical WikiPage merge
    crosslink.py       # wiki<->wiki linking pass

  wikigraph/
    __init__.py        # build_wiki_graph(wiki_dir) -> WikiGraph
    metrics.py         # importance, coverage, redundancy, orphan rate

  telemetry/
    __init__.py        # Run lifecycle + structured event log
    report.py          # human-readable run summary

  cli.py               # ingest / distill / report
```

No `core/`, no `operations/`, no `presentation/`, no `legacy/`.

## Distillation strategies

The distillation step is *not* a single algorithm. Several strategies
implement the same `distill(...)` entry point and can be selected per run.
They are described in detail in [`strategies.md`](./strategies.md):

- **S0 brute_force** — oracle baseline; large model reads everything.
- **S1 concept_walk** — concept-driven graph traversal with per-hop
  read-depth decisions (full doc / section / chunks / summary).
- **S2 pagerank_extract** — pagerank-weighted doc sampling, small-model
  per-doc extract, single large-model curate, medium-model write. **v1
  primary.**
- **S3 hierarchical_mr** — embedding-cluster map-reduce.
- **S4 agent_explorer** — single agent driving the corpus through a tool
  surface.

The pipeline section below describes the *shape* every strategy follows
(seed → expand → canonicalise → write → cross-link → graph → finalise).
Each strategy plugs different sampling, extraction, and curation logic
into that shape.

## The distillation pipeline shape

A fixed list of small steps. Each step is a function with a typed input and
a typed output. No DAG framework, no YAML. The agent is called *inside*
steps, with narrow context.

```
Step 1. profile_corpus
   in : CorpusGraph + vector store
   out: CorpusProfile  (size, doc kinds, rough domains, hot subgraphs)
   how: pure python; cluster embeddings into rough domains; pick high-
        centrality chunks as "hot regions"

Step 2. seed
   in : CorpusProfile, budget B1, existing wiki dir (may be empty)
   out: [CandidateConcept], [CandidatePerson]
   how: sampler picks B1 chunks from hot regions; small-model agent reads
        them and emits candidate concepts AND candidate people, each
        anchored to the chunks they came from. Existing pages are passed
        in as "already known" so the agent can avoid re-seeding them.

Step 3. expand
   in : candidates, budget B2
   out: candidates (enriched + possibly NEW candidates)
   how: for each candidate, sampler walks the corpus graph from its
        evidence chunks (similarity + co-section + cites) to fetch more
        chunks; agent re-reads, refines, AND is allowed to spawn new
        candidates it discovers along the way (with their own evidence).
        New candidates feed back into the same step until B2 is spent or
        no new candidates appear in a pass.

Step 4. canonicalize
   in : candidates + existing wiki pages on disk
   out: [WikiPage] skeletons (concept + person), each marked new|update|merge
   how: deterministic merge by alias + embedding similarity. For each
        candidate, decide:
          - new page
          - update of an existing page (same id)
          - merge with an existing page (alias/embedding match)
        No agent.

Step 5. write
   in : skeletons, budget B3
   out: WikiPage with body_markdown + evidence
   how: per page, agent gets:
          - the skeleton (and the existing page body, if updating/merging)
          - its evidence chunks (already sampled; no new sampling)
          - 1-hop neighbors in the corpus graph for context
        agent writes/rewrites the markdown, anchored to evidence markers.
        On merge, the agent is told "integrate the new evidence into the
        existing page; do not delete claims that still have valid evidence."

Step 6. cross_link
   in : all wiki pages (new + untouched)
   out: pages with `links` populated
   how: embedding kNN over page bodies + alias matching; agent only used
        for ambiguous ties.

Step 7. build_wiki_graph
   in : wiki dir
   out: WikiGraph (written to wiki/_graph.json)

Step 8. finalize
   in : WikiGraph, Run
   out: metrics + telemetry written under runs/{run_id}/
```

Properties of this pipeline:

- **Sampling is explicit and budgeted.** Each step that calls the agent has
  a budget `B_i`. Total cost is bounded and visible.
- **The agent is called from exactly four places**: steps 2, 3, 5, and as
  a tie-breaker in 6.
- **Context is distributed**: each agent call gets a small focused context
  (a handful of chunks + a skeleton), never the whole corpus.
- **Re-runs are merge-or-add.** Step 4 explicitly classifies each candidate
  as new / update / merge against the wiki dir on disk. A second run with
  a bigger budget grows and refines the same wiki; it does not replace it.
  The wiki dir IS the state across runs.
- **Every claim is anchored.** A page whose body has unanchored factual
  sentences fails a lint pass at the end of step 5. One hard quality gate.

## What we deliberately drop from the current repo

- `core / ingest / wiki / papers` four-boundary split. `papers` is gone for
  v1; if it comes back it sits *next to* `wikify_simple`.
- `wiki/operations/`, `wiki/presentation/`, `wiki/legacy/`, `wiki/recipes/`,
  `wiki/discovery/{dag,executor,planner,strategies,nodes,workflows}`.
- `WikiUpdateBundle`, `EpochLog`, recipe compiler, `DagRunSpec`,
  `DocumentProfile`, `ExtractionUnit`, `ExtractionNote`, `CoverageRecord`,
  `DagNodeSpec`.
- All compatibility shims and lazy-import allowlists.
- YAML workflow configuration. One python `RunConfig`, one yaml file.
- Storing wiki pages in a database. The wiki is files.

## Open questions for the next iteration

1. Vector store choice: chroma, lancedb, sqlite-vss, or numpy+faiss? Since
   the wiki is no longer in sqlite, we have more freedom here. Lancedb is
   probably the cleanest "files on disk" option and matches the inspectable
   spirit. Chroma is the most boring. Pick one.
2. Exactly which corpus-graph edge types do we materialise at ingest time?
   Proposal: `contains`, `similar` (kNN, k=10), `co_section`, and `cites`
   only when the parser produced citations. Anything else?
3. Sampler interface: should the sampler be a single function with a
   `strategy` enum (`hot`, `walk`, `random`, `coverage`) or a small set of
   functions? Lean toward a small set.
4. Agent interface: one `Agent` protocol with `extract_candidates` and
   `write_page` methods, or two protocols? Lean toward two — they have
   very different prompts and budgets.
5. How does step 3's "spawn new candidates" terminate? Proposal: hard cap
   on total candidates per run + a "no new candidates this pass" stop
   condition, whichever comes first.
