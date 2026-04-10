# wikify_simple -- open questions

Updated 2026-04-10. Items resolved since the original slices 0-5 document
are marked [RESOLVED].

## [RESOLVED] 1. Parsers are stubs

Only `markdown.py` is functional. PDF/DOCX/PPTX/HTML parsers raise
NotImplementedError. The metadata helpers in `ingest/metadata.py` are
ported and ready; the parser bodies are not.

Status: still open but not blocking. The mvp20 corpus is markdown.

## [RESOLVED] 2. Embedder

Was hash-based. Now uses `sentence_transformers` via
`WIKIFY_SIMPLE_EMBEDDER=sentence_transformers` env var. The single
replacement point is `infra/embedding.py`. Eval and ingest share the
same embedder callable.

## [RESOLVED] 3. Agent strategy loop

The staged pipeline (`--phase extract|write`) is the solution. Python
handles data prep; Claude Code orchestrates model calls between phases
using subagents. No agent-init/agent-finalize verbs needed.

## [RESOLVED] 4. paths.py

Kept. Every module uses it. Added `write_requests_dir` for staged pipeline.

## [RESOLVED] 5. No SQLite/ChromaDB

Confirmed. Files on disk only. No change needed.

## [RESOLVED] 6. Speed problem

Root cause: double-polling in file dispatch (pipeline polls 250ms +
drain polls 2-3s). Fix: inline heuristic binding (~3s) + staged pipeline
with subagent writing (~5min). The claude_code binding still exists but
is no longer the recommended path.

## [RESOLVED] 7. Prompt templates

Moved to `prompts/*.yaml` with name/role/schema/template fields.
Registry loads them once at startup.

## [RESOLVED] 8. Wiki index

Landed. `_index.json` + `_index.md` per bundle. Rebuildable.

## [RESOLVED] 9. Query mode

Landed. `wikify-simple query --bundle <dir> "question"` with fake and
claude_code bindings. Results at `data/queries/`.

## Open: strategy grid sweep

The sampler parameter space is `jump_rate x global_op` (13 cells).
Need a sweep harness and comparison table. See HANDOFF.md.

## Open: model-backed extraction

Heuristic extraction finds concepts via regex but produces no
definitions, summaries, or parameters. Staged extraction with haiku
subagents would produce rich dossiers. Requires serializing
ExtractRequests the same way WriteRequests are serialized.

## Open: adaptive schedule tuning

The novelty threshold (0.05) and shift target (0.7) in
AdaptiveSchedule are untested. The grid sweep should include
schedule variants.

## Open: corpus scaling

20 papers don't differentiate strategies. Need 50+ documents.
