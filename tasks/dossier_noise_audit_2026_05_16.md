# Dossier noise audit + revamp (2026-05-16)

The PR #72 smoke run produced 16 article dossiers. Visual inspection of the
ALD and a handful of adjacent dossiers showed a meaningful fraction of the
evidence + adjacent-context chunks were boilerplate (editorial boards,
affiliations, DOI/publication-history lines, CC-BY copyright paragraphs) or
otherwise irrelevant to the concept the dossier was assembled for.

Two layers contribute:

1. The semantic retrieval that selects evidence per concept is letting
   author bylines, abstracts of off-topic papers, and similar low-signal
   chunks land in the top-k. Example: `wikify work build-evidence
   atomic-layer-deposition` selected e1 = Li-2018 byline + abstract of an
   IMC paper where ALD is barely mentioned.
2. The ingest-time boilerplate filter (`src/wikify/ingest/boilerplate.py`,
   `BOILERPLATE_MARKERS`) misses Frontiers "EDITED BY / REVIEWED BY"
   blocks, "Citation:" / "Received: / Accepted:" publication-history lines,
   and CC-BY paragraphs. These slip through as ordinary body chunks and
   surface in dossiers as "adjacent context".

The `<details>Adjacent chunks (synthesis context, do not cite)</details>`
block in the rendered dossier markdown actively misled 3 of 5 writer
subagents in the smoke run -- they cited adjacent chunks as if they were
canonical evidence.

## Investigations to do

- Semantic-search relevance audit: for each smoke-pass concept, list the
  top-N retrieved chunks with a per-chunk relevance score; flag chunks
  where the concept token (or any alias) does not appear and the
  embedding similarity to the concept-title embedding is below some
  threshold.
- Boilerplate-filter audit: sample ~50 dossier evidence + adjacent chunks
  across the smoke bundle, label each as `useful | marginal | boilerplate`,
  and grep the corresponding chunk text against `BOILERPLATE_MARKERS`. New
  markers to consider: `EDITED BY`, `REVIEWED BY`, `Received:`, `Accepted:`,
  `Published:`, `Citation:`, lines beginning with a DOI prefix, "Copyright
  (C)" paragraphs, CC-BY badge text.
- Adjacent-context block decision: pick one of
  - (a) remove the adjacent block from the dossier entirely;
  - (b) keep but render distinctly (e.g. visually muted, separate heading)
    so cite-grounded validators can detect citations into it as a fail;
  - (c) move it behind a `--with-adjacent-context` flag that defaults
    `off`.
  Owner of the decision: render/dossier maintainer. Validator must change
  in lockstep with whichever option lands.
- Per-concept checklist: for each of the 16 smoke-pass concepts, count
  `(useful, marginal, boilerplate)` evidence chunks. Kumar 2025 alone
  contributed 3 boilerplate chunks (editor list, citation header,
  affiliation block) into the ALD dossier; this is probably not unique.

## Out of scope here

Backfilling `assets.metadata_json` width/height for 207 corpus docs and
rebuilding the corpus is a separate task.
