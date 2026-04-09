# wikify_simple -- session log (2026-04-08 to 2026-04-09)

## Starting state

- Slices 0-5 landed, smoke test passing, wiki index + partial ingest port done.
- No real corpus had ever been processed through `wikify_simple`.
- 30 tests passing. Fake binding only. Hash-based embedder (no semantics).
- PDF parser stub raising `NotImplementedError`. Markdown-only ingest.

## What was built (chronological, with commit hashes)

### Phase 1 -- Slice 0b parser port + query mode + shared embedder + index tests + g_links

- `2705ace` **slice 0b parsers, query mode, shared embedder, index tests, g_links** --
  Ported the PDF parser from legacy `wikify.ingest` (pymupdf4llm + fitz TOC merge +
  image extraction). Added `wikify-simple query` CLI verb. Replaced the hash-based
  embedder with a switchable backend (`sentence_transformers` or `hash`) via
  `infra/embedding.py`. Added `WikiIndex` tests. Implemented `g_links` modularity
  metric in `eval/metrics.py`. Unblocked real corpus ingest and semantic metrics.

### Phase 2 -- Legacy image/table extraction restored

- `7c3fbfd` **restore legacy image/table extraction, persist images with sidecars** --
  Ported image extraction from `wikify.ingest.extract.media`. Images persisted as
  `corpus/images/{doc_id}/fig_NNN.{ext}` with JSON sidecars carrying caption, bbox,
  label, page, content_hash. Unblocked figure references in wiki pages.

### Phase 3 -- Image folder/file naming + image index

- `630de66` **clean image folder/file names + per-corpus image index** --
  Word-bounded slug for image folder names (avoids MAX_PATH on Windows). Built
  `ImageIndex` in `store/images_index.py` with `for_doc(doc_id)` lookup. Unblocked
  writer access to figures by doc_id.

### Phase 4 -- Structural ingest fixes (sections, years, authors, topics, figures, naming)

- `df7049b` **structural ingest fixes** --
  Fixed empty section lists (heading parser was dropping levels). Fixed year
  extraction from PDF metadata. Fixed author extraction from markdown frontmatter.
  Fixed topic deduplication. Fixed figure label parsing. Fixed doc_id naming
  (slug from title, not filename). Unblocked clean corpus state for distill.

### Phase 5 -- Item 7 (prompts YAML, Pydantic schemas, --feed)

- `0a8d6b9` **prompts to YAML, Pydantic schemas, --feed incremental mode** --
  Moved prompt templates from string constants in `pipeline.py` to
  `prompts/{extract,write,query}.yaml`. Upgraded `agents/schema.py` from
  dataclasses to Pydantic v2 with `extra="forbid"`. Added `--feed` flag for
  incremental distill (loads existing bundle, merges evidence by title/alias).
  Unblocked prompt experimentation without code changes.

### Phase 6 -- Slice 6 first runs (fake + real binding, multiple iterations)

- `1b52852` **slice 6 first real run on mvp20 (20 PDFs)** --
  First end-to-end run against `data/papers/mvp20/`. 689 chunks, 312 images,
  156 concept + 52 person pages (fake binding). Hit Windows MAX_PATH in extract
  cache (fixed by hashing chunk_id to 24-char sha256 prefix). Discovered
  embedder-dimension mismatch at eval time.
- `c9f2a0c` **persist embedder backend, fail loud on dim mismatch** --
  Added `vectors.meta.json` next to `vectors.npz`. `coverage_residual` now
  requires explicit embedder argument matched to corpus metadata. Unblocked
  consistent M1 measurement.
- `9791a6e` **first-class eval CLI verb** --
  Added `wikify-simple eval --bundle --corpus` command. Writes `_metrics.md` +
  `_metrics.json`. Unblocked reproducible metric capture.
- `2ca6423` **distill consumes ImageIndex** --
  Wired `ImageIndex` into the extract and write loops. `WriteRequest.figures`
  populated from corpus images. Unblocked figure references in written pages.
- `c4e1244` **slice 6 re-run on mvp20 after structural fixes** --
  Re-ran with sentence_transformers embedder + ImageIndex wiring. M1=0.5591,
  g_links Q=NaN (n>150 cap), g_evidence=0 (writer starvation). Query mode
  worked. 30/30 tests pass.

### Phase 7 -- Louvain modularity, dispatcher smoke test, runbook

- `1915192` **Louvain communities (drops n>150 modularity cap)** --
  Replaced the greedy O(n^4) community detector with `networkx.community.louvain_communities`.
  Removed the n>150 skip. g_links modularity now computable on any graph size.
- `22e2da3` **dispatcher smoke test + real-binding runbook** --
  Added `RUNBOOK_real_binding.md` with step-by-step operator instructions.
  Added dispatcher integration test. Unblocked the first real-binding run.
- `0dd7e00` **slice 6 first real-binding smoke run findings** --
  0.1x budget (5000 heq) with `--binding claude_code`. 18 concept + 2 person
  pages. First non-zero g_links (Q=0.56, 60 edges). Writer cost 100x extractor
  (46500 heq single write). Cache poisoning risk surfaced (fake/real sharing
  cache namespace).

### Phase 8 -- Token-based cost accounting, cache binding namespace, body validator

- `ea7ac05` **token-based cost accounting (writer figures fix)** --
  Cost meter now uses `input_tokens * rate + output_tokens * rate + overhead`
  per tier (S/M/L). Writer re-tiered from L to M. Per-write cost dropped
  from ~46k to ~10-14k heq.
- `aa7205a` **ExtractCache key includes binding name** --
  Cache path now `data/cache/extract/<binding>/<model>/<prompt_hash>/<chunk_hash>.json`.
  Fake and real binding results can never cross-contaminate.
- `98cc67f` **WriteResponse body validator (min prose + markers + evidence)** --
  Writer responses must have >=2 non-blank prose lines, >=1 `[^eN]` marker in
  prose, >=1 footnote definition in evidence block. Catches empty-body writes.
- `e46970b` **runbook updates after structural fixes** --
  Updated RUNBOOK with new cost model, cache namespace, body validator.

### Phase 9 -- Tolerant quote matching, pymupdf artifact strip, writer tier M

- `f13f91f` **rethink ExtractedConcept + debuggable dispatcher rejections** --
  Added `kind` (concept/person) and `category` (phenomenon/method/material/etc)
  fields to `ExtractedConcept`. Error artifacts written as `<rid>.error.json`
  for postmortem.
- `3070574` **pipeline catches per-call rejections + 3x real-binding findings** --
  `try/except (ValidationError, QuoteNotInChunkError)` around each dispatch call.
  Run continues on rejection; `.error.json` artifacts left for inspection.
  3x real-binding: 215 concepts + 21 people, g_links Q=0.7377, 2601 edges.
- `2e4e579` **tolerant quote substring match (NFKC + dashes + brackets)** --
  `text_normalize` applies NFKC normalization, dash unification, bracket-wrap
  stripping, emphasis stripping. Quote validation uses normalized forms.
  Eliminates the entire class of drain-agent rejections.
- `9131791` **strip pymupdf citation markers + bracket-wrap at parse** --
  Parse-time regex removes `[NN]` citation markers and `[token][bracket][wrap]`
  noise from PDF chunks. Chunks are clean before embedding or extraction.
- `a3ef62b` **drop writer to tier M (5x more writes per budget)** --
  Writer cost dropped from ~50k to ~10-14k heq per call. At 50k budget: 2
  writes became ~3-4 writes.
- `2ff84b8` **markdown-emphasis quote tolerance + mixed schedule rebalance** --
  Added `*` and `_` stripping to `text_normalize`. Bumped exploit_fraction
  from 0.4 to 0.65 (65% budget to writes).

### Phase 10 -- Evidence parser fix, adaptive schedule, confidence tags, audit.md

- `42261c1` **tolerant Evidence-block parser (M3 g_evidence + M6 g2 unblock)** --
  Bundle evidence parser now handles multiple formatting variants. Unblocked
  M3 g_evidence and M6 g2_evidence_ok metrics.
- `ba21e25` **renderer port assessment + figure-placement plan** --
  Design note for HTML renderer. Assessed legacy `wikify` renderer.
- `4777eaa` **writer must reference each embedded figure in adjacent prose** --
  `_check_figure_mentions` validator ensures every `![Figure N](...)` in body
  has a text mention within 3 lines. Prevents orphaned figure embeds.
- `8aa624f` **adaptive schedule reallocation after extract loop** --
  `AdaptiveSchedule.reallocate` wired into `pipeline.run`. After extract loop
  finishes, remaining extract budget reallocated to writes.
- `ac62c16` **confidence-tagged concepts + per-bundle _audit.md** --
  `ExtractedConcept` gains `confidence: Literal["extracted","inferred","ambiguous"]`
  and `score: float`. Bundle emits `_audit.md` listing top communities, hub
  pages, low-confidence claims, and metric gaps.
- `0050542` **iteration architecture design (refine / merge / image consolidation)** --
  `ITERATION_DESIGN.md`: three-operation contract (create/refine/merge),
  re-draft triggers, image consolidation mechanism, per-page provenance history.
  Design only, no implementation.

### Phase 11 -- Full Wikipedia writer, legacy HTML renderer port

- `15c6d9f` **full Wikipedia-style writer (6 sections, strict validator)** --
  Writer prompt produces encyclopedic articles with Background, Mechanism/Method,
  Applications, Characterization, Related Concepts, and References sections.
  Strict section validator enforces structure.
- `496ac5b` **full HTML renderer port from legacy wikify** --
  Ported the Jinja2 + CSS renderer from legacy `wikify.wiki.presentation`.
  `wikify-simple html --bundle <dir>` produces a static site with navigation,
  search, and per-page rendering.

### Phase 12 -- Parse-time cleanup, citations + bibtex + author pages, obsidian links

- `f2b7d5e` **parse-time markdown cleanup (port legacy noise stripping)** --
  Ported `clean_markdown` from legacy ingest. Strips stray formatting, double
  spaces, empty headers, orphaned list markers.
- `b188f58` **citations + library.bib + deterministic author pages** --
  BibTeX generation from corpus metadata. Deterministic author pages (no model
  call): lead sentence + publication list + collaborators + obsidian wikilinks.
  1268 author pages from mvp20 corpus.
- `8efb4b3` **encyclopedic writer (Background/Applications, no wikilinks, no bullets)** --
  Writer prompt revised: no visible `[[wikilinks]]` in prose, no bullet lists
  in body, full paragraph prose. Sections are Background, Applications, and
  Characterization.

### Phase 13 -- Layered prompts (style guide + field guide + artifact template + persona)

- `23781b9` **layered writer prompt (style + field + artifact + persona)** --
  Four-layer prompt stack: `style_guide.md` (global rules) + field-specific
  guide (`prompts/fields/{field}.yaml`) + artifact template
  (`prompts/artifact_types/wiki_article.yaml`) + corpus persona. Field
  auto-detected from corpus topics. Unblocked domain-appropriate writing
  without code changes.

### Phase 14 -- Natural page names, flexible sections, field auto-detect, per-doc obsidian links, float clamp

- `b90fe1f` **clamp coverage_residual to [0, 1] (float underflow guard)** --
  Cosine similarity can produce values slightly > 1.0 due to float precision.
  Clamped to prevent negative residuals.
- `3878f0f` **natural Wikipedia-style page filenames** --
  Page files named `Atomic Layer Deposition.md` instead of
  `concept-atomic-layer-deposition.md`. The `kind` field in frontmatter
  distinguishes page types; the filename IS the title.
- `f5ae66d` **writer sections are guidance, not strict requirements** --
  Section validator relaxed: writer may omit sections if the evidence doesn't
  support them. No more stub sections with "No information available."
- `2a3b1eb` **auto-detect field from corpus topics** --
  `field_detect.py` scores corpus topics against field keyword lists. Threshold
  relaxed to 3 matching topics. Auto-selects the right field guide for the
  layered prompt.
- `7d5acf6` **per-doc obsidian markdown with similar/cites/coupling** --
  Each source document gets an Obsidian-compatible markdown file in
  `corpus/obsidian/` with `[[wikilinks]]` to related docs, similar docs
  (by embedding cosine), citation links, and coupling edges.

### Phase 15 -- Model-enriched person pages, non-author mentions

- `af7dae3` **Wikipedia-style author pages (lead + collaborators + obsidian wikilinks)** --
  Author pages restructured as Wikipedia-style biographical stubs: lead
  sentence with affiliation context, "Publications in corpus" section,
  "Collaborators" section with `[[wikilinks]]`, "Related concepts" section.
- `42ad6d0` **model-enriched person pages + non-author mentions** --
  Person pages enriched with model-extracted context when evidence accumulates.
  Non-author people mentioned in text (e.g. "Leon Chua proposed...") also get
  pages. People router distinguishes corpus authors from mentioned individuals.

### Phase 16 -- Duplicate title/evidence fix

- `192b428` **fix duplicate title heading + duplicate Evidence block** --
  Fixed two rendering bugs: duplicate `# Title` at top of page (frontmatter
  title + body title), and duplicate `## Evidence` blocks when writer and
  pipeline both emit evidence sections.

## Current metrics (mvp20_v6 3x run)

From the final 3x real-binding run on mvp20 (commit 3070574 findings):

| metric | value | notes |
|---|---|---|
| budget | 150k heq | 3x budget, 23% overrun |
| pages (concept + person) | 215 + 21 | substantive concepts, real researchers |
| extractor calls | 74 new + 30 cached | |
| writer calls | 2 | writer starvation (fixed in later commits) |
| M1 coverage_residual | 0.4603 | lower is better |
| M3 g_links Q | 0.7377 | healthy link structure |
| M3 g_links n_edges | 2601 | |
| M3 g_evidence Q | 0.0 | only 2 written pages |
| M5 hit_rate | 0.0 | only 2 pages with evidence |
| M6 g1_anchoring | 0.125 | 14 of 112 sentences have markers |
| figure refs in writes | 2/2 (100%) | |
| extract rejections | 5 | quote-substring class |

From the 1x mvp20_v4 run (post-fix, commit 2ff84b8):

| metric | value |
|---|---|
| budget | 50k heq |
| pages | 83c + 8p |
| M3 g_links Q | 0.7596 |
| writes | 2 |
| write cost (each) | ~10-14k heq |
| extract rejections | 2 |

## What's still open

### Priority 1 -- Blocking quality

1. **M6 g1_anchoring = 0.125 (only 14 of 112 sentences have markers).**
   The writer is not citing enough. The prompt asks for `[^eN]` markers but
   the model produces them sparsely. Either the prompt needs stronger
   enforcement ("every factual claim must have a marker") or the validator
   needs a minimum anchoring ratio.
   - Effort: S (prompt tuning + validator threshold)
   - Files: `prompts/write.yaml`, `distill/pipeline.py`

2. **`--feed` iteration semantics (re-drafts everything).**
   The audit in `ITERATION_DESIGN.md` showed that `--feed` merges evidence
   by title/alias but re-drafts every page from scratch. Existing prose,
   figures, and links are lost. Need the three-operation contract
   (create/refine/merge) with re-draft triggers.
   - Effort: L (new `iteration/` subpackage)
   - Files: `distill/pipeline.py`, `distill/canonicalize.py`, new `distill/iteration/`

3. **Skeleton pages (no body) clutter the bundle and HTML output.**
   Pages extracted but never written have empty bodies. They show up in the
   HTML index and Obsidian vault as stub entries. Need either: (a) filter
   them from rendered output, or (b) write them all (budget permitting).
   - Effort: S (filter in `render/html/` and `store/bundle.py`)
   - Files: `render/html/builder.py`, `store/bundle.py`

4. **Unicode multiplication sign / smart-quote normalization.**
   5 extract rejections in the drain were caused by Unicode characters
   (multiplication sign, smart quotes) that `text_normalize` doesn't handle.
   - Effort: XS (add patterns to `agents/text_normalize.py`)
   - Files: `agents/text_normalize.py`

### Priority 2 -- Important for completeness

5. **Image consolidation mechanism (from ITERATION_DESIGN.md).**
   Refine-mode should re-evaluate figure candidates when new chunks arrive.
   A better figure should displace an older one. Design exists; no
   implementation.
   - Effort: M (new `distill/refigure.py`)
   - Files: `distill/refigure.py` (new), `distill/pipeline.py`

6. **No KaTeX/math rendering in HTML.**
   The HTML renderer does not process LaTeX math expressions. Pages with
   equations show raw `$...$` markup.
   - Effort: S (add KaTeX JS to template, or preprocess with a markdown
     extension)
   - Files: `render/html/templates/`, `render/html/builder.py`

7. **No infobox sidebar in the Wikipedia theme.**
   The HTML template has no sidebar infobox for key properties (chemical
   formula, year discovered, etc). The legacy renderer had one.
   - Effort: M (template + CSS + data extraction)
   - Files: `render/html/templates/`, `render/html/static/`

8. **bibtexparser noisy author tokens ("Dec", "USA" passing `_is_valid_author`).**
   The BibTeX author parser lets month names, country codes, and institution
   fragments through as author names. Need a stoplist or length/pattern filter.
   - Effort: S
   - Files: `ingest/citations.py` or `store/citations.py`

9. **Author page field hint uses single word instead of noun phrase.**
   The field auto-detector returns e.g. "materials" instead of "materials
   science". Author page templates use this as context, producing awkward
   phrasing like "researcher in materials".
   - Effort: XS (map short field keys to display phrases)
   - Files: `distill/author_pages.py`, `prompts/fields/`

10. **The `style_guide.md` bans em-dashes but the corpus persona might use them.**
    The layered prompt stack could produce contradictory instructions if a
    field guide or persona encourages em-dashes while the style guide bans
    them. Need explicit precedence rule in the prompt assembly.
    - Effort: XS
    - Files: `prompts/style_guide.md`, `distill/pipeline.py`

11. **The field_detect threshold was relaxed to 3 (fragile for tiny corpora).**
    A corpus with only 3-4 topics might match a field by accident. Need a
    minimum corpus size guard or a "generic" fallback.
    - Effort: XS
    - Files: `distill/field_detect.py`

### Priority 3 -- Nice to have / infrastructure

12. **`AdaptiveSchedule.reallocate` is wired but untested on real data.**
    The method exists and is called after the extract loop, but no real-binding
    run has produced enough data to verify the reallocation behavior.
    - Effort: S (integration test with real budget numbers)
    - Files: `distill/strategies/mixed.py`, `tests/wikify_simple/test_strategies.py`

13. **No Obsidian graph config file emitted (legacy had `write_graph_config`).**
    The legacy renderer wrote `.obsidian/graph.json` to configure the Obsidian
    graph view. `wikify_simple` does not.
    - Effort: XS
    - Files: `store/bundle.py` or `render/obsidian.py` (new)

14. **Graphify patterns not yet ported (open_questions.md item 6).**
    Several near-drop-in patterns from `safishamsi/graphify` were surveyed:
    - 6a: atomic cache writes (SHA256-keyed, `tmp -> os.replace`) -- not ported
    - 6b: confidence-tagged edges -- partially done (tags landed, grounding
      gate not wired)
    - 6c: Leiden-with-Louvain-fallback -- Louvain landed, Leiden not needed yet
    - 6d: god-node / concept-vs-file filters -- not ported
    - 6e: `_audit.md` -- landed
    - 6f: YAML frontmatter provenance in ingested markdown -- not ported
    - Effort: M (per pattern, most are S individually)
    - Files: `infra/cache.py`, `eval/metrics.py`, `ingest/parsers/`

15. **Slice 0b parser port incomplete.**
    DOCX, PPTX, and HTML parsers still raise `NotImplementedError`. Only PDF
    and Markdown parsers are functional.
    - Effort: M per parser
    - Files: `ingest/parsers/{docx,pptx,html}.py`

## Architecture

The `wikify_simple` package is a standalone wikification pipeline, separate from
legacy `wikify`, designed around files-on-disk (no SQLite, no ChromaDB) and a
dispatcher-based model binding. The package has these modules: `ingest/` (parse,
chunk, embed, graph, citations, bibtex, images, topics, doc_markdown, image_index),
`distill/` (extract, canonicalize, author_pages, write, crosslink, bundle),
`eval/` (metrics, audit, bundle), `store/` (corpus, vectors, wiki_files,
wiki_index, images_index, page_naming, bundle_embeddings, doc_markdown),
`render/html/` (Jinja templates + CSS), `infra/` (cache, cost_meter,
context_envelope, embedding, tokens, role), `agents/` (schema, protocols,
text_normalize), `bindings/` (fake, claude_code), `prompts/` (registry,
style_guide, fields, artifact_types, write/extract/query yamls), and `cli.py`
(ingest, distill, eval, html, query, persona-generate, field-detect).

## How to pick things up

1. Read this file + `RUNBOOK_real_binding.md` + `ITERATION_DESIGN.md`.
2. Check `git log --oneline -20` for the latest state.
3. Run `uv run pytest tests/wikify_simple/ -q` to confirm green (expect 190 tests).
4. Check `slice6_findings.md` for the latest run numbers.
5. The next high-value task is **fixing M6 anchoring** (priority 1, item 1 above):
   the writer is producing prose with only 12.5% sentence-level citation markers.
   This is the single biggest quality gap. Strengthen the write prompt to require
   a `[^eN]` marker on every factual claim sentence, and add a minimum anchoring
   ratio to the body validator. After that, run a fresh 3x distill on mvp20 to
   verify the metrics move. If anchoring reaches >= 0.5, move to the `--feed`
   iteration semantics (priority 1, item 2).
