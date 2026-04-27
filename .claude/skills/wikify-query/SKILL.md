---
name: wikify-query
description: Answer a question against a committed Wikify wiki, with corpus fallback, and append `query_feedback` to the inbox so a later refine can act on gaps. Status: stub — composition shape only, no Python orchestration.
allowed-tools: Bash(wikify *) Skill(wikify-*) Task
---

# wikify-query (stub)

Status: stub — composition shape only. No Python orchestration; the
agent runs primitives in order.

## Intent

Answer a query against an already-committed wiki by composing the
read-only verbs from `wikify-wiki` and `wikify-corpus`, and recording
any gap as a `query_feedback` record on the work inbox.

## Composition

1. `wikify-wiki` — `wiki find "<query>"` then `wiki show <handle>
   --full` for the top hits. If sufficient, synthesise an answer.
2. `wikify-corpus` — fallback: `corpus find "<query>"` and
   `corpus show chunk:<id> --full` for any uncovered facts.
3. `wikify-work` — `work add feedback query --record <gap.json>` for
   every uncovered claim, so a later `wikify-refine` (or
   `wikify-baseline` re-extract) can act on it.

## Strategy

- Synthesiser tier defaults are decided here in skill markdown when
  this stub is filled out, never in Python.
- The CLI fallback policy (when to give up on the wiki and go to
  the corpus) is part of the skill.
- No new CLI commands are introduced. If a verb does not exist, this
  workflow does not paper over it.

## References

- [atoms.md](../wikify/references/atoms.md)
- [cli-tool-surface.md](../wikify/references/cli-tool-surface.md)
- [wiki-graph.md](../wikify/references/wiki-graph.md)
