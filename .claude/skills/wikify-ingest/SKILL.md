---
name: wikify-ingest
description: Build or refresh a Wikify corpus from a fresh source directory. Thin composition over `wikify corpus build` / `wikify corpus refresh`. Use when adding a new document tree before any wiki workflow runs against it. Status: stub — composition shape only, no Python orchestration.
allowed-tools: Bash(wikify *)
---

# wikify-ingest (stub)

Composition shape for ingesting a source directory into a corpus.
The strategy decisions documented here live in skill markdown — they
are not silent Python defaults.

## Strategy decisions (override here)

- Source root: passed by the user as `<src>` (positional argument to
  `corpus build`).
- Corpus root: passed by the user as `<c>` (`--out` for `corpus build`,
  positional for `corpus refresh` / `corpus check`).
- Mode: `--mode additive` (default) keeps prior docs; `--mode sync`
  drops docs no longer in `<src>`.
- Refresh policy: `wikify corpus refresh <c>` only after the source
  tree has changed; otherwise `wikify corpus check <c>` is enough.

## Composition (no Python)

```
wikify corpus build <src> --out <c> --mode additive
wikify corpus check <c>
```

If the corpus already exists and the source tree has incremental
changes:

```
wikify corpus refresh <c>
wikify corpus check <c>
```

After ingest, hand off to `wikify-baseline` to grow a wiki against
the corpus, or to `wikify-query` for read-only Q&A.

## What this workflow does NOT do

- It does not write into any wiki bundle.
- It does not call any model.
- It does not select seeds or extract concepts. That is
  `wikify-baseline` step 1-2.

## References

- [atoms.md](../wikify/references/atoms.md) — atom contracts.
- [schemas.md](../wikify/references/schemas.md) — corpus on-disk shape.
