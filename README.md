# Wikify

Turn a folder of documents into a navigable, evidence-grounded wiki.

[![CI](https://github.com/fgrillo89/wikify/actions/workflows/ci.yml/badge.svg)](https://github.com/fgrillo89/wikify/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/fgrillo89/wikify/branch/master/graph/badge.svg)](https://codecov.io/gh/fgrillo89/wikify)
[![License](https://img.shields.io/github/license/fgrillo89/wikify)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](pyproject.toml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Wikify takes a pile of source files (PDF, DOCX, PPTX, HTML, Markdown) and
produces a browsable encyclopedia: articles, short biographies, and data
tables, rendered to a self-contained static HTML site. An AI agent reads
the corpus the way a researcher would — starting from the most important
papers, following ideas across documents, and writing a page only once a
topic is well understood. Two properties set it apart from chat-over-docs:

- **Grounding.** No page is written from model memory. Every claim carries
  a citation pointing to a verbatim quote that exists in the corpus; a
  fabricated quote fails an automatic check and the page is rejected.
- **Coverage.** Wikify works through the whole corpus, building pages until
  the set of topics is saturated, so the wiki reflects what the documents
  collectively say rather than answering one question and stopping.

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

## Skills

Wikify is driven by four first-class skills (run them from Claude Code);
each wraps a deterministic `wikify` CLI and MCP surface.

- **`arxiv`** — harvest arXiv papers for a topic and stage them for a build:
  scout categories, harvest metadata, download PDFs.
- **`ingest`** — parse a directory of documents into a queryable corpus;
  owns parser-backend choice and post-build health checks.
- **`wikify`** — the researcher-style agent loop. An editor dispatches
  explorer subagents that walk the corpus, gather evidence into dossiers,
  and write pages once a maturity score crosses the gate; a DATA wave
  harvests verifiable numbers into `kind=data` tables.
- **`query`** — answer a question from the committed wiki, falling back to
  corpus search when the wiki is insufficient, and recording feedback.

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

The flow is `ingest -> wikify -> render`: a read-only **corpus**, the agent
loop that fills a **bundle** with grounded pages, then a static **site**.

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
