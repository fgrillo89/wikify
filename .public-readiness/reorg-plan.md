# Skill Reorganization Plan

Goal: collapse the 15 flat `wikify-*` skills into **four first-class
skills** plus a tree of bundled subskills, so a public user sees only the
entry points they should ever invoke.

Date: 2026-06-28. Branch: `wikify-public-readiness-prep`.
Scope: skill tree layout, cross-reference rewrites, and the new `ingest`
skill spec. No behavior change to the Python package or MCP server.

---

## 1. Target shape

Four directories — and only four — sit at the top level of the skills
tree (`.claude/skills/`). Everything else is a **bundled subskill**: a
`SKILL.md` that lives *inside* a first-class skill's directory and is
therefore NOT independently discovered/registered (the skills scanner
treats subdirectories of a skill as bundled resources, like
`references/`). Subskills are reached by relative-path links from their
parent, exactly the way skills already reference each other today.

```
.claude/skills/
├── query/                     # read path        (was wikify-query)
│   └── SKILL.md
├── arxiv/                     # acquisition path  (was wikify-arxiv)
│   └── SKILL.md
├── ingest/                    # NEW: parse local docs -> corpus
│   └── SKILL.md
└── wikify/                    # PRIMARY build path (was wikify-investigate)
    ├── SKILL.md               #   also the umbrella/router for the tree
    └── subskills/
        ├── reference/         # shared reference hub (was the `wikify` skill)
        │   ├── SKILL.md
        │   └── references/    # all durable reference markdown (unchanged tree)
        ├── explore/           # was wikify-investigate-explore
        │   └── SKILL.md
        ├── gather-evidence/   # was wikify-gather-evidence-cluster
        │   └── SKILL.md
        ├── write-page/        # was wikify-write-page (+ references/)
        ├── organize-wiki/     # was wikify-organize-wiki
        ├── extract-data/      # was wikify-extract-data
        ├── consolidate-data/  # was wikify-consolidate-data
        ├── bundle/            # was wikify-bundle (+ references/)
        ├── search-corpus/     # was wikify-search-corpus (+ references/)
        ├── search-wiki/       # was wikify-search-wiki (+ references/)
        ├── refine/            # was wikify-refine (demoted from entry point)
        └── build-simple/      # was wikify-baseline (renamed; see §3)
```

Why everything nests under `wikify/`: the primary build skill already
uses (directly or transitively) every subskill in the list. `query`,
`arxiv`, and `ingest` consume a small shared subset (search-*, bundle,
reference) and reach them by relative path
(`../wikify/subskills/<x>/SKILL.md`). Keeping a single physical home for
each subskill honors single-source-of-truth (no duplicated trees).

### Naming decisions

| Old top-level skill | New location & name | Rationale |
|---|---|---|
| `wikify-investigate` | `wikify/` (name `wikify`) | The real primary builder claims the prime name. |
| `wikify` (reference hub) | `wikify/subskills/reference/` (name `reference`) | Resolves the name collision; it was never a workflow. |
| `wikify-query` | `query/` (name `query`) | First-class read path. |
| `wikify-arxiv` | `arxiv/` (name `arxiv`) | First-class acquisition path. |
| (new) | `ingest/` (name `ingest`) | First-class parse path; see §5. |
| `wikify-baseline` | `wikify/subskills/build-simple/` (name `build-simple`) | "Baseline" is internal strategy-science jargon and reads as a placeholder. `build-simple` says plainly: the simplified conventional-RAG builder, NOT the main path. |
| `wikify-investigate-explore` | `wikify/subskills/explore/` (name `explore`) | Tracks its only parent. |
| `wikify-gather-evidence-cluster` | `wikify/subskills/gather-evidence/` (name `gather-evidence`) | Drops the `cluster` implementation detail (handles singletons). |
| `wikify-write-page` | `wikify/subskills/write-page/` (name `write-page`) | Prefix redundant once nested. |
| `wikify-organize-wiki` | `wikify/subskills/organize-wiki/` | " |
| `wikify-extract-data` | `wikify/subskills/extract-data/` | " |
| `wikify-consolidate-data` | `wikify/subskills/consolidate-data/` | " |
| `wikify-bundle` | `wikify/subskills/bundle/` | " |
| `wikify-search-corpus` | `wikify/subskills/search-corpus/` | " |
| `wikify-search-wiki` | `wikify/subskills/search-wiki/` | " |
| `wikify-refine` | `wikify/subskills/refine/` (name `refine`) | Demoted: it is maintenance of a build, invoked through the build path, not a top-level entry. (Tradeoff flagged in §7.) |

Nested subskill `name:` frontmatter is set to the prefix-free directory
name (`bundle`, `write-page`, `reference`, ...). Names stay globally
unique across the tree, so this is safe even if a future packaging step
recurses discovery.

---

## 2. Exact move map (git mv only; lossless, no deletes)

Run in this order. Step 0 is a preflight collision guard; steps 1-2 free
the `wikify` name before the build skill claims it; step 3 creates the
container; steps 4-15 relocate subskills; steps 16-17 rename the
remaining entry points; step 18 is the only new file.

### Step 0 — preflight: assert no destination collisions

Every `git mv` target below must NOT already exist, or the move either
fails (file target) or nests the source *inside* the existing dir
(directory target) — silently corrupting the tree. The temp name
`wikify-reference` and the new first-class dirs `query`, `arxiv`,
`ingest` are the collision-prone ones (and `wikify/subskills` must be a
fresh dir). Abort the whole sequence if any of these resolve:

```sh
# Fails loudly (non-zero) if any reorg destination already exists.
for d in \
  .claude/skills/wikify-reference \
  .claude/skills/query \
  .claude/skills/arxiv \
  .claude/skills/ingest \
  .claude/skills/wikify/subskills ; do
  if [ -e "$d" ]; then echo "COLLISION: $d already exists; abort"; exit 1; fi
done
```

Confirmed clean on the current tree (2026-06-28): none of
`wikify-reference`, `query`, `arxiv`, `ingest`, or `wikify/subskills`
exist yet. Re-run the guard at execution time in case the tree drifted.

```sh
# 1. Free the `wikify` name (move the reference hub out to a temp top-level name).
git mv .claude/skills/wikify .claude/skills/wikify-reference

# 2. Promote the primary builder to the prime name.
git mv .claude/skills/wikify-investigate .claude/skills/wikify

# 3. Create the subskill container (mkdir is not a delete; git mv needs the parent to exist).
mkdir .claude/skills/wikify/subskills

# 4-15. Relocate every subskill into wikify/subskills/.
git mv .claude/skills/wikify-reference                .claude/skills/wikify/subskills/reference
git mv .claude/skills/wikify-investigate-explore      .claude/skills/wikify/subskills/explore
git mv .claude/skills/wikify-gather-evidence-cluster  .claude/skills/wikify/subskills/gather-evidence
git mv .claude/skills/wikify-write-page               .claude/skills/wikify/subskills/write-page
git mv .claude/skills/wikify-organize-wiki            .claude/skills/wikify/subskills/organize-wiki
git mv .claude/skills/wikify-extract-data             .claude/skills/wikify/subskills/extract-data
git mv .claude/skills/wikify-consolidate-data         .claude/skills/wikify/subskills/consolidate-data
git mv .claude/skills/wikify-bundle                   .claude/skills/wikify/subskills/bundle
git mv .claude/skills/wikify-search-corpus            .claude/skills/wikify/subskills/search-corpus
git mv .claude/skills/wikify-search-wiki              .claude/skills/wikify/subskills/search-wiki
git mv .claude/skills/wikify-refine                   .claude/skills/wikify/subskills/refine
git mv .claude/skills/wikify-baseline                 .claude/skills/wikify/subskills/build-simple

# 16-17. Rename the remaining first-class entry points (drop the wikify- prefix).
git mv .claude/skills/wikify-query  .claude/skills/query
git mv .claude/skills/wikify-arxiv  .claude/skills/arxiv

# 18. NEW skill (created, not moved) — see §5 for content.
#     .claude/skills/ingest/SKILL.md
```

`git mv` preserves history for each `SKILL.md` and its bundled
`references/` subtree (write-page, bundle, search-corpus, search-wiki,
and the reference hub each carry a `references/` directory that moves
with the dir).

---

## 3. Relative-path rewrite rules (inside skill markdown)

Every skill references siblings and the reference hub by relative path
in a `## References` section and in inline prose. After the move the
depth changes. Apply these deterministic rewrites:

| From file | Old link form | New link form |
|---|---|---|
| `wikify/subskills/<A>/SKILL.md` (any subskill) | `../wikify-<B>/SKILL.md` | `../<B>/SKILL.md` |
| same | `../wikify/references/<p>` | `../reference/references/<p>` |
| `wikify/subskills/reference/SKILL.md` (the hub) | `references/<p>` | `references/<p>` (unchanged) |
| `wikify/SKILL.md` (top-level builder) | `../wikify-<B>/SKILL.md` | `subskills/<B>/SKILL.md` |
| same | `../wikify/references/<p>` | `subskills/reference/references/<p>` |
| `query/`, `arxiv/`, `ingest/SKILL.md` | `../wikify-<B>/SKILL.md` | `../wikify/subskills/<B>/SKILL.md` |
| same | `../wikify/references/<p>` | `../wikify/subskills/reference/references/<p>` |

where `<B>` is the renamed subskill (e.g. `wikify-gather-evidence-cluster`
-> `gather-evidence`, `wikify-baseline` -> `build-simple`).

### Files with relative links to rewrite (51 occurrences, 11 files)

Counts from `rg` on the current tree:

- `wikify-investigate/SKILL.md` (14) -> becomes `wikify/SKILL.md`; uses
  the `subskills/...` and `subskills/reference/references/...` forms.
- `wikify-baseline/SKILL.md` (9) -> `subskills/build-simple/SKILL.md`;
  sibling form `../<B>/SKILL.md`, hub form `../reference/references/...`.
- `wikify-refine/SKILL.md` (5) -> `subskills/refine/`.
- `wikify-bundle/SKILL.md` (4), `wikify-write-page/SKILL.md` (4),
  `wikify-investigate-explore/SKILL.md` (4) -> sibling/hub forms.
- `wikify-search-corpus/SKILL.md` (3), `wikify-query/SKILL.md` (3)
  -> query uses the `../wikify/subskills/...` form (it is a top-level sibling).
- `wikify-gather-evidence-cluster/SKILL.md` (2),
  `wikify-search-wiki/SKILL.md` (2).
- `wikify-arxiv/SKILL.md` (1) -> arxiv uses `../wikify/subskills/reference/references/...`.

### Inline prose skill-name mentions

Beyond `## References` links, prose names subskills (e.g. investigate's
"dispatches `wikify-investigate-explore` Tasks running P1-P5"). Rewrite
each bare `wikify-<name>` mention to the new name (`explore`,
`gather-evidence`, `build-simple`, etc.). Grep each moved `SKILL.md` for
the old token set after the move and replace.

### Reference-hub markdown (not just SKILL.md)

The reference hub's `references/` subtree also names old skills in prose.
After the hub moves to `wikify/subskills/reference/references/`, rewrite
each bare `wikify-<name>` token in these files to the new name. Confirmed
hits on the current tree (`rg` over `wikify/references/`, 6 files):

- `exploration/patterns.md` — line 4 names `wikify-investigate-explore`
  ("procedures used by `wikify-investigate-explore`") and `wikify-investigate`
  ("The editor (`wikify-investigate`)"); line 16 names
  `wikify-gather-evidence-cluster` ("go through `wikify-gather-evidence-cluster`").
  Rewrite to `explore`, `wikify`, and `gather-evidence` respectively.
- `exploration/maturity.md`, `exploration/workflow-contracts.md`,
  `writing/schemas.md`, `mcp/setup.md`, `mcp/fallback.md` — grep each for
  the old `wikify-<name>` token set and apply the same name map (§3
  frontmatter table) after the move.

These are content-only string rewrites; the files move with the hub dir
under `git mv` and need no path-depth changes (they sit beside the
`SKILL.md` that already resolves `references/<p>` relatively).

### Frontmatter `name:` field

Every moved `SKILL.md` has `name: wikify-<x>` in frontmatter that must
become the new directory name:
- `wikify-investigate` -> `name: wikify`
- `wikify` (hub) -> `name: reference`
- `wikify-query` -> `name: query`; `wikify-arxiv` -> `name: arxiv`
- `wikify-baseline` -> `name: build-simple`
- `wikify-investigate-explore` -> `name: explore`
- `wikify-gather-evidence-cluster` -> `name: gather-evidence`
- all other `wikify-<x>` -> `name: <x>` (prefix dropped)

---

## 4. The umbrella / router content

The old reference-hub `SKILL.md` carried two things: (a) the shared
`references/` index, and (b) the "Core Capability Skills / Workflow
Skills / When to use which" router that listed the whole tree. Split
them:

- **(a) stays** in `wikify/subskills/reference/SKILL.md` (now a pure
  reference index; rewrite its skill-tree prose to the new names/paths or
  trim it to just the reference index).
- **(b) moves up** into the top-level `wikify/SKILL.md`, which becomes
  the router a user lands on: "primary builder here; for a simpler
  conventional-RAG build follow `subskills/build-simple/SKILL.md`; to
  refine committed pages follow `subskills/refine/SKILL.md`; read with
  the top-level `query`; acquire with `arxiv`; parse local docs with
  `ingest`." Update the bullet list to the four entry points + the
  build-internal subskills, with corrected relative paths.

---

## 5. New `ingest` skill spec

`ingest` is the first-class "I have a folder of documents, turn it into a
corpus" entry point. It is a thin, decision-light wrapper over
`wikify corpus build`, owning **parser-backend selection** — the guidance
that currently lives in `CLAUDE.md` under "Parser backend (Docling
default)" moves here verbatim (and is deleted from `CLAUDE.md`; see §6).

`.claude/skills/ingest/SKILL.md`:

```yaml
---
name: ingest
description: Parse a directory of documents (PDF / DOCX / PPTX / HTML) into a queryable Wikify corpus. Use when the user has local files to turn into a corpus, or to finish an arxiv harvest. Wraps `wikify corpus build` and owns parser-backend choice (docling default, marker, lite), the --out convention, partial-failure policy, and post-build health checks.
allowed-tools: Bash(wikify corpus *)
---
```

Body contents:

- **Command:** `wikify corpus build <source> --out data/corpora/<name>
  [--mode additive|sync] [--parser default|lite|marker|docling]
  [--workers N] [--openalex/--no-openalex] [--allow-partial]`.
- **Parser-backend guidance (moved from CLAUDE.md):** default = Docling
  for every format; first run downloads the Granite-Docling-258M formula
  model (~258 MB) + layout/table models, then median PDF ~10 s on an
  Ampere GPU. `--parser marker` = fastest PDF path when equation
  extraction is not needed. `--parser lite` = CI / low-resource
  (pymupdf4llm + python-docx + python-pptx + trafilatura, no models).
  (`default` and `docling` are the same backend.)
- **Output convention:** `--out data/corpora/<name>`, not `build/<name>`
  (matches the corpus-output-dir rule).
- **Partial-failure policy:** default OFF — any per-file parse failure
  aborts with exit 5; pass `--allow-partial` to continue and recover
  successful papers on the next run.
- **Post-build check:** `wikify corpus check data/corpora/<name>`
  (corpus dir is a positional argument, not `--out`; it falls back to
  `WIKIFY_CORPUS` or cwd when omitted — see
  `docs/filesystem-state-design.md:738`) for
  doc/chunk/embedding/edge counts before declaring the corpus ready.
- **Rebuild surface (mention, do not expand):** `corpus rechunk`,
  `corpus refresh` for re-deriving artifacts on an existing corpus.
- **References:** `../wikify/subskills/reference/references/cli/grammar.md`,
  `.../cli/output-contract.md`, `.../cli/exit-codes.md`.

**Handoff boundary with `arxiv`:** `arxiv` already states it "stages
PDFs ... and the handoff to ingest." Make that literal — `arxiv`'s final
build step becomes "follow `ingest` on the staged directory" rather than
inlining `corpus build`. This is the one substantive content change
beyond path rewrites; flagged as a follow-up rather than required for the
move (§7).

---

## 6. Cross-references outside the skill tree

These reference old skill names/paths and must be updated in the same
change set (blast radius beyond the skill files):

**Tests (will fail until updated):**
- `tests/wikify/test_skill_layout.py`
  - `WIKIFY_REFERENCES = SKILLS_ROOT/"wikify"/"references"` ->
    `SKILLS_ROOT/"wikify"/"subskills"/"reference"/"references"`.
  - `CORE_SKILLS` tuple (`wikify-search-corpus`, `-search-wiki`,
    `-write-page`, `-bundle`) -> nested `subskills/...` paths; the
    `test_core_skills_stay_small` loop builds `SKILLS_ROOT/name/SKILL.md`
    and must use the nested path.
  - `_iter_skill_dirs()` now yields only `arxiv, ingest, query, wikify`;
    `test_every_skill_has_skill_md_with_matching_name` then requires
    each top-level `name:` to equal its dir — satisfied by §3.
  - `test_baseline_workflow_stays_reasonable` reads
    `wikify-baseline/SKILL.md` -> `wikify/subskills/build-simple/SKILL.md`.
  - `test_umbrella_lists_only_existing_workflows` parses `wikify/SKILL.md`
    for `- \`wikify-<name>\`` bullets; rewrite for the new router bullet
    format/paths (§4).
  - `test_every_reference_link_resolves` assumes `../wikify/references/`
    from subdirs and `references/` from the umbrella; update the expected
    resolution roots to the new `subskills/reference/references/` layout
    and the subskill depth.
  - `test_old_singular_reference_dir_is_gone` /
    `test_old_workflows_dir_is_gone`: path checks still pass but their
    comments/intent are stale post-reorg — review and either retarget or
    retire (they were guarding a previous migration).
- `tests/wikify/test_skill_allowed_tools.py`
  - `_wikify_skill_paths()` uses `skills_root.glob("wikify*/SKILL.md")`
    (one level) — now matches only `wikify/SKILL.md` and misses `query`,
    `arxiv`, `ingest`, and all nested subskills. Change to
    `skills_root.rglob("SKILL.md")` (filtered to the wikify plugin tree)
    so every moved skill is still linted.
- `tests/wikify/test_wiki_handle_resolution.py` — grep hit; confirm
  whether it references a skill name string and update if so.

**Source docstrings (meta-references to the workflow name / hub path):**
- `src/wikify/bundle/work/notebook.py:9` — "The `wikify-investigate`
  workflow writes here" -> `wikify`.
- `src/wikify/bundle/work/maturity.py:10` — "`wikify-investigate` editor
  reads this" -> `wikify`.
- `src/wikify/cli/work.py:1307` — docstring points at
  `.claude/skills/wikify/references/exploration/maturity.md`; the hub
  moves, so rewrite to
  `.claude/skills/wikify/subskills/reference/references/exploration/maturity.md`.
- `src/wikify/sources/arxiv.py:63-64` — user-agent string
  `wikify-arxiv-harvester/0.1` is NOT a skill-name reference; leave it.

**Docs:**
- `AGENTS.md` — four separate hits, all in the same change set:
  - line 14 — "Read First" bullet points at
    `.claude/skills/wikify/references/`; the hub moves, so rewrite to
    `.claude/skills/wikify/subskills/reference/references/`.
  - line 80 — "Workflow shape and stopping criteria live in
    `.claude/skills/wikify*/SKILL.md`"; the flat `wikify*` glob no longer
    spans the tree (subskills nest one level deeper). Rewrite to
    `.claude/skills/**/SKILL.md` (or name the four entry points).
  - lines ~166-178 — capability/workflow skill bullets list
    `wikify-search-corpus`, `-search-wiki`, `-write-page`, `-bundle`,
    `wikify-baseline`, `wikify-investigate`, `wikify-query`, `wikify-refine`.
    Rewrite to the four entry points + nested subskill names.
- `docs/architecture.md` — three hits:
  - line 37 — "stopping criteria all live in
    `.claude/skills/wikify*/SKILL.md`"; same flat-glob fix as AGENTS.md:80
    (`.claude/skills/**/SKILL.md`).
  - lines ~186-187 — "The shared reference skill
    (`.claude/skills/wikify/`)" -> the hub's new path
    `.claude/skills/wikify/subskills/reference/`.
  - lines ~191-198 — same capability/workflow name list; rewrite.
- `CLAUDE.md` — delete the "Parser backend (Docling default)" section
  (moved into `ingest`); optionally add a one-line pointer to the
  `ingest` skill. Also update any prose naming `wikify-investigate` as
  the active track to `wikify`.

**Workflow generator (descriptive prose; no functional dependency):**
- `.claude/workflows/wikify-public-readiness.js` already says "wikify
  (the main entry point; currently named wikify-investigate)" and
  carries the reorg target in its prompt text. Leave as historical
  framing; it is not a live path reference.

**Packaging interaction (handoff to the plugin phase):**
- `.public-readiness/research-plugin.md` §6 proposes nesting all 15
  skills flat under `.claude/skills/wikify/skills/*` with the plugin root
  also named `wikify`. That layout predates this reorg and now triple-
  collides on `wikify` (plugin root / build skill / dir). Recommend the
  plugin phase load the skills tree via the **project skills-dir scope**
  (`.claude/skills/` as-is) rather than introducing a nested `wikify/`
  plugin root, so the four-entry-point tree from this plan is exactly
  what ships. Note this dependency in the packaging step.
- `.public-readiness/research-plugin.md` leaks personal identifiers /
  machine paths that must be redacted before this doc (or anything
  derived from it) ships publicly. Required edits in the same change set:
  - line 158 — `/plugin marketplace add fgrillo89/scholarforge` -> the
    public org/repo slug placeholder (e.g. `<owner>/<repo>`), not a
    personal GitHub handle.
  - lines 179-180 — the `.mcp.json` example hardcodes
    `C:/dev/scholarforge` (project path) and
    `C:/dev/scholarforge/data/corpora/ald_all_marker` (machine corpus
    path). Replace with a relative project root and a
    `${WIKIFY_CORPUS}` / placeholder corpus, matching the "both must go
    before this is shippable" caveat already noted there.
  - line 282 — `"repository": "https://github.com/fgrillo89/scholarforge"`
    -> the public repository URL placeholder.
  The reorg change set is the right place to land these since it already
  edits §6 of this same file; flag as a hard public-readiness gate, not
  optional.

---

## 7. Flagged tradeoffs / follow-ups

- **`refine` demotion.** Refine is no longer a top-level entry; a user
  who wants to refine reaches it through the `wikify` router. This is the
  instructed shape, but it slightly buries a real maintenance workflow.
  If discoverability suffers, reconsider promoting `refine` back to
  first-class (it would be a 5th entry point).
- **`arxiv` -> `ingest` delegation.** Making `arxiv`'s build step call
  `ingest` (rather than inlining `corpus build`) is the clean boundary
  but is a content edit, not a move. Do it in the same change set if
  cheap; otherwise it is the one explicit follow-up.
- **Subskill discoverability assumption.** This plan relies on the
  scanner treating `SKILL.md` files under another skill's directory as
  bundled resources (not registered skills). If a future Claude Code
  version recurses skill discovery, add an explicit `skills` allowlist in
  the plugin manifest, or rename `subskills/` to a non-`SKILL.md`-bearing
  convention. The prefix-free, globally-unique subskill names keep that
  fallback safe.
- **`build-simple` line ceiling.** The existing 250-line ceiling test for
  the old `wikify-baseline` should follow the renamed file; keep the
  ceiling.
```
