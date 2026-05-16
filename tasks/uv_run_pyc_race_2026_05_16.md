# uv run pyc race -- ModuleNotFoundError flake on Windows (2026-05-16)

## Symptom

During the PR #72 smoke `wikify work build-evidence <slug>` loop, the
first 4 invocations succeeded and the next 11 failed instantly with:

```
ModuleNotFoundError: No module named 'wikify.bundle.wiki.vectors'
```

Re-running the exact same command (no source change) worked again. The
module `src/wikify/bundle/wiki/vectors.py` exists on disk and is imported
without issue when CLI calls are spaced apart.

## Smoking gun

`src/wikify/bundle/wiki/__pycache__/` carries leftover temp files of the
form `chunks.cpython-312.pyc.<long_int>` after the failures. CPython
writes a `.pyc.<random>` next to the target, then renames into place.
On Windows the rename can race with another interpreter writing the same
file, and either rename fails (leaving the temp behind) or the import
machinery sees the temp file mid-flight and the module appears missing.

This is a known interaction between CPython byte-compile writes and the
Windows file-locking semantics when multiple Python processes share a
package directory; `uv run` launches a fresh interpreter per CLI call,
so a tight orchestration loop (the smoke runner issues calls
back-to-back) is the worst case.

## Workarounds to evaluate

- Pin `PYTHONDONTWRITEBYTECODE=1` for orchestrator-driven CLI
  invocations. The orchestrator is short-lived and runs many CLIs in a
  loop, so skipping bytecode caches at orchestration time avoids the
  rename race entirely. Cost: each CLI invocation re-compiles `.py`
  files in-memory.
- Pre-warm a single `uv run` shell once at the top of the orchestrator
  and run repeated CLIs through it (instead of one `uv run` per call).
  Removes most of the cost from the first workaround but requires the
  orchestrator to manage a long-running shell.
- File an upstream issue on `uv` / CPython covering the Windows rename
  race; this is not Wikify-specific.

## Acceptance check

Re-running the smoke loop 30 consecutive times on Windows with the
chosen mitigation must yield zero `ModuleNotFoundError` flakes. Clean
`__pycache__/` first and inspect it after; no `*.pyc.<long_int>` temp
files should remain.

## Related

`reference_gpu_stability.md` (auto-memory): unrelated Windows-driver
class of flakes also affecting this machine. Both should be tracked
when triaging "is the smoke run noise mine or the code's?"
