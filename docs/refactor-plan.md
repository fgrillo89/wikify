# Refactor Plan -- COMPLETED AND ARCHIVED

This document recorded the original ScholarForge -> Wikify refactor plan. All phases below
have been completed. It is kept for historical reference only.

For current project state, see `docs/project-status.md` and `docs/architecture.md`.

---

## What Was Done

| Phase | Description | Status |
|-------|-------------|--------|
| Package rename | `scholarforge` -> `wikify`; CLI command `wikify`; GitHub repo `wikify` | Done |
| Phase 0 | Freeze baseline, capture benchmark artifacts | Done |
| Phase 1 | Separate core platform from benchmark/ALD-specific logic | Done |
| Phase 2 | Run-scoped state via `RunContext`; eliminate process globals | Done |
| Phase 3 | One canonical research pipeline; hierarchical progressive disclosure as default | Done |
| Phase 4 | Evaluation honesty: prose quality wired into composite; metric presence explicit | Done |
| Phase 5 | Generalized prompting: artifact type + field guide + journal profile as orthogonal inputs | Done |
| Phase 6 | Token economics: progressive disclosure as default policy; per-phase spend tracking | Done |
| Phase 7 | Structured error outcomes: `ok/error` envelope contract at tool boundaries | Done |
| Phase 8 | Test coverage expanded to retrieval, evaluation, generation contracts | Done (647 tests) |

## What Replaced This Document

The project now has two clearly separated pipelines:

- **Pipeline A (Research Paper Writing)**: generate -> evaluate -> revise
- **Pipeline B (Wikipedia / Epoch)**: concept discovery -> graph -> article writing -> cross-ref

See `docs/architecture.md` for the current module layout and data flow for both pipelines.
See `docs/project-status.md` for the current working state and what remains to be built.
