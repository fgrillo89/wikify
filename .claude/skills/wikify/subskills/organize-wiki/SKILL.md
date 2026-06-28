---
name: organize-wiki
description: Build a validated navigation hierarchy for a committed Wikify bundle. Use after batches of pages are committed and before render so articles are grouped into reader- and agent-friendly topic sections.
allowed-tools: Bash(wikify wiki *)
---

# organize-wiki

Use this capability after pages have been committed. The output is a
topic hierarchy, not article prose and not a taxonomy embedded in page
frontmatter.

## Inputs

Generate the organizer context:

```bash
wikify wiki navigation-context --run <bundle> --out <bundle>/derived/navigation_context.json
```

Read that JSON. It contains page ids, titles, kinds, aliases, excerpts,
links, evidence counts, source counts, compact cluster hints, existing
navigation when present, and freshness deltas.

`pages` carries every committed page across all three kinds: `article`,
`person`, and `data`. `kind=data` pages live in a separate data store,
not the wiki page graph, but they render and are navigation targets, so
they appear here and must be placed in a group. Group them with the
article whose topic they quantify, or in a dedicated data/tables group.
Do not drop them: any page id omitted from every group lands in the
validator's `ungrouped_page_ids`.

Use `freshness.has_navigation` to choose the organizing mode:

- Full organization: no existing navigation, or the existing hierarchy is
  too weak to preserve. Build the whole tree from `pages` and
  `cluster_hints`.
- Incremental subtree update: existing navigation is present and only
  `freshness.new_page_ids` or `freshness.changed_page_ids` need placement.
  Preserve stable groups and update only the closest affected branch unless
  the new evidence clearly changes the top-level structure.

Use `cluster_hints` as deterministic neighbor suggestions. They rank related
pages by explicit links, backlinks, shared evidence documents, and title or
excerpt token overlap. Treat them as organizing evidence, not as mandatory
edges.

## Output

Write a JSON file with this shape:

```json
{
  "schema_version": 1,
  "strategy": "baseline",
  "groups": [
    {
      "id": "materials-and-devices",
      "title": "Materials and devices",
      "description": "Materials systems and device classes covered by the wiki.",
      "page_ids": ["Hafnium Oxide"],
      "children": []
    }
  ]
}
```

Then validate and persist it:

```bash
wikify wiki apply-navigation <path-to-json> --run <bundle>
```

## Grouping Rules

- Group articles by scientific topic and article role: materials,
  devices, mechanisms, methods, characterization, applications, theory,
  and people when applicable.
- Place `kind=data` pages with the topic they quantify, or in a single
  data/tables group. Never leave them unplaced.
- Prefer moving a changed page inside its current broad topic when
  `existing_navigation` already has a sensible location for it.
- Place new pages near their strongest `cluster_hints` neighbors when the
  scientific topic agrees with the page title and excerpt.
- Use two levels for small or narrow wikis. Use three or four levels
  only when the page set clearly has nested subtopics.
- Do not force every group to have the same depth.
- Do not duplicate a page across groups.
- Keep group titles short and reader-facing.
- Put uncertain pages in the closest broad group; the validator will
  put omitted pages into `ungrouped_page_ids`.
- Do not expose RAG mechanics, chunking, telemetry, or model details in
  group titles or descriptions.

## Workflow Placement

- Baseline workflows run this once after the commit loop and before
  `wikify render`.
- Iterative workflows run it after a committed batch of at least five
  pages, or once at workflow close when fewer pages changed.
