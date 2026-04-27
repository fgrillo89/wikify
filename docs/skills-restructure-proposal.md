# Wikify Skill Restructure Proposal

## Purpose

Make Wikify skills simple, readable, and composable.

The target shape is:

```text
small job skills -> workflow skills -> benchmarkable strategies
```

A workflow such as the baseline should read like:

```text
extract concepts
  -> find evidence
  -> write pages in parallel
  -> consolidate work
  -> render and evaluate
```

Each step should be a named skill with explicit inputs, outputs,
preconditions, and failure behavior. Strategy choices such as budget,
model tier, retry policy, loop shape, and parallelism stay in workflow
skills, not Python.

## External Patterns Checked

Sources reviewed:

- Claude skill authoring best practices:
  <https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices>
- Agent Skills specification overview:
  <https://www.mintlify.com/anthropics/skills/spec/overview>
- Codex skills docs:
  <https://developers.openai.com/codex/skills>
- Codex plugins docs:
  <https://developers.openai.com/codex/plugins>
- Anthropic public skills repository:
  <https://github.com/anthropics/skills>
- OpenAI public skills repository:
  <https://github.com/openai/skills>

Example repositories were downloaded locally for inspection under:

```text
.tmp/skill-examples/anthropics-skills-full
.tmp/skill-examples/openai-skills-full
```

## Key Best Practices

1. Keep each skill focused on one job.
2. Put all routing information in the `description` field.
3. Keep `SKILL.md` concise and move detail into one-level references.
4. Write imperative steps with explicit inputs and outputs.
5. Use scripts for deterministic or repetitive mechanics.
6. Treat helper scripts as black boxes unless debugging.
7. Use workflow skills for orchestration, strategy, budgets,
   parallelism, and retries.
8. Test trigger descriptions against realistic prompts.
9. For Codex, use `.agents/skills` for repo-scoped skills.
10. Use plugins when distributing a bundle of reusable skills.

## Good Example Features

### Anthropic PDF Skill

Path inspected:

```text
.tmp/skill-examples/anthropics-skills-full/skills/pdf
```

Useful features:

- Broad but coherent domain skill.
- Short trigger metadata.
- Detailed references split into `reference.md` and `forms.md`.
- Deterministic helper scripts under `scripts/`.
- Clear distinction between quick-start guidance and advanced details.

### Anthropic Webapp Testing Skill

Path inspected:

```text
.tmp/skill-examples/anthropics-skills-full/skills/webapp-testing
```

Useful features:

- Decision tree for choosing an approach.
- Explicit workflow pattern: inspect, identify selectors, act.
- Helper script guidance says to run `--help` first and avoid reading
  large scripts unless necessary.
- Practical guardrail around dynamic DOM loading.

### OpenAI Playwright Skill

Path inspected:

```text
.tmp/skill-examples/openai-skills-full/skills/.curated/playwright
```

Useful features:

- CLI-first skill with a small wrapper script.
- Clear prerequisites.
- References split into `references/cli.md` and
  `references/workflows.md`.
- `agents/openai.yaml` provides Codex UI/default prompt metadata.
- Guardrails are concrete and operational.

### OpenAI GitHub Address Comments Skill

Path inspected:

```text
.tmp/skill-examples/openai-skills-full/skills/.curated/gh-address-comments
```

Useful features:

- Narrow job skill.
- One helper script for deterministic comment fetching.
- Human clarification point is explicit.
- The skill body is short enough to scan quickly.

### OpenAI Notion Spec-To-Implementation Skill

Path inspected:

```text
.tmp/skill-examples/openai-skills-full/skills/.curated/notion-spec-to-implementation
```

Useful features:

- Workflow skill over external tools.
- Steps compose smaller operations: locate, parse, plan, create tasks,
  link artifacts, track progress.
- Detailed templates live in references/examples instead of bloating
  the main skill body.

## Current Wikify Skill Issues

### Validation Failures

Several current skill files fail Codex skill validation.

Affected examples:

- `.claude/skills/wikify-baseline/SKILL.md`
- `.claude/skills/wikify-guided-explore/SKILL.md`
- `.claude/skills/wikify-ingest/SKILL.md`
- `.claude/skills/wikify-maintain/SKILL.md`
- `.claude/skills/wikify-query/SKILL.md`
- `.claude/skills/wikify-refine/SKILL.md`
- `.claude/skills/wikify-render-eval/SKILL.md`
- `.claude/skills/wikify-render/SKILL.md`

Causes:

- Unquoted YAML descriptions contain `Status: ...`, which is parsed as
  a mapping.
- `wikify-render` uses angle-bracket placeholders such as `<bundle>` in
  the description, which Codex validation rejects.

### Skill Location Mismatch

Wikify skills currently live under:

```text
.claude/skills/
```

Codex repo-scoped skills are discovered from:

```text
.agents/skills/
```

For Codex compatibility, either mirror or move the active skill pack to
`.agents/skills`. If Claude compatibility is still required, use a
checked-in compatibility mirror or plugin metadata instead of letting
the two trees drift.

### Atomic Layer Is Too CLI-Noun Oriented

Current "atomic" skills map mostly to CLI nouns:

```text
wikify-corpus
wikify-run
wikify-work
wikify-draft
wikify-wiki
wikify-render
wikify-eval
```

Those are useful as command references, but they are not the same as
composable user jobs. A workflow wants to compose jobs such as
`extract concepts`, `find evidence`, `write page`, `commit page`, and
`consolidate work`, not nouns such as `work` and `draft`.

### Stubs Are Discoverable

Several workflow skills are stubs but still appear as skills:

```text
wikify-guided-explore
wikify-ingest
wikify-maintain
wikify-query
wikify-refine
wikify-render-eval
```

This creates accidental trigger risk. Unfinished workflows should either
be disabled for implicit invocation or moved into references until they
are executable.

### Claude-Specific Frontmatter

Some skills include fields such as:

```yaml
allowed-tools: Bash(wikify *) Task
```

The Codex skill creator guidance expects only `name` and `description`
in frontmatter, with optional Codex metadata in `agents/openai.yaml`.
If these skills need to be portable across Claude and Codex, keep the
portable skill surface clean and put runtime-specific policy in the
runtime-specific metadata layer.

### Missing Codex Metadata

No current Wikify skills have:

```text
agents/openai.yaml
```

This is optional, but useful for readable skill selectors, default
prompts, invocation policy, and dependency/tool declarations.

### Baseline Has Inline Gaps

The baseline contains inline placeholders such as:

```text
| <jsonl writer> > /tmp/ev.jsonl
```

That should become a job-skill contract. The workflow should call
`wikify-find-evidence` and receive a known evidence JSONL artifact,
rather than describing an ad hoc conversion inside the baseline loop.

### Inbox Naming Drift

The docs and skills currently disagree on inbox names:

```text
query_feedback.jsonl
query_feedback_suggestions.jsonl
```

The restructure should choose one canonical inbox file grammar and
update docs, references, skills, and tests together.

## Proposed Structure

Use `.agents/skills` as the primary Codex-discoverable tree:

```text
.agents/skills/
  wikify/
    SKILL.md
    references/
      bundle-layout.md
      bundle-state.md
      cli-grammar.md
      schemas.md
      citation-format.md
      write-constraints.md
      tiers.md
      escalation.md
      concept-extraction.md
      exploration-patterns.md
      workflow-contracts.md

  wikify-search-corpus/
    SKILL.md
    references/
      corpus-cli-patterns.md
      corpus-recursive-search.md
      corpus-graph-traversals.md

  wikify-search-wiki/
    SKILL.md
    references/
      wiki-cli-patterns.md
      wiki-recursive-search.md
      wiki-corpus-bridges.md

  wikify-write-page/
    SKILL.md
    references/
      writer-response.md
      article-style.md
      person-style.md
      refinement-style.md

  wikify-bundle/
    SKILL.md
    references/
      run-lifecycle.md
      work-state.md
      claims-and-locks.md
      draft-validation.md
      commit-and-projections.md
      render-and-eval.md
      failure-handling.md

  wikify-baseline/
    SKILL.md

  wikify-guided-explore/
    SKILL.md

  wikify-query/
    SKILL.md

  wikify-refine/
    SKILL.md
```

Keep `.claude/skills` only if Claude Code still needs that tree. If so,
make it a generated mirror or a thin compatibility package.

## Core Skills

### `wikify-search-corpus`

Purpose:

- Explain the corpus CLI as the agent-facing surface over the corpus
  fluent API.
- Cover all read/query functions without deciding an exploration
  strategy.

Scope:

- List corpus objects: docs, chunks, authors, figures, equations,
  files.
- Show handles: documents, chunks, source markdown, figures, equations.
- Run semantic retrieval for topics, concepts, and evidence needs.
- Run exact text search for phrases, acronyms, equations, section names,
  and citation labels.
- Discover seed documents through PageRank or other exposed centrality
  queries.
- Use graph traversal affordances exposed through CLI flags or handles:
  citations, cited-by, neighbors, authors, coauthors, section-scoped
  chunks, nearby figures, nearby equations, and source neighborhoods.
- Explain recursive search patterns for graph traversal.

Does not do:

- Decide which documents to explore next.
- Decide how many concepts to extract.
- Mutate a bundle.
- Write pages.

Reference files:

- `corpus-cli-patterns.md`: complete command grammar and common command
  shapes.
- `corpus-recursive-search.md`: inspect-result-pick-handle-traverse
  loops.
- `corpus-graph-traversals.md`: graph traversal examples over the CLI
  surface.

Example recursive search pattern:

```text
1. `wikify corpus find "atomic layer deposition" --top-k 8`
2. Inspect returned chunk and doc handles.
3. `wikify corpus show chunk:<id> --full` for one or two promising hits.
4. Traverse outward, for example cited-by or neighboring-source queries.
5. Inspect the narrowed result set.
6. Pull full text only after choosing a handle.
```

### `wikify-search-wiki`

Purpose:

- Explain the committed wiki CLI as the agent-facing surface over the
  wiki fluent API.
- Cover all committed-wiki read/query functions without deciding a
  refinement or exploration strategy.

Scope:

- List committed pages and wiki files.
- Show pages, page bodies, frontmatter, evidence, links, and backlinks.
- Run semantic page search.
- Run exact text search over titles, aliases, frontmatter, and body
  prose.
- Inspect graph-derived relationships such as links, linked-by,
  co-evidence, overlaps, thin pages, and orphan pages when exposed by
  the CLI.
- Bridge from wiki results back to corpus evidence or source documents.
- Explain recursive wiki search patterns.

Does not do:

- Decide whether a page is ready to refine.
- Append query feedback.
- Mutate work state.
- Write or commit pages.

Reference files:

- `wiki-cli-patterns.md`: complete command grammar and common command
  shapes.
- `wiki-recursive-search.md`: wiki graph traversal loops.
- `wiki-corpus-bridges.md`: patterns for moving from committed wiki
  evidence back to corpus search.

Example recursive search pattern:

```text
1. `wikify wiki find "ALD vs CVD" --top-k 5`
2. `wikify wiki show "<page>" --full`
3. Inspect links, evidence docs, and missing coverage.
4. Search related pages or co-evidence pages.
5. Use page titles or cited evidence docs as corpus search probes.
```

### `wikify-write-page`

Purpose:

- Explain and execute the page-writing contract.
- Given supplied text, evidence, and context, produce a valid
  `WriteResponse` in a selected style.

Inputs:

- A `WriteRequest` or equivalent workflow-provided page context.
- Evidence entries with chunk ids, doc ids, quotes, and chunk text.
- Requested page kind and style.

Outputs:

- `work/concepts/<slug>/response.json`.
- `call` event with token and cost metadata.

Style/reference coverage:

- Encyclopedic article pages.
- Person pages.
- Comparison pages.
- Refinement/update pages.
- Citation marker and reference-definition grammar.
- Quote grounding constraints.
- Links field rules.
- Banned phrases and style constraints.

Does not do:

- Commit directly to `wiki/`.
- Bypass citation or structure validation.
- Decide which concepts deserve pages.
- Decide how much evidence is enough.

Reference files:

- `writer-response.md`: exact `WriteResponse` contract.
- `article-style.md`: article-page voice and structure.
- `person-style.md`: person-page voice and metadata use.
- `refinement-style.md`: how to update an existing page from new
  evidence.

### `wikify-bundle`

Purpose:

- Explain the mechanical operations for interacting with a Wikify
  bundle.
- Cover state inspection, mutation commands, claims, draft validation,
  commit gates, projections, rendering, evaluation, and failure codes.
- Keep strategy outside this skill.

Scope:

- Run lifecycle: init, show, list events, set small state fields, close.
- Work state: list/show concepts, add concepts, add evidence, add
  feedback, set status, tend.
- Claims and locks: claim, release, contention, TTL, stale claim
  behavior.
- Draft operations: build, show, check.
- Commit operations: wiki list/find/show/build/check/commit.
- Projection operations: indexes, graph, vectors.
- Downstream artifacts: render and eval.
- Exit codes and recovery guidance.

Does not do:

- Decide exploration order.
- Decide readiness thresholds.
- Decide model tier, budget, retry count, or writer concurrency.
- Decide whether to refine, expand, or stop.

Reference files:

- `run-lifecycle.md`: run state and event ledger operations.
- `work-state.md`: concept cards, evidence, inbox, statuses, tend.
- `claims-and-locks.md`: run locks and per-concept claims.
- `draft-validation.md`: draft build/check mechanics.
- `commit-and-projections.md`: commit gate and derived projections.
- `render-and-eval.md`: static site and metric artifacts.
- `failure-handling.md`: exit code matrix and recovery moves.

## Shared References

References under `wikify/references/` are not strategy skills. They are
durable facts and prompt material that multiple skills or workflows can
load on demand.

### `bundle-layout.md`

Documents:

- `run/`, `work/`, `wiki/`, and `derived/`.
- Canonical versus transient versus rebuildable artifacts.
- Bundle roots and path resolution.

### `bundle-state.md`

Documents:

- Concept status vocabulary.
- Run status vocabulary.
- Stage/status updates.
- Relationship between `run/state.json`, `work/index.md`, and
  committed pages.

### `cli-grammar.md`

Documents:

- Current supported CLI grammar, derived from actual `--help` output.
- The seven nouns and their verbs.
- Default text output versus `--format json`.

### `schemas.md`

Documents:

- Durable artifact schemas.
- `WriteRequest` and `WriteResponse`.
- Evidence records.
- Inbox records.
- Event envelopes.

### `citation-format.md`

Documents:

- `[^eN]` marker grammar.
- Reference definition grammar.
- Verbatim quote substring rule.
- Validator expectations.

### `write-constraints.md`

Documents:

- Wikipedia voice.
- Article and person-page constraints.
- Link-field rules.
- Banned phrases.

### `tiers.md`

Documents:

- Tier vocabulary.
- Cost mapping.
- How workflows should declare model tier explicitly.

### `escalation.md`

Documents:

- When to retry.
- When to escalate.
- How to record escalation.

### `concept-extraction.md`

Documents:

- Concept candidate criteria.
- Person candidate criteria.
- Title normalization.
- Alias extraction.
- Merge/split judgment.
- Confidence/rationale schema.

This is prompt/reference material, not a strategy. Workflows decide what
text to feed into concept extraction.

### `exploration-patterns.md`

Documents named sampling patterns without making any one pattern the
default strategy.

Suggested pattern entries:

- `pagerank-entrypoint`: start from central documents.
- `abstract-first`: inspect abstracts, introductions, and conclusions
  before full documents.
- `citation-neighborhood`: traverse cited/citing papers from seeds.
- `topic-vocabulary`: use ingest topics or author keywords as probes.
- `wiki-gap-driven`: start from thin/orphan/low-evidence committed
  pages.
- `query-driven`: use user questions to reveal missing coverage.
- `coverage-residual`: explore corpus chunks far from current wiki page
  embeddings.
- `author-network`: discover people and communities from bibliography
  and coauthor graph.

Each pattern should include:

```text
Intent
Useful when
CLI affordances used
Typical recursive loop
Signals to stop
Failure modes
```

### `workflow-contracts.md`

Documents:

- What workflow skills own.
- What primitive skills must not decide.
- How workflow skills compose search, bundle, and writer skills.
- Required telemetry/cost discipline for strategy comparison.

## Proposed Workflow Skills

Workflow skills own strategy. They choose exploration patterns, stop
conditions, budget, model tiers, readiness thresholds, retry policy, and
parallelism. They compose `wikify-search-corpus`, `wikify-search-wiki`,
`wikify-bundle`, `wikify-write-page`, and shared references.

### `wikify-baseline`

Baseline workflow:

```text
1. Use `wikify-bundle` to initialize or open a bundle.
2. Use `wikify-search-corpus` to select seed material according to the
   baseline strategy.
3. Use `concept-extraction.md` to extract concepts from selected text.
4. Use `wikify-bundle` to add accepted concepts.
5. Use `wikify-search-corpus` to gather evidence for selected concepts.
6. Use `wikify-bundle` to append evidence and claim write targets.
7. In parallel, use `wikify-write-page` to produce responses.
8. Use `wikify-bundle` to validate, commit, tend, refresh projections,
   render, eval, and close.
```

Strategy owned here:

- Seed count.
- Seed ranking weights.
- Evidence top-k.
- Writer concurrency.
- Model tier and model id.
- Retry and escalation policy.
- Stop conditions.

### `wikify-pagerank-explore`

Example strategy workflow:

```text
1. Start from top PageRank documents.
2. Read selected abstracts or full documents.
3. Extract concepts from observed text.
4. Add concepts to the bundle.
5. Decide which concepts are evidence-ready or write-ready.
6. Spawn page writers in parallel for ready concepts.
7. Inspect the bundle and choose the next exploration action.
8. Continue until budget or coverage stop condition.
```

Strategy owned here:

- How many PageRank documents to read.
- Whether to read abstracts, sections, or full documents.
- Concept acceptance threshold.
- Readiness threshold.
- Next-action policy.

### `wikify-guided-explore`

Guided workflow:

```text
read work dashboard
  -> choose next action
  -> extract concept OR find evidence OR write ready page
  -> consolidate periodically
  -> stop on budget or coverage criterion
```

Strategy owned here:

- Exploration rubric.
- Thin versus ready thresholds.
- Per-iteration budget.
- Breadth/depth tradeoff.

### `wikify-query`

Query workflow:

```text
search committed wiki
  -> answer if sufficient
  -> fall back to corpus retrieval if needed
  -> append query feedback
```

Strategy owned here:

- Wiki sufficiency rubric.
- Corpus fallback policy.
- Feedback severity.

### `wikify-refine`

Refine workflow:

```text
consolidate inbox
  -> identify `needs_refine` concepts
  -> find additional evidence if needed
  -> build refine draft
  -> write, validate, commit replacement page
```

Strategy owned here:

- Refinement threshold.
- Evidence growth policy.
- Retry/escalation policy.

## Skill Template

Each core skill should use this shape:

```markdown
---
name: wikify-example-core-skill
description: Explains one Wikify capability surface. Use when ...
---

# Wikify Example Core Skill

## Purpose

- ...

## Capability Surface

- ...

## Recursive Patterns

1. ...
2. ...

## Does Not Do

- ...

## References

- `../wikify/references/...`
```

Workflow skills should use this shape:

```markdown
---
name: wikify-example-workflow
description: Runs a Wikify strategy by composing core skills. Use when ...
---

# Wikify Example Workflow

## Strategy

- ...

## Prerequisites

- ...

## Workflow

1. `wikify-first-job`
2. `wikify-second-job`

## Parallelism

- ...

## Retry And Escalation

- ...

## Stop Conditions

- ...

## Does Not Do

- ...
```

## Migration Plan

### Phase 1: Make Current Skills Valid

- Quote descriptions containing colons.
- Remove angle brackets from descriptions.
- Remove or relocate unsupported frontmatter fields for Codex.
- Add `agents/openai.yaml` for active skills.
- Disable implicit invocation for stubs, or move stubs to references.

### Phase 2: Create The Core Capability Skills

- Move current noun skills into `wikify-bundle` references or shared
  `wikify/references/cli-grammar.md`.
- Create `wikify-search-corpus`, `wikify-search-wiki`,
  `wikify-write-page`, and `wikify-bundle`.
- Ensure each core skill clearly states its capability surface and what
  strategy decisions it does not make.

### Phase 3: Rewrite Baseline As Composition

- Replace inline command sequence with use of the four core skills.
- Move evidence JSONL conversion into `wikify-bundle` or the actual CLI
  output contract; do not leave it as an inline placeholder.
- Move writer response contract into `wikify-write-page`.
- Keep budget, model tier, concurrency, and retry policy in
  `wikify-baseline`.

### Phase 4: Rebuild Secondary Workflows

- Implement `wikify-query`.
- Implement `wikify-refine`.
- Implement `wikify-guided-explore`.
- Keep incomplete workflows disabled until executable.

### Phase 5: Package

- Make `.agents/skills` the primary tree for Codex.
- Decide whether `.claude/skills` remains a mirror.
- If this skill pack should be shared across repos, package it as a
  plugin.

## Open Decisions

1. Should `.claude/skills` be replaced by `.agents/skills`, mirrored, or
   retained as Claude-only compatibility?
2. Should the existing CLI noun skills become references only, or should
   `wikify-bundle` preserve some noun-oriented quick reference sections?
3. What is the canonical inbox file grammar:
   `query_feedback.jsonl` or `query_feedback_suggestions.jsonl`?
4. Should `wikify-write-page` always be invoked as a writer subagent, or
   can a workflow inline the writing step while still following the same
   contract?
5. Should unfinished workflow skills be disabled via
   `agents/openai.yaml`, moved under references, or deleted until ready?
6. Which workflows need Codex UI metadata and default prompts first?

## Recommended Next Step

Start with Phase 1 plus the baseline split:

1. Fix validation failures.
2. Create `.agents/skills/wikify-search-corpus`,
   `.agents/skills/wikify-search-wiki`,
   `.agents/skills/wikify-write-page`, and
   `.agents/skills/wikify-bundle`.
3. Rewrite `wikify-baseline` to compose those core skills.
4. Leave secondary workflow stubs disabled until they are executable.
