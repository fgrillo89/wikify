# Meta-Commentary Audit

Scan target: source docstrings/comments, `.claude/skills`, `docs/`, `AGENTS.md`,
`README.md`. Goal: find narrative about *how the code was developed* (plan
references, dev stages, debugging anecdotes, named experiment corpora,
session/PR acknowledgements, decision stories) that should be rewritten as
prescriptive descriptions of what the code IS / DOES.

## Headline

- **No plan/todo/session/PR references found in shipped code, skills, or docs.**
  Searched `tasks/`, `per tasks`, `this PR`, `this commit`, `recently merged`,
  `we decided`, `in this phase`, `the redesign`, session/prompt acknowledgements.
  The only literal `tasks/` and "No meta-references" hits are the *rules
  themselves* (`AGENTS.md:237`, `.claude/skills/wikify-gather-evidence-cluster/SKILL.md:370`
  write-restriction, banned-phrase lists in prompt style guides) — not violations.
- The real hits are **development-history narrative inside code comments/docstrings**:
  past code states, a parser-probe "Stage B1.5", named dev corpora (`mvp20`),
  and a specific debugging anecdote (`Chua page 11`). These read as changelog,
  not as documentation of current behavior.
- **"Phase 1/2/3/4" labels** in `citations/resolver.py`, `citations/db.py`,
  `sources/arxiv.py`, `ingest/dag.py`, `cli/work.py` describe *algorithm stages*
  and are explicitly allowed by CLAUDE.md/AGENTS.md. Reviewed, not flagged.

## Findings (fix these)

### Medium — development-process / experiment narrative

1. `src/wikify/ingest/parsers/registry.py:157-160`
   > "The DEFAULT was previously Marker for PDFs; the swap landed after Stage
   > B1.5 of the parser probe showed Docling's median wall-clock is within ~13%
   > ... (n=20) ..."
   References an internal "parser probe / Stage B1.5" development effort and
   narrates the decision. Rewrite to state *why Docling is the default now*
   (uniform structural extraction, in-tree formula head, wall-clock parity)
   without the "previously Marker / swap landed after Stage B1.5" story.

2. `src/wikify/ingest/figures.py:163-166`
   > "The 47 pN_imgM binaries we used to keep added 29% noise to mvp20 with
   > zero downstream value. The previous behavior is restored by passing
   > keep_uncaptioned=True ..."
   Cites a named development corpus (`mvp20`) and a past behavior. Rewrite to
   state the rule prescriptively: uncaptioned image binaries are page-graphic
   noise with no semantic anchor and are dropped; `keep_uncaptioned=True`
   retains them for diagnostic ingests.

3. `src/wikify/ingest/figures.py:194-199`
   > "Previously this loop emitted a separate binary for every caption ...
   > (Chua page 11 had Fig. 7 and Fig. 8 both backed by the same page-bytes
   > blob). The dedup hash discriminated by cap.label ..."
   Debugging anecdote tied to a specific document. Rewrite to describe current
   behavior: on a scanned page the full-page raster backs only the first
   matched caption; subsequent captions surface via `figure_refs`. Drop the
   "Previously ... Chua page 11" story.

### Low — past-state / migration phrasing (reword to present tense)

4. `src/wikify/corpus/queries.py:847-848`
   "`find(by="chunk", rank="pagerank")` previously fell through to the document
   ranking and returned doc rows instead of chunks." — past-bug narrative.
   State what it does now: projects the doc metric onto chunks and returns
   chunk rows.

5. `src/wikify/ingest/parsers/pdf.py:34`
   "... much more reliable than the downstream line/paragraph regex scrubbing
   we previously leaned on." — comparison to an abandoned approach. State the
   header/footer suppression behavior without the "we previously leaned on".

6. `src/wikify/ingest/hybrid_chunker.py:22-24` and `:192`
   "equation binding has already migrated to text-match" / "Equation/citation
   binding has migrated to ..." — migration narrative. Reword to "equation
   binding uses text-match, so the offset is best-effort, not load-bearing".

7. `src/wikify/embedding.py:200-201`
   "DirectML can silently route ops to CPU when VRAM is exhausted, which
   previously presented as a 4-12 h hang ..." — borderline; the symptom
   rationale is useful but the "previously presented" framing is historical.
   Reword to "which manifests as a multi-hour hang ...".

8. `src/wikify/bundle/work/chunk_ids.py:8`
   "The MCP / CLI layer historically surfaced a short *handle* form ..." — the
   handle form still exists; "historically" is misleading. Reword to present
   tense ("also accepts a short handle form").

9. `src/wikify/ingest/bibtex.py:458`
   "One structural rule in place of the several per-publisher regexes that used
   to live here." — dead-code breadcrumb. Drop the "used to live here" clause;
   keep "one structural rule replaces per-publisher regexes" as rationale or
   remove entirely.

## Reviewed and intentionally NOT flagged

- `citations/resolver.py` (Phase 1-4), `citations/db.py:357` (Phase 4),
  `sources/arxiv.py` (phase 1 identify / phase 2 download), `ingest/dag.py`
  (Phase 3/4 title index), `cli/work.py:1084,1097` (Phase 1/2 seed/top-up):
  algorithm-stage labels, allowed by project rules.
- `ingest/topics.py:168,237` ("originally", "previously"): stopword/banned-phrase
  list entries, not narrative.
- Numerous "Used to <verb>" docstrings (purpose statements) and "legacy" /
  backwards-compat shims (`corpus/store/kg.py`, `render/html/render.py`,
  `bundle/wiki/graph.py`, etc.): describe current compatibility behavior, not
  development history.
- `\uXXXX` / `10.XXXX` / `cites=N \t pr=X.XXXX`: literal format placeholders.
- `docs/` and `.claude/skills/`: clean of development-history narrative.
