---
name: wikify-search-wiki
description: Explain and use the Wikify committed-wiki CLI as the read/search surface over wiki pages, page evidence, links, backlinks, overlap, thin pages, and wiki-to-corpus follow-up. Use when answering questions from committed pages or inspecting wiki coverage. This skill is read-only and does not refine or mutate bundle state.
allowed-tools: Bash(wikify wiki *)
---

# wikify-search-wiki

Use this skill to inspect the committed wiki. It teaches the read/query
surface; it does not decide whether to refine, expand, or stop.

## Capability Surface

- List committed pages and wiki files.
- Show a page compactly or with `--full`. Page handles accept exact
  slugs OR a unique case-insensitive prefix, so partial titles work
  when unambiguous.
- Open `wikify wiki repl --run <bundle>` for iterative committed-page
  search without repeating the bundle path.
- Search committed page text by title, alias, or body phrase.
- `wikify wiki traverse <slug> --to <relation>` walks one wiki hop:
  `links` (outgoing), `linked-by` (incoming), `co-evidence` (pages
  sharing source docs), `evidence` (emits `chunk:` handles for the
  corpus). Output is handles — pipe directly into another `traverse`
  or into `wikify corpus show`/`corpus traverse`.
- `--format quiet` prints handles only; default for piped stdout.
- Bridge from a committed page back to corpus evidence by piping
  `wiki traverse ... --to evidence --format quiet` into corpus tools.

## Default Loop

1. Search or list pages.
2. Inspect a compact page result.
3. Show the selected page only when needed.
4. Inspect relationships or evidence handles.
5. Decide whether the next step is another wiki query, a corpus search,
   or a workflow-level bundle mutation.

## Examples

```bash
wikify wiki list --run <bundle>
wikify wiki find "ALD vs CVD" --run <bundle> --top-k 5
wikify wiki find "atomic layer deposition" --run <bundle> --text
wikify wiki show "Atomic Layer Deposition" --run <bundle> --full
wikify wiki traverse "Atomic Layer Deposition" --to links \
    --top-k 10 --run <bundle>
wikify wiki traverse "Atomic Layer Deposition" --to evidence \
    --format quiet --run <bundle> \
  | xargs -I {} wikify wiki show {} --run <bundle>   # bridge to corpus
wikify wiki repl --run <bundle>
wikify wiki check --run <bundle>
```

## Does Not Do

- Does not append query feedback.
- Does not change concept status.
- Does not commit or refine pages.
- Does not decide whether wiki coverage is sufficient.

## References

- `references/wiki-cli-patterns.md` - committed wiki command grammar and
  use cases.
- `references/wiki-recursive-search.md` - recursive wiki search loops.
- `references/wiki-corpus-bridges.md` - moving from wiki evidence to
  corpus search.
- `../wikify/references/cli/grammar.md` - shared CLI grammar.
- `../wikify/references/cli/output-contract.md` - output conventions.
