---
name: write-page
description: Produce a Wikify WriteResponse from supplied page context and evidence. Use when a workflow has selected a concept/page and needs encyclopedic article, person-page, comparison, or refinement prose grounded in evidence. Does not commit pages or decide readiness.
allowed-tools: Bash(wikify draft show *), Bash(wikify draft check *)
---

# write-page

Use this skill for the model-writing step. The workflow supplies the
page context and decides why the page should be written. This skill
produces `response.json` content that must later pass validation.

## Role brief (read this first)

The FIRST thing a writer Task reads is its role brief,
`references/writer-brief.md` — a lossless distillation of every
writer-facing rule, schema field, and self-check in the Required Style
Layers plus the citation / constraint / schema references. Write from the
brief; open a named source only when the brief is ambiguous. The brief is
stable across writer Tasks, so the editor dispatches same-role writer
Tasks in one burst to keep the shared brief prefix inside the prompt-cache
TTL. A writer Task MAY batch multiple ready slugs (each processed
independently — own `response.json` + `wikify draft check <slug> --run
<bundle> --dry-run`; per-slug failure does not abort others; batch stays
slug-disjoint from other Tasks; returns an array of
`{slug, response_json_path, dry_run_ok, escalate?}`). Full batched
contract in the brief; it does not change the SEED/PERSON single-Task race rule.

## Inputs

- **`dossier.md`**: the primary evidence artifact for the writer.
  Markdown with frontmatter (`page_id`, `kind`, `aliases`,
  `evidence_records`, `distinct_sources`, `year_range`,
  `section_types`), a marker index table, and chunks grouped by source
  document in reading order. Each chunk shows its `[eN]` marker,
  section type, full text, any equations/tables/figure captions it
  references, and optional adjacent-chunk context inside a
  collapsible `<details>` block. When suitable images are available,
  it also includes a Figure candidates section and an **Available data**
  citation index. Ground GENERAL claims inline (attach the `Cite as`
  marker to the value), never paste it as a table/column; name any data
  artifact in prose (auto-linked under Related data; no `[[wikilinks]]`).
- `draft.json`: the canonical structured contract validators read.
  The writer normally does NOT need to read it; `dossier.md` carries
  the same evidence in human-readable form. Open `draft.json` only
  when the dossier is ambiguous or when checking exact chunk_ids.
- Page title, aliases, and page kind: at the top of the dossier
  frontmatter.
- Requested writing mode: article, person, comparison, or refinement.
- Field guide selection from the workflow or detected corpus field.

## Required Style Layers

The role brief distills these; read a source below only to resolve a brief
ambiguity or an out-of-brief case: `references/style-guide.md`;
`../reference/references/writing/field-guides/generic.md` plus at most ONE
detected field guide (never browse all); the page-kind template
(`article-style.md` / `person-style.md` / `refinement-style.md`); and
`references/writer-response.md`.

## Evidence-grounding contract

The dossier is the substrate, not a hint. Memory is the fallback
layer, not the primary one. Three sentence categories:

1. **Substantive factual**: any value, parameter, mechanism,
   material, year, name, comparison, or measurement (precursor names,
   temperature windows, GPC values, authors, stoichiometries, device
   parameters, on/off ratios, endurance counts) MUST end with at
   least one `[^eN]` marker pointing at the supporting dossier chunk.
   If the dossier does not support the claim, do not make it.
2. **Scaffolding**: short transitions, definitions the dossier
   itself supplies, and standard encyclopedia framing may go uncited.
   If a section has more uncited than cited sentences, you are
   drifting into memory mode.
3. **Memory-derived**: permitted only for uncontroversial
   undergraduate-textbook background the dossier omits. Never
   introduce specific entities, papers, values, or claims from
   memory that are absent from the dossier.

Hard grounding rules:

- **Dossier wins.** When the dossier covers a topic, use its
  version even if memory disagrees. The dossier was curated by an
  upstream vetter; the writer renders, not re-derives.
- **No contradictions.** If the dossier says "~0.43 Å/cycle" and
  memory says "~1 Å/cycle", use 0.43. Surface suspected dossier
  errors as a workflow signal; do not silently overwrite.
- **Definition leads.** When the dossier contains a definition-style
  chunk (vetter scores it 1.0, rendered under `> **Selected quote:**`
  at the top of the evidence section), open with prose that
  paraphrases or quotes it.
- **Quote means quote.** Each `[^eN]:` reference definition contains
  a verbatim sentence from that chunk's text, carrying the claim
  you're citing, not the chunk head or a transition.
- **Self-flag memory mode.** Multiple sentences in a row without
  `[^eN]` markers means stop and re-read the dossier.

## Output

Write strict JSON matching `WriteResponse`. The usual target path is:

```text
work/concepts/<slug>/response.json
```

Before creating/updating `response.json`, run the full writer self-check
from the brief (`references/writer-brief.md`): correct `WriteResponse`
fields (per `writer-response.md`) and `page_kind` (`article`/`person`); no
stale fields; every prose `[^eN]` resolves to exactly one verbatim
`## References` definition; figures follow the figure rule (up to
`max_article_figures = 4`, at most ONE per distinct source document, each
with a `source_marker` in `used_markers`, placed inside and referenced by
the paragraph that discusses it; skip when no candidate fits; person pages
figure-free); person pages have >= 2 non-appendix `## H2` sections; the
page uses enough high-quality evidence to be comprehensive; math uses
double-backslash JSON escapes; and every substantive factual sentence ends
with an `[^eN]` marker grounded in the dossier (nothing contradicting it,
nothing smuggled from memory).

For a deterministic structural pre-check, pipe the candidate JSON into
`wikify draft check --dry-run`:

```bash
echo '<response.json candidate>' | wikify draft check <slug> --run <bundle> --dry-run --format json
```

The dry-run reads the candidate from stdin, validates it against the
on-disk draft, and prints the verdict without writing `response.json`
or `validation.json`. Fix any errors before persisting.

## Hard Rules

- Ground every substantive factual claim in the supplied dossier
  (see "Evidence-grounding contract" above). The dossier wins over
  memory whenever the two could disagree.
- Use `[^eN]` markers and matching `[^eN]:` reference definitions.
- Reference quotes must be verbatim substrings of source chunks. The
  quoted sentence should be the one that carries the cited claim, not
  the chunk's leading byline or a generic transition sentence.
- Prefer explanatory prose chunks over author headers, affiliation
  blocks, bibliography/reference-list chunks, acknowledgments, generic
  figure captions, tables, ORCID/contact fragments, and other
  boilerplate. If the workflow accidentally supplies those records,
  ignore them unless the page scope explicitly requires them.
- NEVER cite a chunk whose text is metadata or boilerplate, even if
  it appears in `evidence`: ISSN/DOI/journal-homepage banners,
  article-history lines, keyword blocks, affiliation /
  corresponding-author blocks, copyright lines, DoD SF298 covers,
  bibliography-list dumps, or pure figure/table captions when a body
  chunk would do. See `../reference/references/writing/write-constraints.md`
  for the pattern list. The writer is the last line of defense: if
  after filtering you cannot field 6 evidence markers per page,
  surface that as a workflow signal rather than padding with junk.
- For normal articles, use evidence across definitions, mechanisms,
  materials/properties, methods, evidence/results, applications, and
  limitations when those facets are present in the draft.
- No visible `[[wikilinks]]`.
- Figures are expected when candidates exist. An ARTICLE page SHOULD
  include figures where the dossier's Figure candidates table supports
  them, up to `max_article_figures = 4`, at most ONE figure per distinct
  source document, and each figure tied to a distinct cited
  source/section (its `source_marker` in `used_markers`). Place
  `{{figure:<anchor>}}` inside the paragraph discussing it; that
  paragraph must reference it in prose ("as shown in the figure",
  "(see figure)"). Zero figures only when no candidate is genuinely
  relevant. Never invent paths or captions. Person pages stay
  figure-free.
- No corpus meta-commentary.
- No page commit. Validation and commit are `bundle` operations. The
  commit step (`draft finalize`) is a one-shot: it consumes the draft,
  garbage-collecting `draft.json`/`response.json`/`validation.json` on
  success. A repeat `draft finalize` then returns `draft_not_found`,
  which means the page was already committed, not that the draft was
  never built. Do not re-run the writer or re-finalize on that signal.

## Optional Substeps

Workflows may stage as `evidence -> write`, `evidence -> compaction
-> write`, or `evidence -> compaction -> editor brief -> write`. Use
`compaction.md` and `editor-brief.md` only when the workflow asks.

## References

- `references/writer-brief.md`
- `references/writer-response.md`
- `references/style-guide.md`
- `references/article-style.md`
- `references/person-style.md`
- `references/refinement-style.md`
- `references/editor-brief.md`
- `references/compaction.md`
- `../reference/references/writing/citation-format.md`
- `../reference/references/writing/write-constraints.md`
