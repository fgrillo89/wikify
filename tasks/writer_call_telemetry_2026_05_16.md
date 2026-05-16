# Writer-agent call telemetry automation (2026-05-16)

The baseline-skill instructions say: "After each Task returns token usage,
run `wikify run record-call ...`". The PR #72 smoke run shipped
`calls = 0`, `haiku_equivalent_tokens = 0.0` in `eval.json/telemetry`
because nothing wired the Agent-tool result's `total_tokens` back to
`wikify run record-call`. That makes the skill's M5 / cost-curve claim
unverifiable and silently breaks the strategy-vs-cost comparison the
project's `CLAUDE.md` "Current Focus" calls out.

## Options to evaluate

(a) **Subagent emits a structured tail line.** The writer prompt asks
   each subagent to print a final line like
   `WIKIFY_CALL {"tokens_in": ..., "tokens_out": ..., "model_id": "..."}`.
   The orchestrator parses the tail line and runs
   `wikify run record-call --model-id ... --tokens-in ... --tokens-out ...`.
   Cheap, but depends on the subagent obeying the contract; one missed
   tail line = one missed call in telemetry.

(b) **`wikify run record-call` reads from `response.json`.** Add a
   `--from-response <path>` flag that reads `tokens_in` / `tokens_out`
   directly from the saved response.json. The writer skill already writes
   response.json with tokens recorded (or should -- check). The
   orchestrator just runs `wikify run record-call --from-response
   bundles/.../draft/<slug>/response.json` after each Task returns. No
   tail-line contract.

(c) **Harness boundary.** The Claude Code harness exposes per-Task token
   usage to the parent agent. The parent could wire this into a hook that
   shells out to `wikify run record-call`. Bypasses subagent cooperation
   entirely but couples to harness internals.

(b) is the most robust under happy-path conditions and the cheapest to
ship. (a) and (c) are useful fallbacks for runs without a saved
response.json (dry-run or refinement flows that bypass disk).

## Acceptance check

End-to-end: after a clean smoke run on a fresh bundle, `eval.json` must
report `telemetry.calls == n_writer_subagents_launched` and
`telemetry.haiku_equivalent_tokens > 0`. Use the PR #72 smoke bundle
(`bundles/baseline_pr72_smoke_2026_05_16/`) as the comparison baseline.
