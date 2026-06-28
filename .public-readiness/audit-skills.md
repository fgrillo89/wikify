# Skill Inventory & Naming Audit

Scope: every skill under `.claude/skills/`. For each skill: purpose, callers
(grepped cross-references), and classification as a **first-class entry point**
(a human invokes it) or a **subskill** (only composed by a workflow). Opaque
names are flagged with rename proposals at the end.

Date: 2026-06-28. Branch: `wikify-public-readiness-prep`.

## Call graph (who composes whom)

```
wikify (shared reference; not callable as a step) ── referenced by ALL via ../wikify/references/*

ENTRY POINTS
  wikify-arxiv ─────────────► (corpus build handoff; no skill children)
  wikify-baseline ──────────► gather-evidence-cluster, write-page, organize-wiki, search-corpus, bundle
  wikify-investigate ───────► investigate-explore, gather-evidence-cluster, write-page,
                              extract-data, consolidate-data, organize-wiki,
                              search-corpus, search-wiki, bundle      [TRUE MAIN ENTRY]
  wikify-query ─────────────► search-wiki, search-corpus, bundle
  wikify-refine ────────────► bundle, search-corpus, organize-wiki, write-page

SUBSKILLS
  wikify-investigate-explore ─► gather-evidence-cluster, search-corpus, bundle   (only caller: investigate)
  wikify-gather-evidence-cluster ─► search-corpus, bundle   (callers: baseline, investigate-explore)
  wikify-write-page          (callers: baseline, investigate, refine)
  wikify-extract-data        (caller: investigate)
  wikify-consolidate-data    (caller: investigate)
  wikify-organize-wiki       (callers: baseline, investigate, refine)
  wikify-search-corpus       (callers: investigate, investigate-explore, refine, query, gather-evidence-cluster)
  wikify-search-wiki         (callers: investigate, query)
  wikify-bundle              (callers: every workflow + the two evidence/explore subskills)
```

## Inventory

| Skill | One-line purpose | Called by (grep) | Class |
|---|---|---|---|
| `wikify` | Shared reference material (schemas, CLI grammar, writing rules, exploration patterns). Not a workflow. | All skills via `../wikify/references/*` | Reference (not an entry point, not a step) |
| `wikify-arxiv` | Scout arXiv categories, harvest metadata, download PDFs, stage for `corpus build`. | User only | Entry point (acquisition) |
| `wikify-baseline` | End-to-end wiki build from corpus to rendered HTML using simple/conventional RAG (scripted strategy). | User only | Entry point (build) |
| `wikify-investigate` | Researcher-style iterative wiki build; editor orchestrator dispatches explorer/writer/data subagents; chunk coverage is the objective (guided strategy). **The real primary build entry point.** | User only | Entry point (build) — primary |
| `wikify-query` | Answer a question from the committed wiki, fall back to corpus search, emit refine feedback. | User only | Entry point (read) |
| `wikify-refine` | Apply inbox feedback, re-gather evidence, rewrite + recommit pages. | User only | Entry point (maintenance) |
| `wikify-investigate-explore` | Library of five depth-bounded recursive exploration patterns (P1-P5) run one-per-Task. | `wikify-investigate` | Subskill |
| `wikify-gather-evidence-cluster` | Sonnet supervisor + haiku chunk-judges fan-out; commits one evidence ledger per slug. | `wikify-baseline`, `wikify-investigate-explore` | Subskill |
| `wikify-write-page` | Produce a `WriteResponse` (article/person/comparison/refinement prose) from dossier + evidence. | `wikify-baseline`, `wikify-investigate`, `wikify-refine` | Subskill (core capability) |
| `wikify-extract-data` | Harvest verifiable numeric/factual data points (with grounding quotes) into the claim store. | `wikify-investigate` | Subskill |
| `wikify-consolidate-data` | Turn the claim store into an evolving `kind=data` comparison-table page (materialized view). | `wikify-investigate` | Subskill |
| `wikify-organize-wiki` | Build a validated topic navigation hierarchy before render. | `wikify-baseline`, `wikify-investigate`, `wikify-refine` | Subskill (core capability) |
| `wikify-search-corpus` | Read/search surface over the corpus (MCP primary, bash CLI fallback). Read-only; no strategy. | `wikify-investigate`, `-explore`, `-refine`, `-query`, `-gather-evidence-cluster` | Subskill (core capability) |
| `wikify-search-wiki` | Read/search surface over the committed wiki (MCP primary, bash fallback). | `wikify-investigate`, `wikify-query` | Subskill (core capability) |
| `wikify-bundle` | Mechanical bundle/run state ops: init, work, evidence, claims, draft, validate, commit, render, eval. | Every workflow + `-gather-evidence-cluster`, `-investigate-explore` | Subskill (core capability) |

## Opaque-name flags & rename proposals

The two flagged names are the highest-impact problem for a public audience: a
newcomer cannot tell that the two build pipelines are `baseline` and
`investigate`, and the names actively mislead.

1. **`wikify-baseline` — opaque + misleading.** "Baseline" only has meaning
   inside the internal strategy-science framing (it is the comparison baseline
   for the scripted-vs-guided study). A public user reads "baseline" as a
   placeholder/reference, not as a usable, complete build workflow. It is in
   fact the simplified conventional-RAG wiki builder.
   - Propose: **`wikify-build-simple`** (or `wikify-build-rag`).

2. **`wikify-investigate` — opaque; hides that this is the main entry point.**
   "Investigate" reads like a read-only diagnostic/debugging tool, yet it is
   the primary, recommended, full-featured wiki builder (coverage-driven
   researcher loop with data waves). Newcomers will overlook it.
   - Propose: **`wikify-build`** (claim the prime name for the primary path).

   Pairing the two builders makes the strategy axis legible:
   `wikify-build` (guided) vs `wikify-build-simple` (scripted).

3. **`wikify-investigate-explore` — subskill name should track its parent.**
   If `investigate` becomes `build`, rename to **`wikify-build-explore`** (or
   `wikify-explore-patterns`). It is meaningless standalone and is only ever a
   child of the main builder.

4. **`wikify-gather-evidence-cluster` — leaks an implementation detail.**
   "Cluster" exposes the sibling-slug batching internal; the skill already
   handles singletons. Drop it.
   - Propose: **`wikify-gather-evidence`**.

5. **`wikify-bundle` — internal jargon.** "Bundle" is the system's word for the
   run/output state directory; a newcomer will not map "bundle" to "wiki
   project state + commit mechanics." It is a real schema term, so this is a
   milder flag.
   - Propose: **`wikify-state`** (keep "bundle" as the documented noun inside).

6. **`wikify` (the shared-reference skill) — collides with the product/CLI
   name.** A bare `wikify` skill is ambiguous against the `wikify` product and
   the `wikify` CLI; it is not invokable as a step yet sits at the top of the
   list as if it were the umbrella entry.
   - Propose: **`wikify-reference`** (or `wikify-common`).

Names that are already clear and should NOT change: `wikify-arxiv`,
`wikify-query`, `wikify-refine`, `wikify-write-page`, `wikify-extract-data`,
`wikify-consolidate-data`, `wikify-organize-wiki`, `wikify-search-corpus`,
`wikify-search-wiki`.

## Secondary finding (metadata)

In the live skill registry, `wikify-baseline` and `wikify-bundle` surfaced with
the skill **name echoed as the description** ("wikify-baseline: wikify-baseline",
"wikify-bundle: wikify-bundle") even though both `SKILL.md` files carry a proper
`description:` in frontmatter. Before publishing, confirm the registration/
packaging step reads the frontmatter `description` for every skill so discovery
prose is not lost. Low severity, but it directly affects how a public user finds
these two skills.
