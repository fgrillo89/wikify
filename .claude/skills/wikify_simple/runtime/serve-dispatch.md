---
name: wikify_simple/distill
description: Run the agent strategy by orchestrating extract / write / orchestrate skills against a corpus.
---

# distill (agent strategy)

This is the only strategy that has no Python file. It is realised by
the outer Claude Code session running this skill, which in turn
invokes `extract`, `write`, and `orchestrate` skills against the
wikify_simple harness in agent mode.

## The wiki index is load-bearing for the agent loop

Every bundle has a `_index.json` file that mirrors the on-disk pages.
The orchestrator reads it to plan the next action; the writer reads
it to find neighbour titles for the context envelope; the extractor
reads it to see what canonical titles already exist (so dedup-after-
extract is free). **Treat the index as the primary read surface for
the wiki.** Never walk `concepts/*.md` or `people/*.md` directly when
planning — that's an O(n) operation the index makes O(1).

The harness rewrites `_index.json` after every batch of pages
written, so a freshly-read index is always coherent with what's on
disk.

## Steps

1. Make sure the corpus has been ingested via `wikify-simple ingest`.
2. Start the harness in agent mode:
   `wikify-simple distill --strategy agent --binding claude_code --budget 1x --seed 0`
   (The harness will block on dispatch files under `data/dispatch/`.
   It writes the initial `_index.json` for the bundle before yielding.)
3. Loop: poll `data/dispatch/orchestrate/`, `data/dispatch/extract/`,
   and `data/dispatch/write/` for new request files. For each:
     - read the request,
     - **for orchestrate requests, read `index_path` first** so the
       Task subagent has the current wiki state in its prompt,
     - invoke the matching skill (`/wikify_simple/extract`,
       `/wikify_simple/write`, `/wikify_simple/orchestrate`),
     - write the response file next to the request.
4. The harness terminates the loop when the orchestrator returns
   `{"name": "done"}` or the cost meter aborts.
5. The bundle is written under `data/wikis/agent_*` with a final
   `_index.json` reflecting the complete wiki.

There is no judgment in this skill. The orchestrator decides every
action; the writer/extractor skills are mechanical adapters; the
harness keeps every budget invariant; the index is the shared
runtime view of the wiki.
