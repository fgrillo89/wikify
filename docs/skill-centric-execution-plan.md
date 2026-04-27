# Skill-centric execution plan

**Status: completed.** This document was the planning brief that drove the
Wikify v2 redesign. The redesign has shipped through workstreams W0-W11.
Active reference material now lives in:

- `docs/architecture.md` — final v2 architecture
- `docs/filesystem-state-design.md` — final filesystem and CLI surface
- `AGENTS.md` — agent contract
- `tasks/skill-centric-redesign-plan.md` — the implementation plan as it
  was executed
- `.claude/skills/wikify/references/` — agent-facing reference (schemas,
  CLI grammar, citation format, write constraints, tiers, escalation,
  knowledge-graph, wiki-graph)

The sections below are kept as **historical** context for why the
redesign was structured the way it was. Treat them as design rationale,
not agent instructions.

---

## Historical: brief

This document was the planning brief for the Wikify redesign. It was the
contract that drove production of the implementation plan. Any agent
producing that plan had to follow it.

### Goal (historical)

Produce an implementation plan for the full skill-centric Wikify redesign
with PR-sized slices, disjoint ownership, and a phased legacy-removal
target.

### Architectural decisions (load-bearing — frozen by this brief)

1. **Skills own strategy. Python is deterministic only.** No Python
   strategy controllers. No model SDK calls in Python. The CLI exposes
   control surfaces; strategy is composed in skills from CLI atoms.

   Skills split into two kinds, both implemented as canonical Claude
   skills (each is its own directory under `.claude/skills/<skill-name>/`
   with a `SKILL.md`):

   - **Atomic skills**: single-purpose, reusable building blocks with
     declared inputs/outputs (e.g. `wikify-extract-concepts`,
     `wikify-gather-evidence`, `wikify-write-page`, `wikify-refine-page`,
     `wikify-consolidate-inbox`, `wikify-answer-from-wiki`,
     `wikify-tend`). Each atomic skill runs in a forked subagent
     (`context: fork`), composes CLI atoms via Bash, and loads the
     prompt assets it needs from its own or shared reference material.
   - **Workflow skills**: compositions of atomic skills that encode a
     strategy (`wikify-baseline` for the focus workflow; deferred
     stubs for guided/free/query/ingest/maintain). Workflow skills hold
     loop shape, stopping criteria, and parallel-agent dispatch. They
     contain no model-call logic of their own.

2. **CLI is the agent's only normal interface to bundle state.**
   `list` = ls; `find --text` = rg; `find` (no flag) = semantic/graph
   retrieval; `show` = cat with parsed text/YAML views. Direct shell
   `ls/rg/cat` is for debugging only.

3. **Query is a skill, not a CLI noun.** Python exposes only
   deterministic feedback verbs (`work add feedback query`,
   `work list inbox query_feedback`). The answering loop lives in a
   workflow skill.

4. **Legacy removal is the explicit target, executed in named phases.**
   No permanent compatibility aliases. Each phase ended with concrete
   file deletions.

5. **Existing on-disk data is preserved.** The redesign does not
   delete or rewrite `data/wikis/*` or `data/corpora/*` produced under
   the older layout. Such directories are no longer an execution
   surface; the current pipeline only reads bundles that match the
   `run/state.json` marker rule (`Bundle.open`).

6. **Parallelism is explicit.** Multiple agents may operate on one
   bundle. The plan specifies the lock contract, per-concept claim
   semantics, claim TTL, and CLI verbs (`work claim`, `work release`,
   `work list claims`). Exit codes for contention are documented in the
   architecture doc.

7. **Test focus is deterministic Python only**: CLI behavior, JSON/JSONL
   schema validation, fluent API, store-layer mutations, lock/claim
   atomicity, path resolution. Skills and agent loops are not in the
   unit-test scope. Integration tests exercise the CLI surface
   end-to-end.

8. **Reuse over rewrite.** Prompts, validators, fluent KGs, ingest
   pipeline, metric math, and renderer templates were preserved.

### Workstreams W0-W11 (historical)

The brief produced an implementation plan with twelve workstreams, a
four-phase legacy-removal sequence, and three lead PRs. All workstreams
have shipped; legacy execution surfaces have been deleted; the skill
hybrid layout is in place.

| ws | scope | status |
|---|---|---|
| W0 | mechanical package skeleton (file moves only) | shipped |
| W1 | `api.py` (Bundle/Corpus/LegacyBundle); `cli/migrate.py` | shipped |
| W2 | `bundle/run/` + `cli/run.py` + `cli/_io.py` | shipped |
| W3 | `corpus/queries.py` + `cli/corpus.py` | shipped |
| W4 | `bundle/work/` + `cli/work.py` | shipped |
| W5 | `bundle/draft/` + `cli/draft.py` | shipped |
| W6 | `bundle/wiki/{commit,derived}.py` + `cli/wiki.py` | shipped |
| W7 | `cli/render.py` rewired to v2 paths | shipped |
| W8 | `cli/eval.py` reads `events.jsonl` | shipped |
| W9 | hybrid skill layout under `.claude/skills/` | shipped |
| W10 | legacy retire (CLI nouns, baselines, citations debug entry) | shipped |
| W11 | adapter collapse + doc rewrites | shipped (this PR) |

### Final state (canonical references)

For the final answer to "what does the system look like?" read:

- `docs/architecture.md` — seven CLI nouns, package layout, telemetry
  contract, citation grounding, design invariants.
- `docs/filesystem-state-design.md` — full filesystem contract and
  agent-facing CLI grammar.
- `AGENTS.md` — agent contract, runtime model, error/exit codes.
- `tasks/skill-centric-redesign-plan.md` — the implementation plan that
  was executed, including the preservation inventory.
