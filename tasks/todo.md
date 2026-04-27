# Wikify — current todo

## Branch in flight

`redesign/w9-w11-finish` — finishes W7 through W11 in one PR. Started from
`tasks/finish-skill-centric-redesign-single-session.md`. Six rounds of
review collapsed dead code, fixed real bugs, and stripped path-dependent
vocabulary.

### Shipped on this branch

- [x] W7/W8 finalisation: `wikify render --bundle`, `wikify eval --bundle [--corpus]`, `M1` + `M5_hit_rate` + `M6` + telemetry rollup in the eval payload, telemetry-parity gate, MVP-smoke test, M5 end-to-end test.
- [x] W9 canonical skill layout: shared reference under `.claude/skills/wikify/`, single-action skills (`wikify-corpus|run|work|draft|wiki|render|eval`), workflow skills (`wikify-baseline` plus stubs for `query|guided-explore|refine|render-eval|ingest|maintain`), `tests/wikify/test_skill_layout.py` enforces shape.
- [x] W10/W11 cleanup: `cli/migrate.py` removed; `LegacyBundle`, `LayoutMismatchError`, `_detect_layout` removed; `bundle/wiki/files.py` and `bundle/wiki/index.py` removed; `cli/legacy/`, `citations/__main__.py` removed.
- [x] CLI surface: seven nouns (`corpus`, `run`, `work`, `draft`, `wiki`, `render`, `eval`); `wikify` console script entry; `Bundle.open` strict (requires `run/state.json`); `run init` is the privileged bootstrap path that constructs `Bundle(root=...)` directly.
- [x] M5 producer: `corpus find` and `corpus show chunk:<id>` emit `chunk_read` events when cwd is a bundle.
- [x] Boundary IO logger: `_io.py` tees `run init` capture to a temp dir and promotes to `<bundle>/run/io/` only on success.
- [x] Schema cleanup: `WriteEvidenceRefV2 -> WriteEvidenceRef`; `WriteRequest.evidence_v2 -> evidence`; old `WriteEvidenceRef` (small form) deleted; `prompts/write.yaml` updated; all consumers + tests follow.
- [x] Path-dependent vocabulary stripped from active code, tests, skills, and live docs (`v1`/`v2`/`legacy`/`post-pivot`/`Phase C`/`W0-W11`); allowlist documented.
- [x] Reviewer-flagged stale docs fixed: `wikify migrate` removed from skills/AGENTS/architecture, "eight nouns" -> "seven" in `cli-tool-surface.md`, dead module references in `AGENTS.md` and `docs/architecture.md` removed, dead commands in `docs/filesystem-state-design.md` removed.
- [x] `RunStateV1 -> RunState`: schema versioning lives in the `schema_version` field on the data, not in the class name.

### Open before commit

- [x] Final stale-surface scan with the seven-round allowlist; zero active hits in `src/wikify`, `tests/wikify`, `.claude/skills`, `AGENTS.md`, `docs/architecture.md`, or `docs/filesystem-state-design.md`.
- [x] Compose commit message + PR body (W7-W11 + reviewer rounds 1-7).
- [x] Decide commit cadence: one squashed PR — branch is a single integrated change per the original spec.

## Open after merge

- [ ] Real-world end-to-end run. The unit + smoke + M5 tests prove the pipeline is correct; only a live run against a real corpus proves it is useful. Suggested: pick one ALD-corpus subset, run `wikify-baseline`, inspect the rendered site and `derived/eval.json`.
- [ ] Workflow stubs are stubs. `wikify-query`, `wikify-guided-explore`, `wikify-refine`, `wikify-render-eval`, `wikify-ingest`, `wikify-maintain` carry "Status: stub - composition shape only" frontmatter. Promoting any to a real workflow is its own task.
- [ ] M5 producer breadth. Today only `corpus find` / `corpus show chunk:<id>` emit `chunk_read`. Decide whether `corpus show doc:<id> --full` and `wiki find` should also emit (the agent reads chunks via several paths).
- [ ] Eval thresholds. `eval` emits the metric values; nothing today gates on them. Ship a small `eval_check` step or a CI-style threshold file once we have benchmarks from real runs.

## Review section (after each step)

_Update inline as steps complete. Per CLAUDE.md: "Document Results" lives here._
