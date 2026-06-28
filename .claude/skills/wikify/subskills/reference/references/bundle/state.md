# Bundle State

Important state surfaces:

- `run/state.json`: run id, corpus path, strategy, status, budget, and
  stage status.
- `run/events.jsonl`: append-only event ledger.
- `work/index.md`: regenerated dashboard over concept state.
- `work/concepts/<slug>/work.md`: concept control card.
- `work/concepts/<slug>/evidence.jsonl`: active/archived evidence.
- `wiki/articles/*.md` and `wiki/people/*.md`: committed pages.

Concept statuses are workflow-facing. Workflows decide readiness and
stop conditions; Python validates and persists state transitions.
