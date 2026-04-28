# Corpus CLI Audit

Branch: `cli/corpus-search-audit`
Reference corpus: `data/corpora/ald_all_marker` (208 docs, ~9.5k chunks)
Profiler: `scripts/profile_corpus_cli.py`

## Method

Impersonate a researcher writing wiki pages from this corpus. Five
exploration rounds (ALD foundations, RRAM, author graph, HfO2 cross-cut,
stress-test). Profile every call. Record CLI bugs, ergonomic friction,
and ingestion/citation issues separately. Address CLI/skill issues in
batches. Ingestion issues only get noted, not fixed.

## Floor / cold cost

| Call                                | s       | stdout | stderr | notes                                  |
|-------------------------------------|---------|--------|--------|----------------------------------------|
| `wikify --help`                     | 1.24    | 2.0KB  | 0      | Python+Typer startup floor             |
| `wikify corpus schema`              | 1.16    | 1.4KB  | 0      | Floor; pure dict print                 |
| `wikify corpus check`               | 1.62    | 168B   | 0      | + manifest + chunks + field detect     |
| `wikify corpus list docs`           | 1.26    | 22KB   | 0      | 208 long-form ids — heavy              |
| `wikify corpus find --seed --max 10`| 4.40    | 180B   | 0      | + seed select (KG + embeddings)        |
| `wikify corpus find … --by paper --rank citation_count` (no query) | 1.87 | small | 0 | Cheap, no embed                |
| `wikify corpus find "ALD" --top-k 8`| 4.80    | 128B   | 136B*  | + embedder load + vector search        |
| `wikify corpus find "ALD" --text`   | 2.03    | 151B   | 0      | + linear chunk grep                    |
| `wikify corpus find … --by author`  | 4.75    | small  | 136B*  | embed + KG                              |
| `wikify corpus traverse <doc> --to authors` | 1.85 | 13B | 0   | + KG load                              |
| `wikify corpus traverse <doc> --to cited-by --rank citation_count` | 1.87 | 180B | 0 | + KG load |
| `wikify corpus traverse <chunk> --to cited-in-corpus` | 2.37 | varies | 0 | KG loaded twice (chunk + ref)   |
| `wikify corpus show doc:<short>`    | 1.36    | 158B   | 0      | + load docs index                      |
| `wikify corpus show author:<key>`   | 2.48    | 393B   | 0      | + KG (heavy)                            |

*stderr=136B = `[embed] model=…` + `[embed] health check OK` banners — silenced in Batch 1.

Take-aways:
- Floor is **~1.2s** (Python + Typer). Every CLI call pays this. The
  REPL mitigates this by keeping state warm; one-shot CLI cannot.
- **KG load adds ~0.7s** on top of floor. Many helpers re-load it
  redundantly within one command (e.g. `_emit_paper_rows` calls
  `doc_metrics()` after the search already loaded the KG).
- **Embedder warm cost is ~3.6s**. Probably mostly fastembed/onnx
  weight load — repeated across one-shot calls.

## CLI / skill issues (in scope)

### Batch 1 — landed

- **#1 Embedder banners** `[embed] model=…` + `[embed] health check OK`
  printed to stderr on every semantic call (~136B / 2 lines of context
  noise per call). **Fixed**: gated behind `WIKIFY_EMBED_VERBOSE=1`.
  Errors and the silent-CPU `RuntimeError` still always raise.
- **#2 `--format` validation inconsistent** — `corpus find/traverse`
  raised an unhandled `ValueError` and surfaced a Python traceback
  on `--format ndjson`; `corpus schema/check/list` silently fell
  through to text. **Fixed**: `_resolve_format_or_error` /
  `_resolve_simple_format` produce a clean `bad_format` envelope.
  Same wrap applied to `wiki` CLI.
- **#3 Stale `table` format** advertised in schema, accepted by
  `_format.py`, never implemented (fell through to compact silently).
  **Fixed**: dropped from `VALID_FORMATS` and from
  `_CORPUS_SCHEMA["formats"]`.
- **#4 `WIKIFY_CLI_FORMAT` env override** added so agents can force
  `compact` globally without per-call flag (`auto` checks env before
  TTY detection). Pipes still get `quiet` only when `auto` and stdout
  is a pipe and env is unset.

### Batch 2 — planned

- **#5 `list docs` emits 22KB of long-form ids** for 208 docs. Skill
  contract is "12-hex short handles"; this command violates it. Default
  to short handles, add `--long` for full ids, drop output ~5x.
- **#6 `show doc/chunk/figure/equation/author`** prints the long
  internal id as `id:` — agents copy that and re-use, but the canonical
  re-usable form is `doc:<short>`. Print `id: doc:<short>` (the handle
  with kind prefix) by default; long id only with `--long`.
- **#7 Loose tier-4 suffix matcher** in `handles.resolve` matches any
  candidate ending with the bare short string. `corpus show doc:5`
  ambiguity-matches every id ending in "5" (most of them). Drop tier-4
  (tier-3 `_<short>` already handles the canonical case; tests confirm).
- **#8 `parse_handle` error hint** suggests `'doc:paper_A'` /
  `'chunk:paper_A__c0001'` (test-fixture form). Update to suggest
  real-corpus form `'doc:<short-hex>'`.

### Batch 3 — landed

- **#9 `cited-in-corpus` silent-zero hint**: when markers parse but
  resolve to 0 in-corpus refs, `traverse <chunk> --to cited-in-corpus`
  now writes a stderr hint with the parsed ords. Suppress with
  `WIKIFY_QUIET=1`.
- **#10 Author search column rename**: `find --by author "<query>"`
  now emits `n_match=N` (per-query match count) instead of overloading
  `n_papers=`. Metric-only mode keeps `n_papers=` (author total). The
  JSON shape adds an explicit `n_match` field in search mode while
  retaining `n_papers` for back-compat.
- **#12 Skill auto-format guidance**: SKILL/cli-patterns now teach
  `export WIKIFY_CLI_FORMAT=compact` at session start; auto resolution
  consults the env var before TTY detection.
- **#14 Windows `\r\n` pipe bug** (NEW; surfaced during round 2): the
  documented `traverse … --format quiet | xargs traverse …` pattern
  failed on Windows because Python's text-mode stdout writes `\r\n`,
  xargs strips `\n` only, leaving `<handle>\r` which fails resolution.
  Force `newline=""` on the CLI's UTF-8 stdio reconfig + strip
  whitespace in `parse_handle`. Multi-hop pipes now work cross-platform.
- **#15 `--top-k` validation** (NEW): `--top-k 0` silently returned
  empty results; `--top-k -1` returned 3 (Python slice quirk). Reject
  both via `_validate_positive_int` with `bad_int` envelope.
- **Cosmetic**: `traverse --explain` now prints the actual fluent
  chain (`kg.source('X').cited_by() -> top(...)`) instead of a
  string-concat approximation.

### Batch 4 — deferred

- **#11 `--format json` long-id redundancy** — defer; depends on
  callers (eval pipeline, render). Touch in a follow-up that audits
  every JSON consumer.
- **#13 `cited-in-corpus` double KG load** — defer; meaningful win
  needs a long-lived KG cache (the REPL has it; one-shot CLI doesn't).
  That's a bigger architectural change.

## Ingestion / citation issues (out of scope; deferred)

- **I-1 Word-Document title leak** — many docs have `title: Word
  Document` even though the slug carries `[1971 Chua] Memristor-…`.
  Marker/docx extraction sets the metadata title to the docx
  document-properties title which is the literal string "Word
  Document". Either fall back to slug or drop the title field when it's
  the `Word Document` sentinel.
- **I-2 Empty captions for docx figures** — `[1971 Chua]` paper has 16
  figures with empty captions and `page=?` (no page metadata).
- **I-3 Garbage equations** — Chua paper has 64 "equations" with
  `kind=unicode` and content like `HI = d(E1)`, `H = J + f8f`, `n=O`
  — all OCR/extraction noise. Equation indexing pulls `unicode`
  fragments that aren't equations.
- **I-4 `cited-in-corpus` resolution gap** — chunks with valid markers
  like `[52–54]` resolve to zero in-corpus refs, even when the parent
  doc has 80 known references. Either the per-ord index is missing or
  the `references(ords=…)` lookup is broken. Worth a separate
  investigation pass.
- **I-5 Equation `kind` schema mismatch** — schema documents kinds as
  `math|chem|named` but actual data emits `unicode` (and probably
  more). Either ingestion needs to map down, or the schema needs to
  expand the enum.
- **I-6 Author over-aggregation suspicion** — Chua appears with
  `n_papers=1` (correct for this corpus) but `cites=23` (in-corpus
  inbound count from his single 1971 paper). That's high but plausible
  for a foundational paper. No bug, but worth verifying author key
  normalization handles "L. O. Chua" vs "Chua" vs "Leon Chua".

## Profiling log

See repeated runs in audit walkthrough; raw lines emitted by
`scripts/profile_corpus_cli.py` (one JSON per call). Not retained as a
separate file — measurements summarised in the table above.

## Skill changes — landed

- `wikify-search-corpus/SKILL.md`: Step 1 teaches both
  `WIKIFY_CORPUS` and `WIKIFY_CLI_FORMAT=compact` at session start.
  Capability surface paragraph documents the new env-resolution order.
  SKILL trimmed back under the 200-line ceiling by moving worked
  examples and the env-var table into `references/corpus-cli-patterns.md`.
- `references/corpus-cli-patterns.md`: explicit env-var table,
  worked examples, `n_match=` vs `n_papers=` column documentation,
  bad-format envelope note, `--top-k` validation note.
