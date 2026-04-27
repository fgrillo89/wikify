# Corpus CLI Patterns

The corpus is the authoritative evidence layer. During a wiki run,
corpus access is read-only unless a workflow explicitly runs ingest or
refresh outside the bundle.

## Common Commands

```bash
wikify corpus check <corpus> [--format text|json]
wikify corpus list docs   --corpus <corpus>
wikify corpus list chunks --corpus <corpus> [--doc <doc-id>]
wikify corpus list files  --corpus <corpus>
wikify corpus find "<query>" --corpus <corpus> [--top-k N] [--format text|json]
wikify corpus find "<query>" --corpus <corpus> --text
wikify corpus find --seed --corpus <corpus> [--max N] [--pagerank-weight W]
wikify corpus show doc:<doc-id> --corpus <corpus> [--full]
wikify corpus show chunk:<chunk-id> --corpus <corpus> [--full]
wikify corpus repl --corpus <corpus>
```

Use `--format json` only when another deterministic tool must parse the
output. Prefer terse text for agent inspection.

## Interactive Session

Use `wikify corpus repl --corpus <corpus>` when a workflow needs many
iterative corpus queries. The process keeps docs/chunks indexed and
loads the semantic embedder only once after the first semantic `find`.

```text
find atomic layer deposition HfO2 memristor top=10
find-papers atomic layer deposition HfO2 memristor top=10
find --text "atomic layer deposition" top=20
show chunk:<chunk-id> full
list docs
seed max=20
exit
```

`find` returns chunks. `find-papers` groups the best matching chunks by
paper and returns `best_score`, match count, `doc_id`, and
`best_chunk_id`. Use it when the workflow needs the most relevant full
paper for a concept before drilling into chunks.

## Query Shapes

- Concept query: subject name, alias, method, material, device, person.
- Exact phrase query: acronym, equation label, material formula,
  section heading, quoted term.
- Seed query: `find --seed` to expose central corpus entry points.
- Evidence query: concept title plus a missing aspect, for example
  `"atomic layer deposition temperature window"`.
- Disambiguation query: title plus field or source context.

## Full Text Discipline

Do not open full documents or chunks by default. Use previews to choose
a handle first. Then call `show --full` on the specific selected handle.
