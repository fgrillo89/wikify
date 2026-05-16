---
name: wikify-organize-wiki
description: Build a validated navigation hierarchy for a committed Wikify bundle. Use after batches of pages are committed and before render so articles are grouped into reader- and agent-friendly topic sections.
allowed-tools: Bash(wikify wiki *)
---

# wikify-organize-wiki

Use this capability after pages have been committed. The output is a
topic hierarchy, not article prose and not a taxonomy embedded in page
frontmatter.

## Inputs

Generate the organizer context:

```bash
wikify wiki navigation-context --run <bundle> --out <bundle>/derived/navigation_context.json
```

Read that JSON. It contains page ids, titles, kinds, aliases, excerpts,
links, evidence counts, and source counts.

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
