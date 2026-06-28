# SKILL Authoring Best Practices — Checklist

Source: Anthropic Agent Skills authoring guidance. Use this to audit and
rewrite every Wikify skill before public release.

## Progressive disclosure (the core principle)

- [ ] Treat the skill as three loading tiers: (1) name + description in
      frontmatter, always in context; (2) `SKILL.md` body, loaded when the
      skill triggers; (3) bundled reference files, loaded only when the body
      points to them. Put just enough in each tier to decide whether to go
      deeper.
- [ ] The body should be the table of contents and the common path, not the
      full manual. Push exhaustive detail, edge cases, schemas, and long
      examples into reference files.
- [ ] Link reference files by relative path with a one-line "read this when…"
      cue so the model loads them on demand, not preemptively.

## Frontmatter

- [ ] Only two fields are required and supported: `name` and `description`.
      Do not invent extra keys.
- [ ] `name`: lowercase, hyphenated, matches the directory, <= 64 chars,
      no spaces or special characters.
- [ ] `description`: third person, states BOTH what the skill does AND when to
      use it ("Use when…"). This is the only signal the model sees at
      discovery time, so make trigger conditions concrete and keyword-rich.
- [ ] Keep `description` under ~1024 chars; front-load the trigger.

## SKILL.md size and structure

- [ ] Keep the body lean — target well under ~500 lines / ~5k words. If it
      grows past that, that is the signal to split into reference files.
- [ ] One skill = one capability. Split multi-capability bundles into separate
      skills rather than one mega-skill.
- [ ] Open with the goal and the decision the model must make, then the
      steps. Lead with named procedures/algorithms over loose prose rubrics.
- [ ] Use headings, short lists, and tables; avoid walls of narrative.

## Splitting detail into reference files

- [ ] Move schemas, CLI grammar, long worked examples, troubleshooting, and
      rarely-needed branches into separate `.md` (or data) files in the skill
      dir.
- [ ] Each reference file should be self-contained and individually loadable.
- [ ] Reference, don't duplicate: shared material lives in one place (e.g. a
      shared `wikify` reference skill) and other skills point to it.

## Prescriptive (not narrative) voice

- [ ] Write imperative instructions to the model: "Do X", "If Y, then Z",
      "Never W". Not stories, not "the system was designed to…".
- [ ] State preconditions, steps, and stop conditions explicitly. Prefer
      checklists and numbered procedures over paragraphs.
- [ ] Be specific about tools, file types, and exact commands; avoid vague
      "you might consider" hedging.
- [ ] No meta-references to plans/todos/phases ("per tasks/foo.md", "in this
      phase"). Describe what the skill IS and DOES.

## Output discipline

- [ ] Token-light by default: files are the interface, not stdout.
- [ ] Examples should be minimal and runnable, not decorative.
- [ ] Keep console/text output ASCII; avoid emojis and non-ASCII.

## Pre-release audit pass (apply per skill)

- [ ] Frontmatter has exactly `name` + `description`, both well-formed.
- [ ] Description trigger is discoverable and unambiguous vs sibling skills.
- [ ] Body is lean, prescriptive, and points to references for depth.
- [ ] No dead/duplicated content; no plan/phase meta-references.
- [ ] Reference files load on demand and are self-contained.
