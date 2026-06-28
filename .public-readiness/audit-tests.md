# Test Suite Audit — Public-OSS Readiness

Scope: `tests/` (125 files, 1567 tests). Goal: find tests of dead code,
tests asserting superseded behavior/patterns, duplicates, and low-value
tests. Recommend keep / refactor / remove per finding.

## Method

- Enumerated all `tests/**/*.py`, mapped each suspicious file to its
  target module(s) via imports.
- Ran `pytest tests/wikify --collect-only`: **all 1567 tests collect
  with zero import errors** → no test imports a removed module, so there
  are **no truly dead (uncollectable) tests**.
- Grepped for migration/milestone naming (`phase[2-6]`, `M5`, `MVP`,
  `slice N`, `Fxx regression`, `legacy`, `dual-write`) and for
  `pytest.skip`.
- Attempted to cross-check against a dead-code inventory: **none exists
  yet** (no `tasks/public-readiness/`; `.public-readiness/` holds only
  `research-*.md`). This audit must be re-run after the inventory lands.

## Dead-code cross-check (independent findings)

No tests depend on the stale/dead source dirs below, so none need
removal on their account — but they belong in the dead-code inventory:

- `src/wikify/store/` — only `__pycache__`, no git-tracked source. The
  `tests/wikify/store/` suite does **not** import `wikify.store`; it
  imports the live `wikify.corpus.store` / `wikify.bundle.wiki.store`.
  The test directory name mirrors an old layout but the tests are live.
- `src/wikify/_prototype/` — only `__pycache__` (`sqlite_store`); no
  source, no importers, no tests. Pure dead artifact.
- `src/wikify/citestore/` — only `__pycache__`. The module was renamed
  to `wikify.citations`; every test under `tests/wikify/citestore/`
  already imports `wikify.citations.*`. Directory name is a retired-name
  leftover.

## Findings

### 1. Migration-era "phaseN" test naming asserts live behavior (medium)
`tests/wikify/store/test_phase2_dual_write.py`,
`test_phase3_query_routing.py`, `store/test_phase4_wiki_db.py`,
`store/test_phase5_metrics.py`, `store/test_phase6_global_metrics.py`.

The asserted behavior is current (SQLite is the single authoritative
backend), but filenames + docstrings ("Phase 4 acceptance", "Phase 5
acceptance", "dual_write", "query_routing", "Re-resolve was triggered
during dual-write", "legacy ingest") are meta-references to a finished
migration project. This violates the repo's own "No meta-references" and
"No dead versioning" rules and reads poorly in public OSS. These are
project-phase labels, **not** algorithm-stage labels, so the
CLAUDE.md carve-out does not apply.
Recommendation: **refactor** — rename to behavior-describing names
(e.g. `test_ingest_write.py`, `test_query_rank.py`, `test_wiki_db.py`,
`test_cheap_metrics.py`, `test_global_metrics.py`) and strip the
"phase/dual-write/legacy" framing from docstrings/comments.

### 2. Milestone-named e2e/smoke tests (medium)
`tests/wikify/test_m5_end_to_end.py`, `tests/wikify/test_mvp_smoke.py`.

Valuable end-to-end coverage (full CLI lifecycle, M5 hit-rate plumbing),
but "M5" / "MVP" are internal milestone meta-references. Also import
fixtures across test modules (`from tests.wikify.test_mvp_smoke import
_good_response_payload`, `from tests.wikify.test_corpus_queries import
_make_corpus`), coupling test files together.
Recommendation: **refactor** — rename to describe behavior
(`test_hit_rate_e2e.py`, `test_cli_lifecycle_smoke.py`); move shared
payload/corpus builders into `conftest.py` fixtures instead of
cross-importing test modules.

### 3. Fragile, self-skipping ingest test (low)
`tests/wikify/store/test_phase2_dual_write.py::test_inbound_resolution_during_ingest`.

The test documents its own non-determinism and `pytest.skip`s when "the
legacy ingest replaced synthetic bib row", deferring to the unit-level
`test_inbound_resolution_by_doi`. It couples to internal ingest refresh
behavior and asserts little reliably.
Recommendation: **remove** the e2e variant (keep the cited unit-level
guarantee), or **refactor** to a deterministic fixture that does not
depend on whether the cite parser overwrote the synthetic row.

### 4. Stale "citestore" naming in tests (low)
Directory `tests/wikify/citestore/` and the docstring of
`tests/wikify/test_cite_parse.py` ("Tests for citestore.parse",
"citestore's standalone citation parser") reference the retired
`citestore` module name; the code under test is `wikify.citations.*`.
Recommendation: **refactor** — rename `tests/wikify/citestore/` to
`tests/wikify/citations/` and fix the docstrings. (Source-side: the
`wikify.ingest.cite_parse` docstring carries the same stale name — flag
for the source dead-code/naming sweep.)

### 5. `test_skill_layout.py` will churn during the skill reorg (low)
`tests/wikify/test_skill_layout.py` hardcodes current skill names
(`CORE_SKILLS`), retired-command patterns, retired CLI-noun skill names,
line-count ceilings (200/250), and the `references/` tree. It is a
genuinely useful guard (dangling links, frontmatter, retired surface),
**not** low-value — but the public-readiness skill rename/reorg will
break it unless updated in lockstep.
Recommendation: **keep**, and update it in the same commit as any skill
rename/move so CI stays green. Do not delete.

## Non-findings (verified live, not duplicates)

- `test_bibtex.py` (tests `ingest.bibtex` author cleaning) vs
  `citestore/test_bibtex.py` (tests `citations.bibtex.openalex_to_bibtex`)
  — distinct modules, **not** duplicates.
- `test_citations.py` (`ingest.citations.extract_citations`, live: used
  by `ingest/pipeline.py` + `ingest/rechunk.py`) vs `test_cite_parse.py`
  (`citations.parse`) — distinct, both live.
- `test_images.py` / `test_images_index.py` / `test_image_metrics.py` /
  `test_figure_refs.py` / `test_caption_reassignment.py` — five distinct
  modules, no overlap.
- `marker` parser tests (`test_marker_parser.py`, `test_parsers.py`)
  remain valid: Marker is a still-supported `--parser marker` backend.

## Summary recommendation

No tests need removal for dead code (suite collects clean). The dominant
public-readiness issue is **superseded naming/framing** (phaseN, M5,
MVP, citestore) on otherwise-live tests — refactor, do not delete. One
fragile self-skipping test (Finding 3) is the only remove candidate.
Re-run this audit once the dead-code inventory exists to confirm no
test exercises a module slated for deletion.
