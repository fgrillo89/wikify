# wikify_simple iteration architecture

Status: design proposal, awaiting review. No implementation yet.

This note answers two open questions from the user:

1. Is there a consolidation mechanism that involves images? After reading
   many papers on later runs, the model might find a more suitable image
   for an older wiki page.
2. Is the improvement and possibly the merging of previous wikis
   prescribed by the wikification? If not, address.

## 1. Audit of `--feed` today

`distill/pipeline.run` accepts `feed: bool` and, when set, calls
`_load_existing_pages(bundle)` to parse all `.md` files under `concepts/`
and `people/` back into `WikiPage` objects (evidence list, links, body
markdown). Those pages are then passed to `canonicalize(candidates,
existing=...)`.

What `canonicalize` actually does with `existing`:

- Builds an `alias_index` from `(_normalize(title), _normalize(alias))`
  to `page.id` for every existing page.
- For each new `Candidate`, looks up the normalized title in the alias
  index.
- If a match is found: appends a NEW `Evidence` to the existing page's
  evidence list (the marker is `e{len(evidence)+1}`).
- If no match: creates a fresh `WikiPage` with `body_markdown=""`.
- Returns the union of new + updated pages.

What the rest of `pipeline.run` does next:

- The write loop iterates `pages[:max_concepts]` -- including pages that
  came from `existing` -- and calls `writer.write(req)` on each one. The
  writer's response REPLACES `page.body_markdown` outright. There is no
  branch for "this page already has good prose; skip it".
- `pages = [p for p in pages if p.evidence]` drops unsupported pages
  but does not preserve any prior body that wasn't re-drafted.
- `crosslink(pages)` re-runs on the unioned set, recomputing links from
  scratch. Old `links` lists are not preserved.
- `write_page_file(bundle, page)` overwrites the existing `.md` file
  with the new body, frontmatter, and evidence block. The sidecar
  `.provenance.json` is also overwritten with this run's provenance.
- `build_index(bundle, pages).save()` rebuilds the wiki index from the
  unioned set. There is no incremental update.

Figures:

- Figures are computed inside the write loop: for each page,
  `images_index.for_doc(did)` is called for every doc_id present in the
  page's evidence. Whatever the writer chooses to embed lands in the
  body. Old figures are not re-evaluated against new ones because the
  whole body is rewritten.

Bottom line: today's `--feed` MERGES EVIDENCE by title/alias but
RE-DRAFTS EVERY PAGE FROM SCRATCH. Existing prose is lost. Existing
figures are lost. Existing links are lost. The only thing that survives
across runs is the `id`, `title`, `aliases`, and the union of evidence
entries. The `_run.json` snapshot records `feed: true` but that is the
extent of cross-run provenance.

## 2. Three-operation iteration vocabulary

Today's pipeline collapses iteration into a single boolean flag. Propose
a three-operation contract:

| op       | inputs                                | outputs       | what it does |
|----------|---------------------------------------|---------------|--------------|
| create   | corpus, strategy, budget              | new bundle    | today's default `run(feed=False)` |
| refine   | corpus, prior bundle, strategy, budget| same bundle, mutated in place | merge new evidence + figures into existing pages, re-draft only when triggered |
| merge    | bundle A, bundle B                    | new bundle    | union of two bundles, alias-merge across them, no LLM call |

What's already there:

- `create`: works.
- `refine` partial: `--feed` provides the loader + the alias merge in
  `canonicalize`. But the re-draft policy is wrong (rewrites everything)
  and figures/links are lost.
- `merge`: not implemented.

Minimal structural change to get all three first-class:

- Replace `feed: bool` with `iteration: Literal["create","refine","merge"]`
  on the pipeline entry point. Keep `--feed` as a deprecated alias for
  `--iteration refine` for one release.
- Pull the existing-bundle loader out of `pipeline._load_existing_pages`
  into a small `iteration/` subpackage with three functions:
  `create_pages`, `refine_pages`, `merge_pages`. Each returns the same
  `(pages, page_history)` tuple the writer + crosslink consume.
- `merge_pages` is purely data: load both bundles, run the same
  alias-merge as canonicalize, dedupe evidence by `(chunk_id, quote)`,
  union links, no writer call. The result is a NEW bundle dir.
- `refine_pages` introduces re-draft TRIGGERS (see section 4 below)
  instead of unconditionally rewriting every page.

## 3. Image consolidation mechanism (refine mode)

Goal: when refine-mode pulls in new chunks for an existing page, the
page's figures should be re-evaluated against the new candidate pool.
A better figure should be able to displace an older one.

Proposed shape:

1. For each existing page in the loaded bundle, parse its current
   embedded figures from the body markdown (the same regex
   `_FIGURE_EMBED_RE` already used by the figure-mention validator) and
   look each one up in `ImageIndex` to recover its caption + label.
2. Score every candidate figure (existing + newly-collected from the
   docs touched in this refine run) against the page's body text:
   `score = cosine(embed(caption + " " + nearby_prose), embed(body_clean))`.
   The "nearby prose" is the 200 characters around the figure's
   `near_chunk_ids` reference in the page evidence.
3. Sort candidates by score descending. Keep the top N (default N=3).
   If the new top-1 beats the current top-1 by a margin (e.g. 0.05
   absolute cosine), perform a swap: remove the old `![...](...)` line
   and insert the new one near the sentence that mentions the figure
   number, reusing the writer-side `_check_figure_mentions` validator
   to confirm the mention is still present.
4. Provenance: every figure swap records `(run_id, old_figure_id,
   new_figure_id, old_score, new_score)` in
   `provenance["figure_history"]` so the swap is auditable.

Where the logic lives: a new module `wikify_simple/distill/refigure.py`
(<= 250 LOC). The refine-mode caller invokes it after canonicalize and
before the write loop, BUT only on pages that are NOT going to be
re-drafted (re-drafts already get fresh figures from the writer).

`ImageIndex` is the source of candidates -- no new store. The cosine
scoring uses the existing corpus embedder via `embedder_for(meta)`.

Out of scope for refine: figure DEDUPLICATION across pages (one figure
referenced from multiple pages), and figure CAPTION SUMMARISATION (the
caption is used as-is). Both are listed as follow-ups in
`open_questions.md` and not blocking the refine contract.

## 4. Provenance: per-page run history

Today the page sidecar `.provenance.json` records only the last run's
`run_id`, `model`, `strategy`, plus the confidence aggregates added in
phase 2. Refine-mode needs MORE: an append-only history of which run
contributed which evidence and which figure.

Proposed schema for `.provenance.json`:

```json
{
  "current_run_id": "abc123",
  "history": [
    {
      "run_id": "run-001",
      "op": "create",
      "model": "haiku",
      "added_evidence": ["e1", "e2", "e3"],
      "added_figures": ["docA/Figure_01"],
      "drafted_body": true,
      "timestamp": "2026-04-08T19:00:00Z"
    },
    {
      "run_id": "run-002",
      "op": "refine",
      "model": "sonnet",
      "added_evidence": ["e4"],
      "added_figures": [],
      "swapped_figures": [
        {"old": "docA/Figure_01", "new": "docB/Figure_03",
         "old_score": 0.41, "new_score": 0.62}
      ],
      "drafted_body": false,
      "trigger": "below_redraft_threshold",
      "timestamp": "2026-04-08T20:00:00Z"
    }
  ],
  "confidence_scores": [...],
  "confidence_min": 0.5,
  "confidence_mean": 0.85
}
```

The history list is append-only and survives every refine. It is the
source of truth for "when did this page last change", "who wrote this
sentence", and "which run added this figure".

`_run.json` (per-run snapshot) records the inverse: which page ids were
touched by this run. Together they form a bipartite run/page log.

## 5. Re-draft triggers

Refine-mode must NOT call the writer on every page. Re-drafting is
expensive (M/L tier) and destroys human-valuable prose. The writer is
called on an existing page only when at least one of these is true:

- **K-evidence trigger**: the page accumulated >= K new evidence entries
  this run (default K=3). Below K, the new evidence is appended to the
  evidence list and the body is left alone. The Evidence section in the
  rendered .md will show all entries; the prose only references the
  ones it knows about.
- **Figure-swap trigger**: a figure was swapped this run. The writer
  needs to re-anchor the figure mention to the new image.
- **Forced trigger**: the user passed `--rewrite-all`. Treats every
  page as if K=0 was crossed.
- **New-page trigger**: the page was created this run (no prior body).
  Always drafted -- this is the same as create-mode.

Pages that don't trip any trigger are written back to disk unchanged
EXCEPT for: the new evidence entries appended to the evidence block,
the new links from crosslink, and the new history entry in
`.provenance.json`.

## 6. Test plan (no code yet)

Tests that would verify the contract above. To be written at
implementation time, not now:

- `test_refine_preserves_body_below_threshold`: create a 2-page bundle,
  call refine with one new chunk that adds one evidence to one page;
  assert the page body markdown is byte-for-byte unchanged except for
  the appended evidence footnote.
- `test_refine_redrafts_above_k`: same setup but with 4 new evidence
  entries on one page; assert the body was rewritten and the
  `.provenance.json` history shows `drafted_body: true`.
- `test_refine_swaps_figure_when_better_match`: create a page with one
  figure scored 0.3 against the body; run refine with a new candidate
  scored 0.6; assert the embedded figure path changed and the history
  records the swap.
- `test_refine_keeps_figure_within_margin`: same but new candidate
  scores 0.32; assert no swap.
- `test_refine_history_grows_append_only`: run refine 3 times; assert
  `.provenance.json["history"]` has 4 entries (1 create + 3 refine) in
  chronological order.
- `test_merge_unions_two_bundles`: create bundles A and B with one
  shared concept (by alias) and one unique each; call merge; assert
  the result has 3 pages and the shared one's evidence is the union.
- `test_merge_no_writer_calls`: assert merge mode does not invoke the
  writer at all (cost meter shows 0 writer calls).
- `test_create_unchanged`: smoke test that create-mode behaves
  identically to today's `feed=False` after the iteration refactor.

## 7. Out of scope

Explicitly NOT addressed by this design:

- Cross-corpus deduplication of identical figures (one image file
  appearing in two papers).
- LLM-driven figure captioning when the source caption is empty.
- Cross-page figure linking (a single `Figure 3` from doc A used as
  the canonical illustration on five pages).
- Body-level edit suggestions ("this paragraph contradicts the new
  evidence; rewrite"). Refine is conservative on prose by design.
- Multi-bundle merge beyond 2 inputs. `merge` takes exactly two
  bundles; chain it for more.
