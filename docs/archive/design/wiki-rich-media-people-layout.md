# Wiki Enrichment: People, Layout, Equations, Images & Tables

> **Status: All five features are implemented and tested.** This document is retained as
> the design reference. For current project status, see `docs/project-status.md`.

## Overview

Five features that bring Wikify closer to a real Wikipedia experience:

1. **People/Author identification** — Discover and write biography pages for people mentioned
   across the corpus, not just from author lists.
2. **Wikipedia HTML layout** — Render the wiki as navigable HTML with proper Wikipedia-style
   sections, infoboxes, TOC, sidebar, categories, and breadcrumbs.
3. **Equation extraction** — Parse mathematical and chemical equations from source text;
   use them to enrich concept articles and identify new concepts.
4. **Image & table ingestion** — Extract images and tables via pymupdf4llm, associate them
   with source documents, and make them retrievable for article writing.
5. **Haiku vision for extraction** — Use Haiku's multimodal capabilities to extract knowledge
   from figures and tables during Pass 1; allow larger models to inspect images during writing.

---

## 1. People / Author Identification

### Problem

The current wiki discovers concepts (techniques, materials, phenomena, etc.) but never
identifies **people**. Authors are stored as JSON strings on `Paper.authors` but are never
elevated to wiki entities. People are mentioned throughout paper text — in introductions
("pioneered by Strukov et al."), in methods ("using the Yang model"), in acknowledgments —
but these mentions are invisible to the concept extraction pipeline.

### Design

Add `person` as a new concept type alongside `technique`, `material`, `phenomenon`, etc.

**Discovery (Pass 1 extension):**

Extend the extraction template (`data/wiki/_template.md`) to include a `people` section:

```json
{
  "concepts": [...],
  "people": [
    {
      "name": "Dmitri Strukov",
      "aliases": ["D.B. Strukov", "Strukov et al."],
      "role": "researcher",
      "affiliations": ["UC Santa Barbara"],
      "contributions": ["demonstrated first memristor crossbar array"],
      "mentioned_context": "pioneer | cited_author | collaborator | theorist"
    }
  ]
}
```

People are stored as `ConceptRecord` rows with `concept_type = "person"`. This means they
participate in the concept graph, get importance scores, and can be cross-referenced like
any other concept.

**Deduplication:**

People are harder to deduplicate than concepts. The merge pipeline must handle:
- Name variants: "J.J. Yang", "J. Joshua Yang", "Joshua Yang", "Yang, J.J."
- Same surname, different person (common in CJK names)

Strategy: normalize to "Firstname Lastname" form, then fuzzy match with threshold 0.85.
When ambiguous, keep separate records and let the LLM merge during maintenance.

**Cross-reference with Paper.authors:**

After Pass 1 discovers people, cross-reference against `Paper.parsed_authors`. If a discovered
person matches an author, link the `ConceptRecord` to all papers they authored. This gives
biography pages a "Publications in this corpus" section for free.

**Biography article template:**

Add a `person` template to `src/wikify/prompts/article_templates.py`:

```
Structure:
1. **Lead** (2-3 sentences): Full name, role, primary affiliation, why they matter in this field.
2. **## Contributions**: Key research contributions mentioned in this corpus.
   Cite with [REF:paper_display].
3. **## In This Corpus**: Which papers they authored or are cited in.
   Table: | Paper | Role | Year |
4. **## Collaborators**: Other people they co-author with or are frequently cited alongside.
   Link with [[wikilinks]].
5. **## Key Concepts**: Concepts this person is most associated with.
   Link with [[wikilinks]].
```

**Infobox data (for HTML layout):**

```yaml
---
concept: Dmitri Strukov
type: person
affiliations: ["UC Santa Barbara"]
role: researcher
corpus_papers: 5
first_cited_year: 2008
---
```

**Document-type generalization:**

The extraction prompt must NOT assume academic papers. A corpus might contain blog posts,
reports, README files, or slide decks. The prompt should say:

> "Identify people mentioned in this source — authors, researchers, practitioners,
> historical figures, collaborators, or anyone whose contribution is described.
> Do NOT limit to the author list."

### Data model changes

No new tables needed. `ConceptRecord` with `concept_type = "person"` is sufficient.
Add a `PersonInfo` JSON field to `ConceptRecord` (or use the existing `definition` field
as structured JSON for person metadata: affiliations, role, known_for).

### File layout

```
data/wiki/domains/{domain}/people/
  dmitri_strukov.md
  j_joshua_yang.md
```

People articles live under `people/` instead of `concepts/` within each domain directory.

---

## 2. Wikipedia HTML Layout

### Problem

The wiki currently outputs Markdown files browsable in Obsidian. There is no rendered HTML
view with Wikipedia-style navigation: no sidebar, no TOC, no infoboxes, no category pages,
no search. Machine-readable hashes (paper IDs, concept IDs) appear in frontmatter but should
not render in the HTML.

### Design

Generate a static HTML site from the wiki markdown, styled to resemble Wikipedia.

**Technology choice: Static site generator**

Use a lightweight Python-based approach:
- Parse existing markdown + YAML frontmatter
- Render to HTML with Jinja2 templates
- Style with a Wikipedia-like CSS theme
- Serve locally via `python -m http.server` or the existing FastAPI dashboard

This avoids heavy dependencies (no Hugo/Jekyll/MkDocs). The generator is a new module:
`src/wikify/wiki/html.py`.

**Page structure (per-article):**

```
+------------------------------------------------------------------+
| HEADER: Wiki title + search bar                                  |
+------------------------------------------------------------------+
| SIDEBAR          | ARTICLE                                       |
|                  |                                                |
| - Main page      | TITLE (h1)                                   |
| - Index           |                                               |
| - Domains         | INFOBOX (right-floated)                       |
|   - Domain 1     | +-----------+                                 |
|   - Domain 2     | | Type      |                                 |
| - People          | | Status    |                                 |
| - Recent changes  | | Domain    |                                 |
| - Random page     | | Importance|                                 |
|                  | +-----------+                                 |
|                  |                                                |
|                  | Lead paragraph (no heading)                    |
|                  |                                                |
|                  | TABLE OF CONTENTS                              |
|                  | 1 Mechanism                                    |
|                  | 2 Properties                                   |
|                  | ...                                            |
|                  |                                                |
|                  | == Mechanism ==                                |
|                  | Body text with [[wikilinks]] rendered as       |
|                  | <a href="..."> links.                          |
|                  |                                                |
|                  | == See Also ==                                 |
|                  | == References ==                               |
|                  | == Categories ==                               |
|                  | [domain] [type] [status]                       |
+------------------------------------------------------------------+
```

**Infobox:**

Generated from YAML frontmatter. Different layouts per concept type:
- **Material**: formula, crystal structure, key properties
- **Technique**: type, key parameters
- **Person**: affiliations, role, corpus papers count
- **Generic**: type, domain, importance, status, epoch

Machine-readable fields (`id`, `wiki_id`, file hashes) are **read by the generator but
NOT rendered** in the HTML output.

**Navigation elements:**

| Element | Source | Implementation |
|---------|--------|---------------|
| Sidebar | Domain list + special pages | Jinja2 partial, generated once |
| TOC | Auto from h2/h3 headings | JavaScript or build-time extraction |
| Breadcrumbs | Domain > Category > Article | Built from article path |
| Category pages | Group articles by type, domain, status | Auto-generated index pages |
| Search | BM25 over rendered text | Client-side lunr.js or server-side |
| Recent changes | EpochLog + article updated_at | Auto-generated special page |
| Wikilinks | `[[concept name]]` in markdown | Resolved to `<a href="slug.html">` |
| Disambiguation | Concepts with same slug | Auto-generated when collisions detected |

**Special pages:**

- `_index.html` — Main page (library catalog)
- `_recent.html` — Recent changes (from EpochLog)
- `_categories.html` — All categories
- `_people.html` — All people
- `_random.html` — JavaScript redirect to random article
- Per-domain index pages

**CSS theme:**

A single `wiki.css` file mimicking Wikipedia's Vector skin:
- Max-width content area (~960px)
- Left sidebar (~200px)
- Serif body text (Georgia/Times), sans-serif headings
- Blue wikilinks, red links for missing articles
- Infobox with light gray background, right-floated
- Category bar at bottom

**Image rendering:**

Figures associated with articles render as thumbnails with captions, Wikipedia-style:
```html
<figure class="wiki-figure">
  <img src="figures/ab/cd/abcd1234.png" alt="Fig. 1">
  <figcaption>Fig. 1: I-V characteristic of HfO2 memristor</figcaption>
</figure>
```

**Build command:**

```
wikify wiki html                    # Build static site to data/wiki/_site/
wikify wiki html --serve            # Build and serve on localhost:8080
wikify wiki html --serve --watch    # Rebuild on file changes
```

**Equations in HTML:**

LaTeX equations (`$...$` and `$$...$$`) render via KaTeX (lightweight, no server needed).
Include KaTeX CSS + JS from CDN. Chemical formulas use `mhchem` KaTeX extension.

### Implementation

New module: `src/wikify/wiki/html.py`
- `build_site(wiki_dir, output_dir)` — main entry point
- `render_article(md_path, template, context) -> str` — single article
- `build_sidebar(domains, special_pages) -> str`
- `build_infobox(frontmatter, concept_type) -> str`
- `resolve_wikilinks(html, slug_map) -> str` — replace `[[...]]` with `<a>`
- `generate_category_pages(articles_by_category, template)`
- `generate_special_pages(epoch_log, recent_articles, template)`

Dependencies: `jinja2` (already common), `markdown` (Python-Markdown), `pymdown-extensions`
(for math). KaTeX loaded from CDN, no Python dependency.

### Skill updates

Update `/wiki-epoch` skill: after Pass 5, optionally regenerate HTML site.
Add regeneration to `/wiki-maintain` as well.

---

## 3. Equation Extraction

### Problem

Equations pass through as raw text. `Chunk.has_equations` is a boolean flag but the actual
equations are not extracted, classified, or used to enrich concepts. A concept like "Fick's
second law" should have its governing equation displayed in its article. Chemical equations
(ALD half-reactions) should enrich material and technique articles.

### Design

**Extraction pipeline:**

Add `src/wikify/extract/equations.py`:

```python
@dataclass
class ExtractedEquation:
    id: str                # hash of normalized LaTeX
    paper_id: str
    chunk_id: str
    latex: str             # raw LaTeX string
    equation_type: str     # "mathematical" | "chemical" | "inline"
    context: str           # surrounding text (1 sentence before + after)
    label: str | None      # equation label if present (e.g., "Eq. 1")
    variables: list[str]   # extracted variable names
    section_path: str
```

**Detection patterns:**

```python
# Display math
r"\$\$(.+?)\$\$"           # $$ ... $$
r"\\\[(.+?)\\\]"           # \[ ... \]
r"\\begin\{equation\}(.+?)\\end\{equation\}"

# Inline math
r"\$([^\$]+?)\$"           # $ ... $ (single dollar)

# Chemical equations (plain text patterns)
r"(\w+)\s*\+\s*(\w+)\s*(?:->|-->|→)\s*(\w+)"  # A + B -> C
r"(?:Al|Hf|Ti|Zr|Si|W|Mo|Ta)\(?(?:CH3|C2H5|OC2H5|NMe2)\)?\d*\s*\+\s*(?:H2O|O3|O2|NH3)"
```

**Enrichment:**

During Pass 1, the extraction template gains an `equations` section:

```json
{
  "equations": [
    {
      "latex": "J = -D \\frac{\\partial C}{\\partial x}",
      "type": "mathematical",
      "describes": "Fick's first law",
      "variables": {"J": "diffusion flux", "D": "diffusion coefficient", "C": "concentration"},
      "related_concepts": ["diffusion", "Fick's law"]
    }
  ]
}
```

Equations are linked to concepts: if an equation "describes" a concept, it appears in that
concept's article under a **## Key Equations** section.

**Chemical half-reactions for ALD:**

Special handling for ALD precursor reactions:
```
TMA + surface-OH → surface-O-Al(CH3)2 + CH4   (half-reaction A)
H2O + surface-Al(CH3)2 → surface-Al(OH)2 + CH4 (half-reaction B)
```

These enrich technique articles with a **## Reaction Mechanism** section showing balanced
half-reactions.

### Data model

New SQLite model in `store/models.py`:

```python
class Equation(SQLModel, table=True):
    id: str = Field(primary_key=True)     # hash of normalized LaTeX
    paper_id: str = Field(foreign_key="paper.id")
    chunk_id: str = Field(foreign_key="chunk.id")
    latex: str                             # raw LaTeX
    equation_type: str = "mathematical"    # mathematical | chemical | inline
    context: str = ""                      # surrounding text
    label: str | None = None               # "Eq. 1" etc.
    variables: str = "[]"                  # JSON list
    section_path: str = ""
    concept_links: str = "[]"              # JSON list of ConceptRecord.id values
```

### Article template updates

Add to all relevant templates:

```
If equations are available for this concept:
7. **## Key Equations**: Display each equation in LaTeX block format.
   For each: the equation, a plain-English description, and variable definitions.
```

---

## 4. Image & Table Parsing

### Problem

Current state:
- `extract/figures.py` extracts binary images via fitz but with poor caption matching
  (only looks for "Fig" at line start on the same page)
- `extract/figure_refs.py` extracts caption text via regex but has no binary images
- Tables are not extracted at all — they pass through as markdown text in chunks
- No way to associate extracted images with their captions reliably
- No way for the LLM to view figure content

### Design

**Unified extraction via pymupdf4llm:**

Replace the current dual-path (figures.py + figure_refs.py) with a single pipeline that
uses pymupdf4llm's `write_images=True` mode combined with fitz's structured extraction.

New module: `src/wikify/extract/media.py` (replaces figures.py):

```python
@dataclass
class ExtractedMedia:
    """A figure or table extracted from a source document."""
    id: str                    # content hash
    paper_id: str
    media_type: str            # "figure" | "table" | "scheme" | "chart"
    caption: str               # full caption text
    label: str                 # "Fig. 1", "Table 2", "Scheme 1"
    page_number: int
    section_path: str
    image_path: str | None     # path to extracted image file (figures)
    markdown_table: str | None # markdown table text (tables)
    width_px: int
    height_px: int
    bbox: tuple[float, float, float, float] | None  # page coordinates
```

**Extraction strategy:**

1. Run `pymupdf4llm.to_markdown(path, write_images=True, image_path=tmp_dir, dpi=150)`
2. Parse the markdown output to find image references and their surrounding captions
3. Match extracted image files to captions using positional proximity
4. For tables: use `page.find_tables()` to get structured table data; store both as
   markdown and as the raw grid for LLM consumption
5. Cross-reference with `figure_refs.py` regex output for caption validation

**Storage layout:**

All media under `data/figures/` (existing directory), content-addressed:

```
data/figures/
  {hash[:2]}/
    {hash[2:4]}/
      {hash}.png          # image file
      {hash}.meta.json    # caption, label, paper_id, page, section, bbox
```

No new directories. The `.meta.json` sidecar replaces the need for a separate lookup.
The `Figure` SQLite model is the authoritative index; the sidecar is for quick inspection.

**Table extraction:**

Tables get special treatment:
1. `page.find_tables()` extracts structured grid data
2. Store as markdown in `Figure.extracted_data` (JSON with headers + rows)
3. For tables that are images (scanned PDFs), fall back to image extraction + Haiku vision

**Updated Figure model:**

Extend the existing `Figure` model:

```python
class Figure(SQLModel, table=True):
    id: str = Field(primary_key=True)
    paper_id: str
    media_type: str = "figure"        # NEW: figure | table | scheme | chart
    caption: str | None = None
    figure_number: str | None = None
    label: str | None = None          # NEW: "Fig. 1", "Table 2"
    section_path: str | None = None
    image_path: str = ""
    width_px: int = 0
    height_px: int = 0
    format: str = "png"
    page_number: int | None = None    # NEW: populated now
    bbox: str | None = None           # NEW: JSON [x0, y0, x1, y1]
    tags: str = "[]"
    extracted_data: str | None = None # Tables: JSON {headers, rows}
    markdown_table: str | None = None # NEW: markdown representation
    reuse_count: int = 0
    llm_description: str | None = None # NEW: Haiku-generated description
```

### Validation experiment

Before full integration, validate on 4-5 papers from the existing corpus:

1. Select 4-5 papers with known figure/table counts (manually counted)
2. Run the new extraction pipeline
3. Compare: expected vs actual figure count, table count, caption matching accuracy
4. Iterate until >90% accuracy on caption-to-image matching

Papers to test (from the ALD/memristor corpus — pick diverse types):
- A paper with many figures and few tables
- A paper with complex multi-panel figures (Fig. 1a-d)
- A paper with data-heavy tables
- A paper with schemes/reaction diagrams
- A scanned/older paper with poor OCR

### Retrieval

New MCP tools:

```python
def get_figure(figure_id: str) -> dict:
    """Get figure metadata + image path for LLM viewing."""

def get_paper_figures(paper_id: str) -> list[dict]:
    """List all figures/tables for a paper."""

def search_figures(query: str, media_type: str = None) -> list[dict]:
    """Search figure captions semantically."""
```

---

## 5. Haiku Vision for Extraction

### Problem

Haiku supports image inputs. Currently, figures are extracted as binary files but never
shown to the LLM. The extraction pipeline works text-only, missing all information
encoded in figures (trends, data points, device structures, process flows).

### Design

**Pass 1 extension — figure extraction:**

After text-based concept extraction, if a source has extracted figures:

1. For each figure with `media_type in ("figure", "scheme", "chart")`:
2. Load image bytes, base64-encode
3. Send to Haiku with prompt:

```
You are extracting knowledge from a scientific figure.

Figure caption: {caption}
Paper context: {paper_title}, {section_path}

Extract:
1. What does this figure show? (1-2 sentences)
2. Key data points or trends visible
3. Any concepts, materials, or techniques depicted
4. Any numerical values readable from axes or labels

Return JSON: {description, data_points, concepts, values}
```

4. Store the result in `Figure.llm_description` (for text search)
5. Feed extracted concepts into the standard concept merge pipeline

**Cost management:**

- Image tokens cost ~1,000-1,600 per figure at Haiku rates ($0.80/MTok)
- A paper with 10 figures costs ~$0.008-0.013 in image tokens
- For a 200-paper corpus: ~$1.60-2.60 total (acceptable)
- Gate: skip figures smaller than 200x200px or with captions already containing >50 words
  (the caption itself is likely sufficient)

**Writing-time image access:**

During Pass 3 article writing, the writing agent (sonnet or haiku) can request to view
a figure if it needs visual context:

```python
def view_figure(figure_id: str) -> dict:
    """Returns base64 image + caption + llm_description for the writing agent."""
```

The skill instructions tell the writing model:
> "If a source figure is critical to understanding a concept and the caption alone is
> insufficient, use `view_figure` to inspect it. Use sparingly — most information is
> in the text."

**Table understanding:**

For tables stored as images (scanned PDFs), Haiku vision extracts structured data:

```
Extract the table content as a markdown table. Include all headers and rows.
If any cells contain chemical formulas, preserve them.
If any cells contain numerical values with units, preserve both.
```

Result stored in `Figure.extracted_data` as JSON and `Figure.markdown_table` as markdown.

**Skill updates:**

Update `.claude/skills/wiki-epoch.md`:
- Pass 1 section: "After text extraction, if the source has figures, send each figure
  to a fast-tier agent for visual extraction. Merge visual concepts with text concepts."
- Pass 3 section: "When writing about a concept, check if any associated figures would
  help. Use `view_figure` sparingly for critical diagrams."

---

## Implementation Order

### Phase 1: Image & Table Parsing (Implemented)

`src/wikify/extract/media.py` -- unified extraction pipeline. `Figure` model updated with
`media_type`, `label`, `page_number`, `bbox`, `markdown_table`, `llm_description`.

### Phase 2: Equation Extraction (Implemented)

`src/wikify/extract/equations.py` -- regex + LaTeX detection. `Equation` model in
`store/models.py`. Integrated into ingest pipeline and article templates.

### Phase 3: People Identification (Implemented)

`src/wikify/wiki/people.py` -- person discovery, name dedup, author cross-reference.
`person` template in `article_templates.py`. People articles under `people/` subdirectory.

### Phase 4: Haiku Vision (Implemented)

`src/wikify/llm/vision.py` -- sends figures to Haiku for structured description.
`src/wikify/wiki/figure_enrichment.py` -- batch enrichment at scale.
MCP tools: `get_figure_details`, `get_paper_figures`.

### Phase 5: Wikipedia HTML Layout (Implemented)

`src/wikify/wiki/html.py` -- static site generator with Jinja2 templates, Wikipedia
Vector skin, KaTeX equations, client-side search. CLI: `wikify wiki html [--serve]`.

### Dependencies

```
Phase 1 (Images/Tables) ──→ Phase 3 (People)
         │                         │
         └──→ Phase 4 (Vision) ────┘
                                   │
Phase 2 (Equations) ───────────────┤
                                   │
Phase 5 (HTML Layout) ◄────────────┘
```

Phases 1 and 2 can run in parallel.
Phase 5 can start immediately (basic version) and gain features as other phases complete.

---

## New Dependencies

| Package | Purpose | Phase |
|---------|---------|-------|
| `jinja2` | HTML template rendering | 5 |
| `markdown` | Markdown to HTML conversion | 5 |
| `pymdown-extensions` | Math/chem extensions for markdown | 5 |

KaTeX and lunr.js are loaded from CDN — no Python packages needed.
All other functionality uses existing dependencies (pymupdf4llm, fitz, litellm).

---

## Test Plan

| Phase | Test | Method |
|-------|------|--------|
| 1 | Image extraction accuracy | 4-5 papers, manual count comparison |
| 1 | Table extraction accuracy | Compare markdown tables to PDF tables |
| 1 | Caption-to-image matching | Manual verification on test papers |
| 2 | Equation detection recall | Papers with known equations, check all found |
| 3 | Person discovery precision | Check against known author lists |
| 3 | Name deduplication | Feed variant names, verify merge |
| 4 | Haiku figure understanding | Compare LLM description to manual description |
| 4 | Haiku table extraction | Compare extracted markdown to actual table |
| 5 | HTML rendering correctness | Visual inspection, link verification |
| 5 | Wikilink resolution | All `[[links]]` resolve to valid pages |
| 5 | Infobox generation | Check all concept types render correctly |

---

## Skill Updates Summary

| Skill | Changes |
|-------|---------|
| `/wiki-epoch` | Pass 1: add people + equations + figure vision. Pass 3: add `view_figure` tool, equation sections, person articles. Pass 5: regenerate HTML. |
| `/wiki-campaign` | Add figure/equation/person awareness to directed extraction. |
| `/wiki-ask` | Allow answering questions about people, equations, figures. |
| `/wiki-maintain` | Lint for orphan figures, unlinked people, missing equations. HTML rebuild. |
