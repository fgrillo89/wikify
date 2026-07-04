# Rendering the wiki to a static site

This document covers the last stage of the pipeline: turning a bundle's
committed wiki into a browsable website. If you have not read
[overview.md](overview.md), start there — this document reuses its
vocabulary (corpus, bundle, wiki, page kinds, evidence, data artifact)
without redefining it.

## What the renderer does

A finished **bundle** holds its wiki as plain Markdown files: one file
per page under `wiki/articles/`, `wiki/people/`, and `wiki/data/`. Those
files are good for the agent to write and for version control to track,
but they are not something a person can browse. The **renderer** reads
those files and writes a complete static HTML website — a folder of
`.html`, `.css`, `.js`, and image files that opens in any browser, with
no server, database, or build step required to view it.

The renderer is **deterministic and read-only**. It never changes the
wiki; it only consumes the committed Markdown and produces a fresh site
folder. Running it twice on the same bundle yields the same site.

You invoke it with one command:

```
wikify render --bundle <bundle> --format html [--out <dir>] [--corpus <path>]
```

`--format html` is the only supported format. The site is written to
`<bundle>/derived/site` by default, or wherever `--out` points. The
optional `--corpus` path lets the renderer reach back into the corpus to
stage figures and source PDFs; when omitted it uses the corpus path
recorded in the bundle's run state. `--wiki-name` overrides the site's
display name.

## The site's display name

The header on every page shows a wiki name. If you do not pass
`--wiki-name`, the renderer derives one from the corpus folder's name: it
drops year numbers and parser-tool words (`docling`, `marker`, `lite`,
and so on), upper-cases short tokens that look like acronyms, and appends
"Wiki". So a corpus folder named `ald_docling_2026_05_15` becomes
**"ALD Wiki"**. With no corpus at all, the name falls back to
**"ScholarForge"**.

## Output structure

A rendered site is a self-contained folder laid out like this:

```
site/
  index.html            main page (the landing page)
  references.html       every cited source, one combined list
  graph.html            interactive map of links between pages
  articles/<slug>.html  one file per article page
  people/<slug>.html    one file per person page
  data/<slug>.html      one file per data table
  static/
    wiki.css            all styling
    search.js           the client-side search behaviour
    search-index.js     the search data (titles, urls, excerpts)
  assets/
    <figures and images copied from the corpus>
    figures/            staged figure images
    sources/            copied source PDFs, for direct download
```

Each page's file name is a URL-safe **slug** made from the page's title,
so "Atomic Layer Deposition" lands at `articles/atomic-layer-deposition.html`.

### Which pages get rendered

Not every committed page reaches the site. The renderer drops:

- **Skeleton pages** — any article or person page whose cleaned body is
  shorter than 200 characters. These are stubs the agent started but
  never grew into real prose, and shipping them would look broken. The
  renderer prints how many it skipped.
- **Person pages with a non-name title** — a biography page whose title
  looks like a journal name rather than a person is filtered out.

Data tables are always kept even when short, because a table's value is
its rows, not a prose body.

The surviving pages are sorted alphabetically within each kind:
articles, then people, then data tables.

## Page types

The renderer handles the three page kinds the wiki produces — **article**,
**person**, and **data** — through one shared template, varying the
small details per kind.

### Article pages

An article page is the common case: an encyclopedic write-up of one
concept. Rendering a single article runs the page's Markdown through a
sequence of transforms before it becomes HTML:

1. **Strip the front matter and duplicate title.** The Markdown file
   starts with a metadata block and a `# Title` heading; both are
   removed because the template renders the title itself.
2. **Place figures** (see "Figures" below).
3. **Tidy the math.** Display equations written as `$$...$$` are given
   their own blank lines so the math typesetter renders them as centered
   blocks instead of leaking stray `$` characters into the text.
4. **Turn evidence into citations.** The page's `## Evidence` block — the
   verbatim quotes the agent gathered — is reformatted into a numbered
   reference list in Wikipedia's standard **CS1** citation style (author,
   year, title, journal, volume, DOI), and the heading is renamed to
   `## References`. Two footnotes that point at the same paper are merged
   into one entry. Where the corpus knows a paper's DOI or has its PDF,
   the citation title becomes a clickable link.
5. **Resolve internal links.** The agent writes cross-page references as
   `[[wikilinks]]`. These are turned into ordinary HTML links to the
   target page. A wikilink whose target was not rendered (a dropped stub,
   say) degrades to plain text rather than a broken link — the site never
   ships dead internal links.
6. **Convert to HTML** with tables, footnotes, fenced code, and math
   support, then build a table of contents from the section headings.

The finished article page carries, around the body:

- An **infobox** in the corner summarizing the page (its type and how
  many sources it draws on).
- A **table of contents** when the page has more than two sections.
- A **"Related data"** list linking any data table that draws on a
  source this article also cites, so the matching numbers are one click
  away.
- A **"See also"** list. It starts with the explicit cross-links the
  writer chose; if those are few, it is topped up with other articles
  that share source documents with this one (a co-occurrence guess),
  capped at ten links.
- A **Categories** line naming the page kind.

### Person pages

A person page is a short biography of a notable author. It renders
through the same machinery as an article, with two differences: its
infobox reports how many papers the person authored and how many
collaborators they have (drawn from the page's provenance record), and a
page whose title does not look like a real author name is filtered out
before rendering.

### Data pages

A data page (`kind=data`) is a table of verifiable numbers harvested from
the corpus — for example a column of reported values across many papers.
It is part of the **data-artifact layer**, which is separate from the
wiki page graph (see the overview). On the site, though, data tables are
first-class: they get their own pages under `data/`, appear in the
sidebar and on the main page, and are linked from related articles.

Data pages are cited differently. Each cell of the table carries its own
grounded footnote — the exact quote the number came from. Unlike article
references, these per-cell footnotes are **never merged or reordered**,
because each one is a distinct fact with its own quote. The citation text
itself still uses the same CS1 formatting as the rest of the wiki, so
sources read consistently everywhere.

## Figures

Pages can show images, and the renderer pulls them from the corpus in
three ways, copying each chosen image into the site's `assets/` folder so
the site carries its own pictures:

1. **Inline images.** An image the writer embedded directly in the
   Markdown (`![caption](path)`) is copied across and its link rewritten
   to point inside `assets/`.
2. **Selected figures.** The agent can mark a spot in the prose with a
   placeholder for a specific corpus figure. The renderer swaps the
   placeholder for a proper `<figure>` block with the image, its caption,
   and a small superscript link from the caption back to the source
   quote.
3. **Fallback figure.** If a page ended up with no figure at all, the
   renderer looks through the corpus images belonging to that page's
   source documents and injects the first good captioned one (skipping
   tiny images and publisher banners). It tracks which images it has
   already used so two pages never show the same fallback figure.

Every figure gets readable alternate text for screen readers, taken from
its label or caption — never the raw internal image id.

## Navigation

The site is navigable three ways: a persistent sidebar, the main page,
and the article graph.

### The sidebar

Every page shows the same left sidebar, built from four sections:

- **Navigation** — links to the main page, the references list, and the
  article graph.
- **Topics** — a collapsible tree of topic groups. This comes from an
  optional **navigation file** (`derived/navigation.json`) that an
  organizer step writes: it groups pages into a named, nested hierarchy.
  Each group is a disclosure toggle and lists up to twelve pages; when it
  holds more, the extra pages collapse behind a "+N more" link to that
  group's full list on the main page. When the navigation file is absent,
  the Topics section is omitted and the sidebar falls back to a flat list
  of articles instead.
- **Statistics** — counts of articles, people, and sources.
- **People** and **Data tables** — short lists of those page kinds.

### The main page

`index.html` is the landing page. It opens with a one-line summary of how
many sources the wiki was compiled from and over what year range, a grid
of headline statistics (article count, people count, sources cited,
approximate words processed, figures included), and then:

- **Browse by topic** — when a navigation file exists, a grid of
  **cluster cards**, one per top-level topic group, each showing the
  group's title, its description, and how many pages it holds. This is the
  primary way to browse a large wiki: a card jumps to that group's full
  page list further down the page, where the same groups are expanded into
  a portal grid.
- **Key articles** — the eight best-connected articles (ranked by how
  many links and how much evidence they carry), each with a short
  excerpt.
- **People** and **Data tables** lists.

### The article graph

`graph.html` is an interactive, force-directed map of the wiki. Each page
is a node; each link between two pages is an edge. Edges are weighted —
a one-way link is thin, a mutual link (both pages link to each other) is
thicker — and better-connected nodes are larger.

Nodes carry two visual codes at once. Their **color** is their topical
cluster: pages are tinted by the top-level topic group they belong to (from
the same navigation file the sidebar uses), so one glance shows how the
clusters sit relative to each other. Their **shape** is their page kind —
articles are circles, people are diamonds, data tables are triangles.

The graph is built for exploring a large map without getting lost:

- **Hover focus and context.** Hovering a node highlights it and its direct
  neighbours along with the edges between them, and dims everything else, so
  you can trace one page's connections out of a dense cloud.
- **Search box.** Type in the search box to keep only the matching nodes lit;
  pressing Enter recenters the view on the first match.
- **Zoom controls.** Buttons zoom in, zoom out, and fit the whole graph back
  into view, alongside the usual scroll-to-zoom.
- **Labels that scale with zoom.** Only the most-connected nodes are labelled
  when you are zoomed out; more labels appear as you zoom in, and any node you
  hover or match in search is always labelled.
- **Interactive legend.** A legend lists the clusters with their colors and
  sizes, and the page-kind shapes; click a cluster to toggle it off and on.

You can still drag a node to reposition it and click one to jump to its page.
The graph is drawn with the D3 library.

## References page

`references.html` gathers **every** source cited anywhere in the wiki
into one master list, formatted in CS1 style and sorted by first-author
surname then year. Each entry lists which pages cite it, with links to
them ("Cited in: ..."). When the renderer can reach the corpus and a
cited paper has a source PDF, that PDF is copied into `assets/sources/`
and the reference links straight to it; otherwise the link is the paper's
DOI. This means a reader can get from any claim in the wiki to the actual
paper behind it.

## Search

The site has a working search box in the header with no server behind it.
The renderer writes a small **search index** — every page's title, URL,
and a short excerpt — into `static/search-index.js`. That file assigns
the index to a global variable, and the page loads it with an ordinary
`<script>` tag. As you type, the search script filters the index and
shows up to ten matching pages.

The reason it is a `<script>` tag and not a network fetch is important:
it means search works when the site is opened **directly from disk**
(a `file://` URL), where browsers block fetches. You can unzip the site
folder, double-click `index.html`, and search still works.

## Self-contained packaging

The goal is a folder you can move, zip, or hand to someone, and it just
works in a browser. The renderer supports this by copying everything the
pages reference into the site folder itself:

- All **styling** is one local `wiki.css`.
- All **figures and images** are copied into `assets/`.
- All available **source PDFs** are copied into `assets/sources/`.
- The **search index** is bundled as a local script, so search runs
  offline from `file://` as described above.

Two pieces are loaded from a public CDN rather than bundled: the **KaTeX**
library that typesets mathematics, and the **D3** library that draws the
article graph. Browsing pages, following links, reading citations, and
searching all work fully offline; only math typesetting and the graph
view need an internet connection the first time they load.
