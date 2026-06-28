# Dead / Unused Code — Ready-to-Apply Removal Proposal

> **STATUS: PROPOSAL ONLY — REQUIRES OWNER SIGN-OFF.**
> Nothing in this file has been deleted. Every command below is a *proposed*
> action for a human to review and run. Do **not** apply any group without:
> 1. an explicit owner decision on that group, and
> 2. a green `uv run pytest tests/wikify -q` immediately before **and** after.

Method: import graph built from `ast` over `src/wikify`, cross-checked with
`git grep` over `src`, `tests`, `scripts`, `.claude`. Entry points taken from
`pyproject.toml [project.scripts]` (`wikify = "wikify.cli:main"`) and the
`python -m` path (`wikify.cli.__main__`). Only git-tracked files are in scope.
All `git grep` associations below were re-verified on branch
`wikify-public-readiness-prep`.

Apply order: **Group A first**, run tests, then B, then make the per-item
decisions in C/D/E. A and B together remove ~459 lines across 5 files with
zero callers to amend.

---

## Group A — Confirmed dead modules (HIGH confidence)

Zero importers in `src`, `tests`, `scripts`, `.claude` (other than self).
No test removals required — none of these modules has a test.

```bash
# A1 context.py — 254 lines. Only external mention is a prose docstring in
#    prompts/registry.py (the word "ContextEnvelope"), not an import. Verified
#    NOWHERE else references count_tokens / ContextEnvelope / SlotSpec etc.
git rm src/wikify/context.py

# A2 eval/stats.py — 125 lines. Stat-test helpers; zero importers, not
#    re-exported by eval/__init__.py.
git rm src/wikify/eval/stats.py

# A3 eval/claim_sampler.py — 229 lines. Page/claim sampling; zero references
#    to claim_sampler / sample_claims / sample_pages anywhere but itself.
git rm src/wikify/eval/claim_sampler.py
```

**Test cross-check (Group A): none.** No test imports any of these three.
`pytest --collect-only` is unaffected.

**Downstream symbol sweep — REQUIRED after A1.** Removing `context.py` strands
its two upstream dependencies (verified: these are their *only* remaining
consumers):
- `Role` enum — `src/wikify/types.py:14` becomes dead.
- `CHARS_PER_TOKEN` — `src/wikify/config.py:4` becomes dead.

These are single symbols inside otherwise-live files, so do **not** `git rm`
the files. Propose deleting just the two definitions in the same commit, or
leave them with an explicit owner note. Re-grep before touching:
`git grep -n '\bRole\b\|CHARS_PER_TOKEN' src tests scripts .claude`.

---

## Group B — Unused re-export shims (MEDIUM confidence)

Pure indirection that each claims to be "the canonical import path," but every
real caller and every test imports the implementation directly. Re-verified:
`git grep` for both module paths returns only the shim files themselves.

```bash
# B1 bundle/work/canonicalize.py — 20 lines. Re-exports Candidate/canonicalize
#    from bundle.work.dossier. Tests import from .dossier directly; nothing
#    imports bundle.work.canonicalize.
git rm src/wikify/bundle/work/canonicalize.py

# B2 bundle/work/schema.py — 31 lines. Re-exports nine extract-side types from
#    wikify.schema. Real callers import "from wikify.schema import ..."; nothing
#    imports bundle.work.schema.
git rm src/wikify/bundle/work/schema.py
```

**Test cross-check (Group B): none.** `test_canonicalize.py` and
`test_confidence_propagation.py` already import
`from wikify.bundle.work.dossier import Candidate, canonicalize` — they do **not**
go through the shim and need no edits.

---

## Group C — Test-only modules (MEDIUM / LOW) — DECISION REQUIRED, not a blind delete

A test exists but there is no production caller. For each: either wire the
module into the product, or remove **module + its test together** in one commit.
Never delete a module while leaving its test (the suite would fail to collect).

### C1 `src/wikify/eval/audit.py` (144 lines) + `tests/wikify/test_audit.py` — MEDIUM

`write_audit(bundle, metrics, ...)` renders an audit markdown report. Sole
importer is `tests/wikify/test_audit.py` (verified). Not wired into
`cli/eval.py`. Imports live `eval.community.louvain_communities`, so removal
does **not** orphan `community.py`.

Owner picks ONE:
```bash
# C1-delete: drop the feature and its test as a pair
git rm src/wikify/eval/audit.py tests/wikify/test_audit.py
```
or **C1-keep**: wire `write_audit` into the `eval` CLI (it looks intentional and
useful) and keep the test. Default recommendation: **keep + wire in**.

### C2 `src/wikify/bundle/draft/preload.py` (79 lines) — LOW / MEDIUM

`preload_corpus -> PreloadedCorpus`. Sole importer is
`tests/wikify/test_incremental_ingest.py` (verified). Not exported by
`draft/__init__.py`; no production caller.

Owner picks ONE:
```bash
# C2-delete: remove module AND the two lines in test_incremental_ingest.py that
#            exercise preload_corpus/PreloadedCorpus (edit, not whole-file rm).
git rm src/wikify/bundle/draft/preload.py
#   then: edit tests/wikify/test_incremental_ingest.py to drop the preload import + assertions
```
or **C2-keep**: wire `preload` back into the draft pipeline. Confirm first that
incremental ingest truly no longer preloads.

---

## Group D — `corpus/graph_build.py` (197 lines) — LOW — KEEP (explicit keep/drop flag)

`build_knowledge_graph` / `save_knowledge_graph` / `load_knowledge_graph`. No
`src` caller, **but** three live tests depend on it (verified):
`tests/wikify/store/test_kg_fluent.py`, `tests/wikify/test_bib_quality.py`,
`tests/wikify/store/test_assets.py`. The module docstring is explicit: "entry
points alive for tests and ad-hoc tools." This is deliberate test scaffolding,
not an accident.

**Recommendation: KEEP.** Only revisit if the public release also drops the
KG-fluent test suite. If owner decides to drop it, the paired command is:
```bash
# D-drop (only if KG-fluent tests are also being retired — coordinate as a set):
git rm src/wikify/corpus/graph_build.py \
       tests/wikify/store/test_kg_fluent.py \
       tests/wikify/store/test_assets.py \
       tests/wikify/test_bib_quality.py
# WARNING: test_assets.py / test_bib_quality.py may assert more than the KG;
# inspect before removing whole files — prefer editing out only the KG cases.
```

---

## Group E — Repo hygiene (LOW) — not dead in the import graph

### E1 `scripts/` — tracked one-off dev tooling
Imported by nothing in the package; not in `[project.scripts]`, not under
`src/`, not in `[tool.hatch.build.targets.wheel] packages` (so it never ships in
the wheel — source-repo presentation only).

| file | keep? | reason |
|------|-------|--------|
| `effq_bench.py` | keep | referenced by `tasks/investigate-measured-savings.md` |
| `scan_bib_quality.py` | keep | referenced by comment in `ingest/metadata.py:530` |
| `audit_formula_leak.py`, `bench_embed_speed.py`, `bib_delta.py`, `compare_chunks.py`, `functional_sweep.py`, `probe_marker_vs_docling.py`, `profile_corpus_cli.py`, `profile_docling_stages.py`, `validate_ingest.py` | relocate | no external ref |

Recommendation (no deletion): move the unreferenced nine under `scripts/dev/`
(or `tools/`). These are not dead in the import graph; this is presentation.

### E2 Untracked working-dir clutter
On disk but NOT git-tracked, so excluded from the wheel and `git archive`:
`test-tmp-local3/`, `.py-0700-test/`, `.pytest-tmp-local/`,
`.pytest-tmp-local2/`, `.pytest-tmp-20260504234140/`.
Recommendation: `rm -rf` these stray dirs locally before public mirroring and
add globs (`*-tmp-*`, `.py-0700-test`) to `.gitignore`. No tracked file affected.

---

## Test-audit cross-check (from `.public-readiness/audit-tests.md`)

The independent test audit confirms the suite collects clean (1567 tests, zero
import errors), so **no test is dead-by-uncollectability**. Mapping each
proposed removal to the tests that travel with it:

| inventory item | test(s) that go with it | action on tests |
|----------------|-------------------------|-----------------|
| A1 `context.py` | none | none |
| A2 `eval/stats.py` | none | none |
| A3 `eval/claim_sampler.py` | none | none |
| B1 `work/canonicalize.py` | none (tests use `.dossier` directly) | none |
| B2 `work/schema.py` | none (tests use `wikify.schema`) | none |
| C1 `eval/audit.py` | `tests/wikify/test_audit.py` (sole importer) | delete **with** module, or keep both |
| C2 `draft/preload.py` | `tests/wikify/test_incremental_ingest.py` (exercises it) | edit out preload cases, or keep |
| D `corpus/graph_build.py` | `test_kg_fluent.py`, `test_assets.py`, `test_bib_quality.py` | KEEP (do not remove) |

**Separately flagged by the test audit (naming/framing, NOT dead code — refactor,
do not delete):** `store/test_phase2..6_*.py`, `test_m5_end_to_end.py`,
`test_mvp_smoke.py`, `tests/wikify/citestore/` directory,
`test_cite_parse.py` docstring. These assert *live* behavior under
meta-reference / milestone names; they are out of scope for this removal
proposal and tracked in the test audit. One remove-candidate noted there
(`test_phase2_dual_write.py::test_inbound_resolution_during_ingest`, a
self-skipping fragile e2e) is a test-quality call, not dead source.

The test audit also names three stale **source** dirs that hold only
`__pycache__` (no git-tracked source, nothing to `git rm`, will not ship):
`src/wikify/store/`, `src/wikify/_prototype/`, `src/wikify/citestore/`.
Clean locally with `find src/wikify/{store,_prototype,citestore} -name __pycache__ -type d`.

---

## Sign-off checklist (owner)

- [ ] Group A approved → run A1+A2+A3, plus the `Role`/`CHARS_PER_TOKEN` symbol sweep
- [ ] Group B approved → run B1+B2
- [ ] C1 decision: __ delete (module+test) __ keep+wire-in
- [ ] C2 decision: __ delete (module+test edit) __ keep+wire-in
- [ ] D confirmed KEEP (or coordinate a paired test drop)
- [ ] E1 relocate plan agreed; E2 local cleanup + `.gitignore` globs
- [ ] `uv run pytest tests/wikify -q` green before and after each applied group

| confidence | action | items |
|-----------|--------|-------|
| HIGH | delete file (no test) | `context.py`, `eval/stats.py`, `eval/claim_sampler.py` |
| MEDIUM | delete unused shim (no test) | `work/canonicalize.py`, `work/schema.py` |
| MEDIUM | wire-in OR delete module+test | `eval/audit.py` (+`test_audit.py`) |
| LOW | wire-in OR delete module+test edit | `draft/preload.py` (+`test_incremental_ingest.py`) |
| LOW | KEEP (flagged) | `corpus/graph_build.py` (+3 tests) |
| LOW | relocate / clean | `scripts/*`, stray `*-tmp-*` dirs |
