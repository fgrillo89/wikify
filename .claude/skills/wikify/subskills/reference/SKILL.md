---
name: reference
description: Shared Wikify reference material for Claude Code skills. Use when working on Wikify skills, workflows, prompts, bundle state, writing rules, schemas, citation grounding, CLI grammar, exploration patterns, or MCP setup.
allowed-tools: Bash(wikify *)
---

# Wikify Reference

Shared reference material for the Wikify skills. This is not a workflow:
it carries no executable steps and decides no strategy. It points the
build, query, acquisition, and ingest skills at durable project facts —
bundle layout, CLI grammar, writing rules and schemas, exploration
patterns, maturity scoring, and MCP setup.

## Canonical skill tree

`.claude/skills/` is the canonical Wikify skill tree. Do not maintain
parallel skill trees by hand; generate compatibility exports from this
tree when another runtime needs them.

Four skills are first-class entry points; everything else is a bundled
subskill under `wikify/subskills/`, composed by an entry point and never
invoked directly by a user.

Entry points:

- `wikify` — primary builder. Researcher-style iterative loop: an editor
  orchestrator dispatches `explore` Tasks running one of five named
  recursive patterns (P1-P5), gathers evidence into per-slug notebooks,
  and writes pages once a composite maturity score passes a gate.
- `query` — answer from the committed wiki with corpus fallback and
  bundle feedback.
- `arxiv` — acquire arXiv papers for a topic and stage them for ingest.
- `ingest` — parse local documents (PDF / DOCX / PPTX / HTML) into a
  corpus.

Build subskills (under `wikify/subskills/`):

- `explore` — the recursive corpus pattern library (P1-P5).
- `gather-evidence` — vet candidate chunks into evidence ledgers.
- `write-page` — produce `WriteResponse` page prose from supplied
  context and evidence.
- `organize-wiki` — create the validated topic hierarchy consumed by
  the renderer.
- `extract-data` — harvest verifiable numbers/tables into the claim
  store via the verification gate.
- `consolidate-data` — build and commit `kind=data` artifact tables from
  the claim store.
- `search-corpus` — read/search the corpus CLI surface.
- `search-wiki` — read/search committed wiki pages.
- `bundle` — inspect and mutate bundle state mechanically.
- `build-simple` — the simplified conventional-RAG builder, NOT the main
  path.
- `refine` — repair committed pages from inbox/new evidence (committed
  pages are repaired only here, never by hand-edits).

## Reference index

Bundle and state:

- `references/bundle/layout.md` — bundle directory layout and which dirs
  are canonical vs rebuildable.
- `references/bundle/state.md` — the state surfaces under `run/` and
  `work/`.
- `references/bundle/events-ledger.md` — `run/events.jsonl` event types
  and the round contract.
- `references/bundle/locking-and-claims.md` — the lock + claim
  coordination layers.

CLI:

- `references/cli/grammar.md` — the workflow nouns and the stable
  file/state grammar.
- `references/cli/output-contract.md` — terse-text default and
  `--format json` shape.
- `references/cli/exit-codes.md` — process exit-code meanings.

Writing:

- `references/writing/schemas.md` — model-facing artifact schemas
  (`WriteRequest` / `WriteResponse` / `EvidenceRecord` / inbox records).
- `references/writing/citation-format.md` — `[^eN]` evidence-marker
  grounding rules.
- `references/writing/write-constraints.md` — encyclopedia-page
  constraints.
- `references/writing/tiers.md` — model-tier vocabulary (S / M / L).
- `references/writing/escalation.md` — writer validator-retry tier
  escalation.
- `references/writing/field-guides/generic.md` — always-loaded writing
  field guide.
- `references/writing/field-guides/<field>.md` — one matching field
  guide (`biology`, `computer-science`, `materials-science`,
  `mathematics`, `medicine`, `physics`, `social-sciences`).

Exploration:

- `references/exploration/concept-extraction.md` — turning corpus text
  into candidate pages.
- `references/exploration/sampling-patterns.md` — sampling building
  blocks.
- `references/exploration/patterns.md` — the P1-P5 recursive procedures.
- `references/exploration/maturity.md` — the composite maturity score,
  bands, gates, and the growth-stall timer.
- `references/exploration/workflow-contracts.md` — the round, explorer,
  gather, and dispatch contracts.

MCP:

- `references/mcp/setup.md` — register and run the stdio MCP server.
- `references/mcp/tool-map.md` — MCP tools and their CLI equivalents.
- `references/mcp/resources.md` — the resource read path for full
  objects.
- `references/mcp/fallback.md` — CLI fallback when MCP is not
  configured.

## Field guide rule

Writers always load the generic field guide. If corpus metadata,
workflow state, or field detection identifies one field with confidence,
load exactly one matching field guide in addition to generic. Do not
load all field guides.
