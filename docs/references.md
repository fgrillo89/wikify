# References and citations

Wikify's source documents are academic papers, and academic papers cite
other papers. This document explains how Wikify turns the messy
reference lists at the back of each paper into clean, structured,
linkable citations — and how those citations are rendered on the finished
wiki.

It expands the "references and author resolution" part of the ingestion
branch. Read `docs/overview.md` first for the core vocabulary (corpus,
chunk, document, page, evidence).

A few terms used throughout:

- **Reference.** One entry in a paper's bibliography — the raw line of
  text that names another work, for example *"L. Chua, Memristor — the
  missing circuit element, IEEE Trans. Circuit Theory 18 (1971)
  507–519."*
- **DOI.** A Digital Object Identifier: a short permanent code like
  `10.1109/TCT.1971.1083337` that uniquely names a published work. A DOI
  resolves to a landing page at `https://doi.org/<doi>`.
- **BibTeX.** A plain-text format for bibliographic records (`@article{
  ... }`), the standard interchange format for reference managers.
- **CS1.** Citation Style 1, the default Wikipedia style for rendering a
  journal citation as a single readable line.

The pipeline has four stages, each handled by a distinct part of the
code:

```
raw paper text
   |
   v   extraction        ingest/citations.py
[ reference strings + DOI + year ]
   |
   v   parsing           citations/parse.py
[ structured fields: title, authors, venue, volume, pages ]
   |
   v   resolution        util/doi_resolver.py, citations/resolver.py
[ verified metadata from CrossRef / OpenAlex, citation graph edges ]
   |
   v   rendering         render/html/citation.py
[ CS1 citations on pages, a References page, source links ]
```

## Stage 1 — Extraction: finding the references in the text

A parsed paper is one long markdown string. The first job is to locate
its bibliography and cut it into individual reference strings. This is
`extract_citations` in `src/wikify/ingest/citations.py`.

It finds the references section by matching a heading like *References*,
*Bibliography*, or *Works Cited* (with optional section numbers and
"and Notes" suffixes), then takes everything from that heading to the
next heading of the same level. If no such heading exists, it falls back
to scanning the last ~40% of the document for clusters of
citation-shaped lines (author initials followed by a year), and only
accepts the fallback when at least three such lines appear together.

The section is then split into entries — either on numbered markers
(`[1]`, `(1)`, `1.`) or, if none are present, on blank lines.

Extraction is deliberately conservative about *what* it pulls from each
entry. It only extracts the fields that can be read reliably from raw
text without guessing:

- **DOI** — matched with a balanced-parenthesis-aware regex, so a DOI
  like `10.1016/S0893-6080(97)00011-7` keeps its parentheses while
  trailing punctuation is stripped. `repair_doi` later re-extracts DOIs
  that earlier ingests stored truncated.
- **year** — the first 4-digit year in the plausible range.
- **author last names** — capitalized words appearing before the year
  that are not journal keywords (capped at ten). These are used only for
  matching references *within* the corpus, never for the exported
  bibliography.

Title, authors, and venue are intentionally **not** extracted here. The
raw text is too noisy to regex apart reliably; those fields come from the
next two stages. Each reference becomes a `CitationEntry` (defined in
`src/wikify/citations/models.py`) carrying the raw text plus whatever was
extracted.

## Stage 2 — Parsing: structuring a citation string

When a reference has no DOI, or its DOI does not resolve, Wikify falls
back to a heuristic parser: `parse_citation` in
`src/wikify/citations/parse.py`. This module is self-contained — it uses
only the Python standard library and can be applied to any citation
string independently of the rest of Wikify.

Academic citations come in many house styles, and the same work looks
very different in IEEE, Nature, ACS, APA, Vancouver, Chicago, MLA,
Harvard, Elsevier, and RSC / AIP / APS formatting. Rather than try one
universal pattern, the parser first classifies the string into one of a
few **extraction strategies** (`detect_format`), grouped by where the
title sits:

- **quoted** — the title is in quotation marks (IEEE, Chicago, MLA).
- **apa** — a year in parentheses follows the authors (APA, Harvard).
- **acs** — authors are separated by semicolons, then the title.
- **elsevier** — everything is comma-separated with a trailing DOI URL.
- **perioded** — the title is a sentence between the author block and the
  venue (Nature, Vancouver, and the default).

Each strategy has its own title extractor; from the title's position the
parser then reads backwards for authors and forwards for venue, volume,
and pages. A layer of validation (`_is_valid_title`, `_is_valid_venue`,
`is_valid_author`) rejects common garbage — author-list fragments
masquerading as titles, journal abbreviations mistaken for venues, page
coordinates parsed as volumes, and so on.

Because the same work is often cited by several papers in the corpus,
`fuse_cross_paper_evidence` merges the parses for what it judges to be the
same work. Works are grouped by a fingerprint (the DOI, or the first
three author surnames plus the year), and within each group the longest
title, the longest author list, and the majority year fill in fields that
any single noisy citation left blank.

The adapter that drives all of this over a corpus is `enrich_citations`
in `src/wikify/ingest/cite_parse.py`, which runs four passes in order:
re-extract DOIs from raw text, heuristic-parse the unresolved entries,
resolve DOIs against the live APIs (Stage 3), and finally fuse across
papers.

## Stage 3 — Resolution: verifying against authoritative sources

Heuristics are a fallback. When a reference carries a DOI, Wikify
prefers to fetch authoritative metadata from a scholarly API. There are
two resolvers, each tuned for a different job.

### DOI resolution (CrossRef + doi.org)

`resolve_many` in `src/wikify/util/doi_resolver.py` is the single entry
point for looking up a DOI. It runs three steps:

1. **Cache.** Results live in a SQLite database at
   `<corpus>/.citestore.db`. Negative results (DOIs that resolved to
   nothing) are cached too, so a failed lookup is not retried on every
   refresh.
2. **CrossRef batch.** Cache misses are sent to the CrossRef API in
   batches of 75 DOIs per request, which returns structured JSON
   (title, authors, journal, volume, pages, year, publisher). CrossRef
   covers roughly 85% of scholarly DOIs.
3. **doi.org content negotiation.** DOIs that CrossRef missed or returned
   incompletely are fetched one at a time from `https://doi.org/<doi>`
   with an `Accept: application/x-bibtex` header. This covers the
   registration agencies CrossRef does not serve (DataCite, mEDRA,
   JaLC). A record counts as complete only when it has both a title and
   at least one author.

The `skip_content_neg` flag trades completeness for speed: with it set,
step 3 is skipped, so CrossRef-registered DOIs still resolve but the
slower long tail is left for later. This is the default during ingest,
where speed matters; full resolution is available when completeness
matters more.

Every outbound request is rate-limited (a polite requests-per-second
floor) and concurrency-capped, and 429/503 responses are retried with
exponential backoff and jitter.

### OpenAlex resolution and the citation graph

`AsyncResolver` in `src/wikify/citations/resolver.py` does a heavier job:
it resolves the corpus's own papers and also discovers the citation
*links* between them, using OpenAlex (which, unlike CrossRef, exposes
each work's list of referenced works). Its strategy is batch-first to
keep the number of API calls small:

1. resolve the corpus papers by DOI in bulk (50 DOIs per call);
2. collect each resolved paper's `referenced_works` for free from those
   responses;
3. bulk-fetch the metadata for those referenced works (100 IDs per call);
4. match any still-unresolved reference *text* locally against everything
   resolved so far.

Step 4 is a local fuzzy match, not an API call. It builds an inverted
index of the significant tokens in every known title, drops tokens that
appear in too many titles (they do not discriminate), picks a small
candidate set for each unresolved reference, and scores with
`rapidfuzz`'s `partial_ratio` so a clean title can be found inside a noisy
raw citation. A match is accepted only above a confidence threshold (85).

Each resolution is tagged with a level on the `ResolutionResult`: level
**A** for an exact DOI hit and level **C** for an accepted fuzzy
text match, with **miss** for references that resolved to nothing. As a
side effect, the resolver stores the parent-to-child citation edges,
which become the corpus citation graph that the agent walks during
exploration.

## Stage 4 — Bibliography artifacts and BibTeX

With references extracted, parsed, and resolved, `src/wikify/ingest/
bibtex.py` writes two end-user files into the corpus and an in-memory
index that the rest of the pipeline consumes:

- **`corpus_papers.bib`** — one `@article` entry per document in the
  corpus.
- **`cited_works.bib`** — entries for the references, but only those that
  resolved cleanly to a real title and author list. Unresolved
  references are deliberately left out of the exported `.bib` so it stays
  trustworthy.

A great deal of this module is defensive cleaning. PDF parsing leaks
affiliation symbols, running headers, JATS markup (`HfO<sub>2</sub>`),
and citation fragments into the title and author slots, so
`_clean_bib_title`, `_clean_author_name`, and a long series of structural
rejects in `_reference_entry_from_citation` discard records that are
still citation soup after cleaning rather than emit a bad entry. BibTeX
keys are readable, of the form `Smith2024Atomic` (first author surname +
year + first significant title word), made unique with an `a`/`b`/`c`
suffix on collision.

`build_citation_index` ties everything together into
`citation_index.json`. It deduplicates documents that share a DOI (the
same paper ingested as both a PDF and a DOCX) or, for pre-DOI papers, a
filename-convention key, and it records, for every citation, which
documents cite it (`source_doc_ids`) and under which bibkey. Resolved
metadata for corpus papers (DOI, title, year, authors, container title)
is also projected into the corpus SQLite store's `documents` table, which
is what the renderer reads.

## Rendering: CS1 citations and source links

The finished wiki shows citations in Wikipedia's CS1 style.
`format_cs1` in `src/wikify/render/html/citation.py` builds the citation
line from a metadata dict:

```
Last, F. M.; Last2, F. M. (Year). "Title". *Journal*. **Vol** (Issue): pages. doi:10.xxxx/yyyy.
```

Any missing field is dropped silently, so a record with only authors, a
year, and a title still renders a clean line. The bulk of the module is
author-name formatting: turning `J. Joshua Yang` into `Yang, J. J.`,
handling comma-pre-flipped names (`Chua, Leon`), surname particles
(`Johannes van der Waals` → `van der Waals, J.`), generational suffixes
(`John Smith Jr.` → `Smith, J., Jr.`), and hyphenated initials
(`Sung-Hyun` → `S.-H.`). Author lists longer than four names are capped
with `et al.`, and `--` page ranges become en-dashes.

Citations need to link somewhere. `_load_doc_meta_map` in
`src/wikify/render/html/render.py` decides each document's click target
with a simple preference:

1. if the document has a **DOI**, link to `https://doi.org/<doi>` — always
   portable;
2. otherwise, if the original **source file** is on disk, copy it into
   `assets/sources/` inside the rendered site and link to that stable
   relative path.

`format_cs1` wraps the title text in that link, and adds the DOI as its
own linked identifier (unless the DOI is already the title link).

These citations appear in two places on the site. Each page's evidence
footnotes are reformatted from their raw `chunk (doc_id) > "quote"` shape
into CS1 references (`_clean_evidence_lines` and, for data-artifact
tables, `_format_data_footnote`); the internal document hash never
reaches the reader. And a single **References page** aggregates every
document cited anywhere in the wiki into one CS1-formatted list
(`_aggregate_references`), each entry sorted by first-author surname and
year and carrying back-links to the pages that cite it. Documents cited
by no page are omitted; documents whose metadata could not be found
render as a bare identifier so nothing silently disappears.
