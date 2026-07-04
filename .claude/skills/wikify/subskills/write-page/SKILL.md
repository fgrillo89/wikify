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

The FIRST thing a writer Task reads is its role brief:
`references/writer-brief.md`. It is a lossless distillation of every
writer-facing rule, schema field, and self-check in the files listed
under Required Style Layers plus the citation / constraint / schema
references. Read the brief and write from it; open a named source file
only when the brief is ambiguous or you hit an out-of-brief case. The
brief text is stable across writer Tasks, so the editor dispatches
same-role writer Tasks in one burst to keep the shared brief prefix
inside the prompt-cache TTL.

**Batched multi-slug writer Task.** One writer Task may process MULTIPLE
ready slugs sequentially, amortising the per-agent fixed overhead
(brief read + tool warmup). Each slug is processed INDEPENDENTLY: the
Task writes each slug's own `response.json` and runs `wikify draft check
<slug> --run <bundle> --dry-run` per slug. A failure on one slug is recorded in that
slug's result object and does NOT abort the other slugs; partial success
is normal.

Safety rule: the batch must be slug-disjoint from every other concurrent
Task (no two Tasks touch the same slug; the one-writer-per-slug ledger
claim holds at the SLUG level, not the Task level). This does not change
the SEED / PERSON single-Task-per-round race rule, which is unrelated.

**Return.** A single-slug Task returns one result object; a batched Task
returns an ARRAY of them, one per slug:
`{slug, response_json_path, dry_run_ok, escalate?}`. The editor iterates
the array in CONSOLIDATE (per-slug telemetry and escalation handling).

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

The role brief already distills these layers; read a source below only to
resolve a brief ambiguity or an out-of-brief case. When you do, always
consult:

1. `references/style-guide.md`
2. `../reference/references/writing/field-guides/generic.md`
3. The detected field guide when the workflow or corpus state identifies
   one with confidence.
4. The page-kind template: `article-style.md`, `person-style.md`, or
   `refinement-style.md`.
5. `references/writer-response.md`

Do not browse all field guides. Use `generic.md` by default and load at
most one additional matching field guide when the field is clear.

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

Before creating or updating `response.json`, perform a writer self-check:

- field names match `references/writer-response.md`;
- `page_kind` is present and is `article` or `person`;
- no stale fields such as `links` are present;
- every prose `[^eN]` marker has exactly one `## References`
  definition;
- every reference quote is copied verbatim from supplied evidence;
- every `{{figure:<anchor>}}` placeholder has a matching `figures[]`
  entry selected from the draft figure candidates;
- every `figures[]` entry sets all five fields, including a non-empty
  `source_marker` that appears in `used_markers`; the validator
  rejects empty markers and the renderer appends a citation link to
  the caption pointing at the source footnote;
- **figure-candidate scan**: when the dossier's Figure candidates
  table is non-empty, include at least one figure by default. Choose
  the candidate that best depicts what a section discusses. Place
  `{{figure:<anchor>}}` inside the paragraph that discusses it; that
  paragraph MUST reference it in text ("as shown in the figure",
  "(see figure)") so the figure is not orphaned. Skip only when no
  candidate is genuinely relevant; do not invent one;
- person pages have at least two non-appendix `## H2` sections;
- the page uses enough of the supplied high-quality evidence to be
  comprehensive, not merely valid;
- math regions use double-backslash JSON escapes (`\\Delta`, `\\text`,
  `\\,`) so the JSON parses and the markdown ends up with one backslash;
- **evidence-grounding spot-check**: re-apply the Evidence-grounding
  contract above to every section -- each substantive factual sentence
  ends with an `[^eN]` marker, nothing contradicts the dossier, and no
  specific entity, value, or year is smuggled in from memory.

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
- Figures are expected when candidates exist. When the dossier's
  Figure candidates table is non-empty, include at least one that
  depicts something a section discusses. Place `{{figure:<anchor>}}`
  inside the paragraph discussing it; that paragraph must reference
  it in prose ("as shown in the figure", "(see figure)"). Zero figures
  only when no candidate is relevant. Two only when the page is
  inherently visual. Never invent paths or captions. Person pages
  stay figure-free.
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
