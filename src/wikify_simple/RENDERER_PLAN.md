# Renderer Port Assessment (legacy `wikify` -> `wikify_simple`)

Status: research note, no code yet.
Scope: can `wikify_simple` bundles become a Wikipedia-style HTML site the
same way the legacy `wikify` package did, and how should figures be placed
near the claims that reference them?

## What the legacy renderer actually does

Legacy code lives under `src/wikify/wiki/presentation/`:

- `html.py` (755 LOC) — the orchestrator: walks the visible wiki dir,
  parses frontmatter, renders markdown to HTML, groups categories and
  domains, copies assets, and writes per-page `.html` files.
- `layout.py` (148 LOC) — filesystem layout + frontmatter helpers
  (`iter_visible_page_files`, `normalize_page_type`).
- `dashboard.py` (779 LOC) — a separate "run dashboard" view, orthogonal
  to the Wikipedia-style article site. Not required for a port.
- `templates/` — Jinja2 templates + CSS:
  - `base.html` (40): HTML skeleton, loads KaTeX from cdn.jsdelivr.net
    for math rendering, links `wiki.css`.
  - `article.html` (46): title, infobox table, TOC, article body,
    categories footer.
  - `index_page.html`, `category.html`, `categories_index.html`,
    `domain_index.html`, `people.html`, `recent.html`, `random.html`,
    `sidebar.html` — small listing templates (<40 LOC each).
  - `wiki.css` (612) — a hand-written Wikipedia-ish theme.
- CLI: `wikify wiki html` verb in `src/wikify/cli/wiki.py:666` calls
  `build_site(wiki_dir, output_dir)` and optionally `serve_site` via
  `http.server`.

Dependencies actually imported: `jinja2`, `markdown` with the extensions
`tables`, `fenced_code`, `toc`, `attr_list`, `def_list`, `footnotes`,
`pymdownx.superfences`, `pymdownx.tasklist`. Math is done client-side via
KaTeX from a CDN (no Python dep). Code highlighting via `pymdownx` +
`pygments` (pulled transitively).

Output shape: a directory of static per-page HTML files with a shared
`wiki.css`, per-category and per-domain index pages, a site root
`index.html`, and a recent/random page. Cross-page links use the
`links:` frontmatter field resolved against page slugs. Footnotes use
the `markdown.footnotes` extension, which is exactly the `[^e1]` syntax
`wikify_simple` already emits — so evidence blocks will render as real
footnotes with back-references for free.

### Five-bullet summary of the legacy renderer

- Modules: `presentation/html.py` (orchestrator), `presentation/layout.py`
  (frontmatter + file walking), `presentation/templates/*.html` (10 Jinja2
  templates), plus a separate `dashboard.py` for run telemetry.
- Templating: Jinja2 with `base.html` extending and `article.html` etc.
  as content blocks; category/domain/recent/random listing templates.
- Output: static per-page `.html` files under `<wiki>/_site/` with a
  shared `wiki.css`, category/domain indexes, and a root index.
- Dependencies: `jinja2`, `markdown`, `pymdownx` (superfences + tasklist,
  pulls in `pygments`), KaTeX from CDN, stdlib `http.server` for preview.
- CSS: one hand-written 612-line `wiki.css` that mimics Wikipedia's
  infobox, TOC, categories footer, sidebar, and article typography.

## What `wikify_simple` would need

Minimum viable renderer for a `wikify_simple` bundle:

- Package `src/wikify_simple/render/` with one file `html.py` (~150 LOC)
  that takes a bundle dir, walks `concepts/*.md` and `people/*.md`, and
  emits `<bundle>/_html/` with per-page HTML + `index.html` + `wiki.css`.
- One Jinja2 template `article.html.j2` (~40 LOC) extending a minimal
  `base.html.j2` (~25 LOC). No category/domain/recent/random pages in
  the first slice — the simple bundle does not track those.
- One CSS file trimmed from the legacy `wiki.css` (~200 LOC). Drop the
  dashboard styles and category sidebar; keep infobox, TOC, article body,
  and footnotes.
- A `wikify-simple html --bundle <dir> [--out <dir>]` CLI verb in
  `src/wikify_simple/cli.py` that calls `render.html.build_site`.
- Dependencies to add via `uv add`: `jinja2`, `markdown`, `pymdown-extensions`.
  KaTeX still loads from CDN; no Python math dep.

Total port budget: roughly 150 LOC Python + 65 LOC Jinja + 200 LOC CSS =
~400 LOC of new code, plus 3 `uv add`s. No schema change to the bundle.

Honest estimate: **small**. The legacy `html.py` is 755 LOC but most of
that is category/domain/people/recent/random plumbing that the simple
bundle does not have. The core "walk markdown files, render with
`markdown`, drop into a Jinja template" pipeline is maybe 80 LOC.

### Alternative: adopt an existing markdown-to-site tool

`wikify_simple` already emits a clean markdown directory with YAML
frontmatter and footnotes. Several off-the-shelf tools consume that
shape directly:

- **mkdocs-material**: Python, mature, Wikipedia-ish themes exist
  (e.g., `mkdocs-wiki`). ~0 LOC of renderer code; just a `mkdocs.yml`.
- **Quartz**: TypeScript, born for Obsidian-style wikilinks, handles
  backlinks and graph view out of the box. ~0 LOC but adds a Node
  toolchain.
- **Docusaurus**: heavier, stronger for versioned docs; overkill here.

Recommendation: **do both in two slices**. Slice 1 is mkdocs-material
with a minimal config (an afternoon of work, zero Python changes). Slice
2, only if the Wikipedia look is load-bearing, is the ~400 LOC custom
port. The legacy port gives tighter control of the infobox and the
evidence-block layout; mkdocs gives search, nav, and a theme ecosystem
for free.

## The figure-placement question

Today the writer skill embeds at most one `![Figure 1](images/...)` per
page, typically at the top. The user wants each figure placed adjacent to
the sentence that references it. Two approaches:

### Option A — writer-prompt change (recommended)

Add an explicit instruction to the writer skill: "when you reference a
figure, embed its markdown image on the line immediately after the
sentence that mentions it, not at the top of the page." Also: "every
figure you embed must be referenced by name ('Figure 1', 'Fig. 2') in
the nearest preceding sentence."

Pros: zero schema change, zero post-processing, zero new tests. The
model is already deciding figure placement; this just redirects its
hand. Works uniformly for markdown and HTML output.

Cons: model-dependent. Cheap extractors may skip the instruction. We
should add a `write_validator` rule: if a page has N embedded images but
fewer than N sentences mentioning "Figure" / "Fig.", flag it.

### Option B — post-processing pass

In `eval/bundle.py` or a new `render/figures.py`: parse `![Figure
N](path)` tokens and "Figure N" / "Fig. N" mentions, move each image to
live immediately after its nearest preceding mention, or append at the
end if no mention exists.

Pros: model-agnostic, deterministic.

Cons: brittle parsing, mutates body text, creates a second source of
truth for page layout. The writer prompt still has to be told to
*reference* the figure by name, so Option A is required anyway for the
matching step to work.

### Recommendation

Do Option A alone. Add a one-line writer-skill instruction and a
validator rule. Keep Option B on the shelf as a fallback only if the
validator flag rate stays above ~10% after the prompt change.

## Recommended next slice

Two small, sequential steps:

1. **Writer prompt + validator**: one instruction in the writer skill,
   one rule in `write_validator`. ~20 LOC, one test. Unblocks
   figure-adjacent claims today, regardless of renderer choice.

2. **Renderer**: ship the mkdocs-material config first (a
   `src/wikify_simple/render/mkdocs_template.yml` + a `wikify-simple
   html` verb that runs `mkdocs build` against a generated nav). If the
   Wikipedia look is load-bearing, follow with the ~400 LOC custom port
   and the trimmed `wiki.css`.

Do not port the legacy `dashboard.py`. The simple harness already writes
`_metrics.md` and `_run.json`, which is enough for now.
