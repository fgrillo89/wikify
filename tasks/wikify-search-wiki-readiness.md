# wikify-search-wiki readiness assessment

Question: is `wikify-search-wiki` usable for an agent writing a paper off
a committed wiki bundle (e.g. `ald_baseline_cluster_2026_05_25`, 14
articles + 3 people, 371 edges, 164 evidence rows, 6 categories)?

## Minimum readiness criteria

| # | Criterion | Status |
|---|-----------|--------|
| 1 | `find` works in all modes: text (exact), bm25, semantic, hybrid | PASS |
| 2 | `show` returns the full page body | PASS |
| 3 | A handle resolves by the **natural concept name**, not only the on-disk file slug | FIXED |
| 4 | `traverse` walks relations (`links`, `evidence`, `similar`, `categories`, ...) and the `evidence` relation bridges to corpus chunks | PASS |
| 5 | Warm latency (persistent MCP session) < ~100 ms/call | PASS |
| 6 | `show` reports the real page title, not the kebab slug | FIXED |
| 7 | SKILL.md examples and the `wiki schema` handle text run as written against a real bundle | FIXED |

## Speed (measured, warm in-process; the MCP surface keeps these warm)

| op | ms/call |
|----|---------|
| Bundle.open (once) | 0.1 |
| find text | 1.8 |
| find bm25 | 5.1 |
| find semantic | 21.6 |
| find hybrid | 23.2 |
| show | 0.3 |
| traverse links / similar / evidence | 3-15 |

Bash one-shot `wikify wiki ...` pays ~1 s python/uv cold-start per call and
reloads the embedding model per semantic call (~3 s). This is why the
skill makes MCP the primary surface and bash the fallback; the warm MCP
path is well within budget. Cold-start is not a blocker for the
agent-writing-a-paper workflow because the MCP session persists.

## The defect that blocked readiness (criteria 3, 6, 7)

`resolve_slug` matched handles only against the on-disk filename stem.
Wiki filenames have used two conventions: current bundles keep the
title's spaces (`Atomic Layer Deposition.md`); older bundles (including
the ALD baseline) used kebab-case (`atomic-layer-deposition.md`). Both
name the same concept, and the frontmatter `id`/`title` is always the
natural title. The skill teaches title handles
(`wiki_show(handle="Atomic Layer Deposition")`), which 404'd against the
kebab-named baseline bundle.

Fix: resolve handles case- and separator-insensitively (collapse runs of
whitespace / `-` / `_`, casefold), comparing stems in Python so a
case-insensitive filesystem cannot report the queried casing as the slug.
`show_page` now also returns the frontmatter `title`, surfaced by the CLI
and the MCP `wiki_show` tool (which previously reported the kebab slug as
the title).
