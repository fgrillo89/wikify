# Corpus Graph Traversals

The corpus fluent API may expose graph concepts through CLI flags or
handles. Use the CLI surface available in the current branch; do not
invent unsupported flags.

## Traversal Families

- Source neighborhood: from one source to cited/citing/nearby sources.
- Author neighborhood: from author to papers, coauthors, and topics.
- Chunk neighborhood: from a chunk to nearby chunks, figures, equations,
  or source sections.
- Citation neighborhood: from a cited work to in-corpus citing chunks.
- Figure/equation neighborhood: from a media/equation handle to the
  chunks that discuss it.

## Fallback When A Direct Flag Is Missing

If the desired graph traversal is not exposed by the CLI:

1. Use `corpus show` on the nearest handle.
2. Inspect the compact metadata and previews.
3. Search with concrete terms from that output.
4. Record the missing traversal as a CLI/API gap if the workflow needs it
   repeatedly.

## Example Traversal Recipes

### Citation Neighborhood

```text
seed source -> cited works -> in-corpus citing chunks -> concept query
```

### Author Network

```text
author handle -> authored sources -> coauthors -> repeated concepts
```

### Figure-Grounded Evidence

```text
concept query -> chunk with figure mention -> figure handle -> nearby chunks
```

These are search patterns, not strategies. Workflow skills decide which
pattern to use and when to stop.
