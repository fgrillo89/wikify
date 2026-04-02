# ScholarForge Refactor Plan

This document outlines a refactor plan for ScholarForge based on the current repo state, the stated product goals, and the issues found in the review.

The aim is not just to clean up code. It is to turn ScholarForge into a coherent, domain-agnostic research system that can help researchers explore literature, talk to it, and produce high-quality reviews and papers with strong generalization and disciplined token usage.

## North Star

- One canonical pipeline for `explore -> retrieve -> synthesize notes -> draft -> verify -> evaluate -> revise`
- One shared substrate for chat, review writing, paper writing, and future "talk to literature" workflows
- Domain- and benchmark-specific logic layered on top as profiles, not embedded in core runtime paths
- Evaluation that honestly reflects what the system optimizes for: output quality, generalization, and token efficiency

## Current Problems To Solve

- Evaluation does not fully match the repo's stated quality model. In particular, prose quality is defined but not fully integrated into the composite quality flow.
- Generic generation paths still contain ALD/memristor-specific assumptions.
- The default runtime path does not fully reflect the repo's own hierarchical retrieval findings.
- Session state is process-global rather than run-scoped, which will become a problem for concurrency, reproducibility, and future app surfaces.
- Many failures are flattened into plain strings instead of structured outcomes, which weakens trust and debuggability.
- Test coverage is strongest in unit-level helpers and prompt assembly, but relatively thin in end-to-end retrieval, evaluation, and generation behavior.

## Phase 0: Freeze The Baseline

Before changing architecture, preserve current behavior so improvements and regressions remain measurable.

- Capture benchmark outputs, token counts, wall-clock times, and PI scores for the current ALD corpus.
- Add a small non-ALD regression set so generalization can be measured explicitly.
- Update `README.md` and project docs to distinguish:
  - working product capabilities
  - benchmark-only paths
  - experimental fast paths
- Preserve current benchmark artifacts so metric or prompt changes do not silently rewrite the historical record.

### Deliverables

- Baseline benchmark snapshot
- Generalization smoke-test corpus
- Updated project status and architecture docs

## Phase 1: Separate Core Platform From Benchmark Logic

The current repo mixes three concerns:

- a general academic writing platform
- an ALD/memristor benchmark harness
- an agent tooling playground

These should be separated cleanly.

### Refactor Direction

- Move ALD/memristor-specific assumptions out of generic runtime paths.
- Treat current benchmark strategies and `.claude` skills as profiles or experiments.
- Make generic pipelines receive:
  - topic
  - artifact type
  - field guide
  - journal profile
  - optional benchmark/domain profile
- Keep the ALD benchmark first-class, but make it a configured specialization rather than the default substrate.

### Immediate Targets

- `src/scholarforge/agent/scripted.py`
- `src/scholarforge/agent/fast_generate.py`
- `src/scholarforge/agent/defaults.py`
- `src/scholarforge/cli.py`

## Phase 2: Introduce Run-Scoped State

The system needs an explicit run/session object instead of process-global mutable state.

### Problems Today

- Paper summaries are stored in a module-global list.
- Reading logs are written to a fixed shared file.
- Concept graph behavior is session-like, but not explicitly scoped as a first-class run object.

### Refactor Direction

Introduce a `RunContext` or `SessionContext` that owns:

- run ID
- topic / task metadata
- paper summaries
- reading log
- concept graph state
- token telemetry
- tool-call telemetry
- warnings and failures
- project/output metadata

### Goals

- make runs isolated
- make behavior reproducible
- make concurrent sessions possible
- support app, MCP, CLI, and background workflows without shared mutable collisions

### Immediate Targets

- `src/scholarforge/agent/tools.py`
- `src/scholarforge/agent/reading_log.py`
- `src/scholarforge/agent/core.py`
- `src/scholarforge/agent/workflows.py`

## Phase 3: Define One Canonical Research Pipeline

The system should have one primary research loop and several optional variants, not several competing defaults.

### Canonical Pipeline

1. Gap discovery
2. Hierarchical retrieval
3. Devil's-advocate search
4. Structured research notes
5. Draft generation
6. Deterministic verification
7. PI review
8. Targeted weakest-section rewrite

### Product Principle

- Chat, literature exploration, review generation, and paper drafting should all sit on top of the same core loop.
- One-shot and scripted modes should remain available, but be clearly marked as speed/ablation variants rather than the main architecture.

### Key Architectural Shift

Promote hierarchical progressive disclosure from an experimental strategy to the default retrieval policy.

### Immediate Targets

- `src/scholarforge/agent/workflows.py`
- `src/scholarforge/retrieve/strategies/hierarchical.py`
- `src/scholarforge/retrieve/strategies/query_driven.py`
- `src/scholarforge/generate/writer.py`

## Phase 4: Fix Evaluation Truthfulness

The evaluation layer should reflect what the repo claims to optimize for.

### Current Issue

Prose quality is defined and weighted in the composite score design, but not fully wired through the report/composite flow.

### Refactor Direction

Split evaluation into three explicit layers:

- corpus-grounded metrics
- prose/structure metrics
- LLM-as-PI review

### Required Improvements

- Fix the prose-quality integration bug.
- Make metric presence explicit.
- Do not present a single composite score if required components are missing.
- Version benchmark reports and scoring schemes.
- Add tests for score calculation, missing-metric behavior, and calibration against known artifacts.

### Immediate Targets

- `src/scholarforge/evaluate/quality.py`
- `src/scholarforge/evaluate/pi_review.py`

## Phase 5: Rebuild Prompting And Planning Around Generalization

The prompt/planning system is strong, but it needs cleaner boundaries.

### Refactor Direction

Treat these as orthogonal inputs:

- artifact type
- field guide
- journal profile
- corpus profile
- benchmark profile

### Desired Behavior

- The planner should infer structure from artifact type plus evidence in the corpus.
- The writer should consume structured notes and section goals, not benchmark-specific section labels.
- The style guide should remain strict, but field/domain adaptation should be data-driven rather than hardcoded into runtime paths.

### Immediate Targets

- `src/scholarforge/generate/persona.py`
- `src/scholarforge/generate/planner.py`
- `src/scholarforge/generate/prompts.py`
- `src/scholarforge/generate/field_guide.py`

## Phase 6: Tighten Token Economics

The repo is already thinking carefully about token usage. The next step is to make those tradeoffs formal and consistent across pipelines.

### Refactor Direction

- Make progressive disclosure the default token policy:
  - metadata
  - digest
  - section
  - full text only by exception
- Introduce explicit budget allocation by phase:
  - exploration
  - retrieval
  - note synthesis
  - drafting
  - verification
  - revision
- Track token spend per phase and per run.
- Keep precompute and caching, but judge them by quality-per-token rather than convenience alone.

### Goals

- better quality per token
- less drift toward deep-read-heavy paths
- more honest comparisons across generation modes

### Immediate Targets

- `src/scholarforge/agent/core.py`
- `src/scholarforge/generate/writer.py`
- `src/scholarforge/store/embeddings.py`

## Phase 7: Replace Stringly Error Handling With Structured Outcomes

Today many failures are converted into plain tool strings. That is convenient for prototyping, but weak for a research product.

### Refactor Direction

Tool and workflow boundaries should return structured result types such as:

- `ok`
- `data`
- `warnings`
- `error`
- `metadata`

### Design Goals

- Make failures machine-detectable.
- Let agent policies distinguish:
  - no evidence found
  - retrieval failure
  - malformed cache
  - export failure
  - infrastructure issue
- Abort or retry appropriately when failures are critical.
- Improve observability and debugging.

### Immediate Targets

- `src/scholarforge/agent/tools.py`
- `src/scholarforge/agent/workflows.py`
- `src/scholarforge/ingest/registry.py`
- `src/scholarforge/llm/client.py`

## Phase 8: Expand Test Coverage To Match Real Risk

The test suite is healthy, but it over-indexes on low-risk layers.

### Add Coverage For

- retrieval execution against a live test DB + embeddings store
- evaluation correctness
- prose-quality and composite-score behavior
- generic versus benchmark-profile generation
- token-budget enforcement
- session isolation
- citation resolution correctness
- end-to-end generation contracts

### Also Add

- golden outputs for:
  - good review
  - high-metric but weak-prose review
  - hallucinated or under-cited review
- regression checks on benchmark artifacts

### Goal

Make the riskiest parts of the system the most tested parts of the system.

## Recommended Execution Order

1. Fix evaluation honesty.
2. Remove ALD hardcoding from generic paths.
3. Introduce run-scoped state and eliminate globals.
4. Promote one canonical hierarchical pipeline.
5. Add revision loop and stronger verification.
6. Optimize speed and product surfaces after the above is stable.

## Suggested First Three PRs

### PR 1: `evaluation-honesty`

- Fix prose-quality integration in the evaluation pipeline.
- Make metric presence explicit in reports.
- Add tests for composite-score correctness.
- Update benchmark docs to match real behavior.

### PR 2: `generic-pipeline-boundaries`

- Extract ALD-specific structure from generic generation paths.
- Introduce profile/config hooks for domain- or benchmark-specific section structures.
- Update CLI defaults and labels to distinguish generic from benchmark-specialized flows.

### PR 3: `run-context`

- Introduce a `RunContext`.
- Migrate reading log, paper summaries, and concept-graph session state off globals.
- Thread context through agent workflows and tool execution.

## Success Criteria

The refactor should be considered successful when:

- a non-ALD corpus can produce coherent outputs without code changes
- the reported quality metrics match what is actually computed
- the default generation path uses hierarchical progressive disclosure
- runs are isolated and reproducible
- benchmark-specific logic is clearly separated from generic runtime logic
- token spend is visible and attributable by phase
- the system can explain why an output was good, weak, expensive, or misleading

## Final Principle

ScholarForge should evolve from a strong benchmark-driven POC into a research operating system.

That means the architecture should prioritize:

- trustworthy output quality
- domain generalization
- explicit state and reproducibility
- honest evaluation
- disciplined token economics

Speed optimizations and interface expansion should follow that foundation, not substitute for it.
