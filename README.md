# Wikify

**Turn a folder of documents into a navigable, evidence-grounded wiki —
researched and written by an AI agent, with every claim traceable to a
real quote in your sources.**

[![CI](https://github.com/fgrillo89/wikify/actions/workflows/ci.yml/badge.svg)](https://github.com/fgrillo89/wikify/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/fgrillo89/wikify/branch/master/graph/badge.svg)](https://codecov.io/gh/fgrillo89/wikify)
[![License](https://img.shields.io/github/license/fgrillo89/wikify)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Point Wikify at a pile of PDFs (or DOCX / PPTX / HTML / Markdown). It
parses them into a searchable **corpus**, then an agent reads that corpus
the way a researcher would and writes a browsable encyclopedia —
articles, short biographies, and data tables — as a self-contained
static HTML site.

```mermaid
flowchart LR
    docs["Your documents<br/>PDF · DOCX · PPTX · HTML"] --> corpus[("Corpus<br/>chunked · embedded · graphed")]
    corpus --> loop{{"Wikify agent loop"}}
    loop --> wiki["Evidence-grounded wiki<br/>articles · bios · data tables"]
    wiki --> site["Static HTML site"]
```

## Why Wikify

- **Grounded, not generated.** Every factual sentence traces to a
  verbatim quote that really exists in your sources. A fabricated quote
  fails an automatic check and the page is rejected — no hallucinated
  citations.
- **Agentic and multi-tier.** A top-tier *editor* model plans and decides
  what to write; cheap *explorer* subagents do the bulk reading. You pay
  premium rates for judgment, not for page-turning.
- **Reads across documents, not one at a time.** It chases a thread
  through the corpus using vector similarity, the citation graph,
  multi-hop traversal, and topic clusters — like a researcher following
  references, not one-shot RAG.
- **Whole-corpus coverage.** It keeps working until topics saturate, so
  the wiki reflects what the documents collectively say, instead of
  answering one question and stopping.
- **Context-managed at scale.** Explorers summarize as they read; the
  editor reasons over scores and summaries, never raw dumps — so it
  holds up on large corpora.

## How wikification works

The agent works in rounds, like a research editor running a newsroom. It
never reads every document itself. Each round it sizes up the state,
dispatches cheap explorer subagents to gather **evidence** (verbatim
quotes) for the topics that need it, scores how ready each topic is, and
writes a page only once that topic crosses a maturity bar. Coverage gaps
feed the next round, so the wiki fills in until it saturates.

```mermaid
flowchart TB
    editor["Editor — top-tier model<br/>plans each round, decides what to write"]
    editor -->|dispatches cheap subagents| explorers["Explorers read the corpus"]
    explorers --> retrieval{{"Multi-modal retrieval"}}
    retrieval --> v["vector similarity"]
    retrieval --> g["citation + graph hops"]
    retrieval --> t["topic clustering"]
    explorers -->|verbatim quotes| dossier[("Evidence dossier")]
    dossier --> gate{"mature enough?<br/>coverage · sources · facets"}
    gate -->|not yet| editor
    gate -->|ready| write["Write page<br/>every claim cited, or rejected"]
    write --> committed[("Committed wiki")]
    committed -.->|coverage gaps feed back| editor
```

Two output layers come out of a run: the **wiki page graph** (articles
and biographies, linked together and queryable) and a separate
**data-artifact layer** of `kind=data` tables that harvest verifiable
numbers across the corpus and re-derive from a stored spec. See
[docs/overview.md](docs/overview.md) for the full loop.

## Quickstart

Requires Python >= 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/fgrillo89/wikify.git
cd wikify
uv sync
uv run wikify --help
```

First-run note: the default Docling parser downloads the Granite formula
model (~258 MB) plus layout/table models on the first parse. Pass
`--parser lite` for a model-free, CI-friendly path.

## Worked example

```bash
# 1. Ingest a folder of documents into a corpus
uv run wikify corpus build ./papers --out data/corpora/ald

# 2. Build the wiki. Run the `wikify` skill from Claude Code against the
#    corpus; it initialises a bundle and drives the editor/explorer loop.
#    The bundle is bootstrapped with:
uv run wikify run init --bundle bundles/ald --corpus data/corpora/ald

# 3. Render the committed wiki to a self-contained static site
uv run wikify render --bundle bundles/ald
#    -> bundles/ald/derived/site/index.html
```

The flow is `ingest -> wikify -> render`: a read-only **corpus**, the
agent loop that fills a **bundle** with grounded pages, then a static
**site**.

## Skills

Wikify is driven by four first-class skills (run them from Claude Code);
each wraps a deterministic `wikify` CLI and MCP surface.

- **`arxiv`** — harvest arXiv papers for a topic and stage them for a
  build: scout categories, harvest metadata, download PDFs.
- **`ingest`** — parse a directory of documents into a queryable corpus;
  owns parser-backend choice and post-build health checks.
- **`wikify`** — the researcher-style agent loop. An editor dispatches
  explorer subagents that walk the corpus, gather evidence into dossiers,
  and write pages once a maturity score crosses the gate; a DATA wave
  harvests verifiable numbers into `kind=data` tables.
- **`query`** — answer a question from the committed wiki, falling back to
  corpus search when the wiki is insufficient, and recording feedback.

## Use as an MCP server

Wikify exposes its corpus and wiki search tools to Claude Code over a
stdio MCP server. To wire it into a project:

```bash
cp .mcp.json.example .mcp.json
# edit WIKIFY_CORPUS in .mcp.json to point at a built corpus, e.g.
#   data/corpora/ald
```

Reload Claude Code and approve the one-time "Use this project's MCP
server `wikify`?" prompt. The `mcp__wikify__*` tools then load. The
server binds a corpus from `WIKIFY_CORPUS`, or autodetects one when
launched from inside a corpus directory. `.mcp.json` is gitignored
(it holds machine-specific paths); the committed
[`.mcp.json.example`](.mcp.json.example) is the template. Alternatively,
install the bundled Claude plugin under `.claude-plugin/`, which ships
the same server plus the skills and prompts for a corpus path on install.

## Documentation

Start at the [docs overview](docs/overview.md), then follow the branch you
need. Full map in [docs/README.md](docs/README.md).

- [overview.md](docs/overview.md) — concepts (corpus, chunk, bundle, wiki,
  dossier, evidence, maturity, data artifact) and the agent loop.
- [architecture.md](docs/architecture.md) — agent runtime, CLI/MCP tools,
  the on-disk bundle, citation grounding, telemetry.
- [filesystem-state-design.md](docs/filesystem-state-design.md) — the
  durable on-disk contract for a bundle.
- [ingestion-and-parsing.md](docs/ingestion-and-parsing.md) — files to
  corpus chunks, embeddings, and graph.
- [wiki-rendering.md](docs/wiki-rendering.md) — the static HTML site.
- [metrics.md](docs/metrics.md) — the evaluation metrics over a bundle.
- [databases.md](docs/databases.md), [vector-search.md](docs/vector-search.md),
  [references.md](docs/references.md) — storage, search, and reference
  resolution internals.

## Contributing

Dev setup is `uv sync`; lint with `uv run ruff check src/wikify tests/wikify`
and test with `uv run pytest tests/wikify -q`. See
[CONTRIBUTING.md](CONTRIBUTING.md) for branch, commit, and review conventions.

## License

MIT. See [LICENSE](LICENSE).
