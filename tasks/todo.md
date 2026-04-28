# CLI improvements (rolling)

## Round 2 plan: simplify, then expose authors

### Phase A — CLI simplification (ship first)

A1. **Default corpus inference**
- [ ] `--corpus` becomes optional. Resolution order: explicit flag > `WIKIFY_CORPUS` env > walk up from cwd looking for `manifest.json` + `docs/` + `chunks/` > error with all three options listed.
- [ ] Apply to `corpus check`, `corpus list`, `corpus find`, `corpus show`, `corpus traverse`, `corpus repl`. (Not `corpus build`, `corpus refresh` — those have a positional source dir.)
- [ ] Tests: explicit > env > cwd-detect; missing-everywhere produces a helpful error.

A2. **`wikify corpus schema`**
- [ ] Prints node types, edge kinds, traverse relations grouped by source-handle kind, rank metrics, handle formats.
- [ ] `--format text|json`. Self-describing — agent runs once and has the grammar.
- [ ] Mirror as `wikify wiki schema` with the wiki-side counterparts.

A3. **`--explain` flag**
- [ ] On `corpus find`, `corpus traverse` (and `wiki find`/`wiki traverse`).
- [ ] Prints a one-line fluent-chain pseudocode (e.g. `kg.chunks().search('X', top_k=40).group_by_doc().top(8, by=citation_count)`) plus the resolved config (paths, top_k, rank). Exits 0 without executing.

### Phase B — Authors graph

B1. **Author handle and `show`**
- [ ] `corpus show author:<key>` — name, h_index, citation_count, n_papers, top coauthors.
- [ ] Author keys are lowercase `"first last"`; CLI accepts case-insensitive prefix when unique.

B2. **Author traversals**
- [ ] `corpus traverse author:<key> --to sources` — all papers by this author.
- [ ] `corpus traverse author:<key> --to coauthors` — co-authors (with rank by `h_index` or `citation_count`).
- [ ] `corpus traverse doc:<short> --to authors` — authors of a paper.
- [ ] Optional `--rank h_index|citation_count|n_papers` on author-typed results.

B3. **Author search and ranking**
- [ ] `corpus find --by author --rank h_index|citation_count|n_papers --top-k N` for top authors in the corpus, no query needed.
- [ ] `corpus find "<topic>" --by author` — authors whose papers' chunks match the query, ranked by best chunk score (semantic).

B4. **The user's two-hop query, documented as a pipe pattern**

```bash
# Papers by authors who cite this paper:
wikify corpus traverse doc:<short> --to cited-by --format quiet \
  | xargs -I {} wikify corpus traverse {} --to authors --format quiet \
  | sort -u \
  | xargs -I {} wikify corpus traverse {} --to sources --format quiet \
  | sort -u
```

No new flags. Just relations + composition. Skill docs document the recipe.

### Out of scope for this round (Tier 3)

Neighborhood / N-hop traversal, co-citation, REPL stateful subject,
hybrid scoring, `--match`/`--since` filters. Carry forward.

### Round 2 — review

Phase A and Phase B both shipped in one pass. 786 tests pass, ruff clean.

**Phase A**:
- `_resolve_corpus` chains explicit flag → `WIKIFY_CORPUS` → cwd walk-up.
  Eight read commands now have optional `--corpus`. `_resolve_bundle`
  on the wiki side gained `WIKIFY_BUNDLE`.
- New `corpus schema` and `wiki schema` print the full surface (nodes,
  edges, relations, metrics, formats, handle rules).
- `--explain` on `corpus find` and `corpus traverse` prints the
  fluent-chain pseudocode without executing.

**Phase B**:
- Author handle `author:first_last` (spaces → underscores at boundary).
  Resolver accepts case-insensitive unique prefix.
- `corpus show author:` prints h_index, citation_count, n_papers, and
  top 5 coauthors.
- `corpus traverse author:<key> --to sources|coauthors`.
- `corpus traverse doc:<short> --to authors`.
- `corpus find --by author` — top authors by metric (no query) or by
  topic (with query).
- New rank metrics: `h_index`, `n_papers` for author-typed results.
- Fluent: extended `traverse_doc` (added `authors`), added
  `traverse_author`, extended `_materialize_traversal` for author rows.

**Verification on `ald_all_marker`**:

```
$ wikify corpus find --by author --rank h_index --top-k 5
h=6  cites=168  n_papers=14  author:sungjun_kim
h=5  cites=93   n_papers=8   author:chandreswar_mahata
h=5  cites=92   n_papers=7   author:muhammad_ismail
...

$ wikify corpus traverse author:sungjun_kim --to coauthors --rank h_index --top-k 3
h=5  cites=93  n_papers=8  author:chandreswar_mahata
h=5  cites=92  n_papers=7  author:muhammad_ismail
h=3  cites=30  n_papers=5  author:dongyeol_ju

$ # Three-hop pipeline: papers by authors who cite this paper
$ wikify corpus traverse doc:bdead88c7417 --to cited-by --format quiet \
  | xargs ... --to authors --format quiet | sort -u \
  | xargs ... --to sources --format quiet | sort -u | head
# Returns 5+ distinct papers from authors in the citation neighborhood.
```

**Carry forward (Tier 3)**: section-scoped chunks (`traverse doc --to
sections --type methods`), per-handle stats in `show doc:`,
neighborhood/N-hop, REPL stateful subject, hybrid scoring with RRF,
`--match` / `--since` filters.

---

# Prior round (shipped): short handles, citation-aware ranking, traversal

Goal: make the CLI directly answer questions like "most cited paper that talks about
ALD" and support recursive exploration ("find chunk → who cites that paper → ..."),
without quoting long IDs or chaining hand-written shell glue.

## Scope (Tier 1 — ship in this pass)

### 1. Short handles (corpus + wiki)
- [ ] `corpus.queries.parse_handle` accepts `doc:<full-id>`, `doc:<12-char-hash>`, and `doc:<unique-suffix>`. Same for `chunk:`.
- [ ] If the suffix is ambiguous (matches >1), exit with a clear error listing the candidates.
- [ ] Handles emitted by the CLI use the **short hash** form by default. `--long` flag on `show` for the full id.
- [ ] Wiki side: `wiki.queries.show_page` accepts page slug exact match (already does) AND case-insensitive prefix match if unique. Wiki pages have natural-title IDs (no hash), so the analogue is "unique prefix".

### 2. UTF-8 stdout (corpus + wiki + repl)
- [ ] At CLI entry point, force `sys.stdout.reconfigure(encoding="utf-8")` so titles with `‐` (unicode hyphen) etc. don't crash on Windows cp1252.
- [ ] Repro: `wikify corpus find --seed --pagerank-weight 1.0 --max 30 --corpus data/corpora/ald_all_marker` currently crashes mid-stream on hyphen.

### 3. Output formats with TTY-aware default
- [ ] `--format quiet|compact|table|json` on `corpus find`, `corpus list docs`, `corpus list chunks`, `wiki find`, `wiki list`.
- [ ] `quiet`: one short handle per line, nothing else. Pipe-safe.
- [ ] `compact` (current default for TTY): tab-separated `score \t cites=N \t handle \t title` for chunks/docs.
- [ ] `table`: aligned columns with header (Rich table when TTY).
- [ ] `json`: existing JSON shape.
- [ ] Auto-detect: if `not sys.stdout.isatty()` and `--format` not given, default to `quiet`.

### 4. `corpus find` ranking and granularity
- [ ] `--by chunk|paper` (default: `chunk` to preserve current behavior).
- [ ] `--rank semantic|citation_count|pagerank` (default: `semantic`).
- [ ] When `--by paper`, semantic ranking uses the existing `find-papers` aggregation (REPL helper promoted to a `queries.search_papers_*` function the CLI calls).
- [ ] Default chunk output gains a `cites=N` column (citation count of the parent doc).
- [ ] Doc/paper output emits `cites=N pr=X.XXXX handle title` columns (and `score` when semantic).

### 5. `corpus traverse` (new subcommand)
- [ ] `wikify corpus traverse <handle> --to <relation> [--rank ...] [--top-k N] [--corpus <c>]`
- [ ] Relations supported in v1: `cited-by`, `references`, `source` (chunk→doc), `cited-in-corpus` (chunk→docs cited by chunk's text markers).
- [ ] Output is handles, one per line. Default `--format quiet` when piped.
- [ ] New fluent method `QueryBuilder.cited_sources_in_corpus()` on chunk-typed sets. Implementation: parse markers from each chunk's text → resolve via `_ord_refs` of the chunk's owning source.

### 6. `wiki traverse` (mirror)
- [ ] `wikify wiki traverse <page-handle> --to <relation> [--top-k N]`
- [ ] Relations in v1: `links` (LINKS_TO outgoing), `linked-by` (incoming), `co-evidence` (CO_EVIDENCE), `evidence` (HAS_EVIDENCE → corpus chunk handles, bridges back to corpus).
- [ ] `--rank n_links|n_evidence` (top by attribute, since wiki has no PageRank/citation counts).
- [ ] Output: page handles for page targets; corpus chunk handles for `evidence`. Quiet mode pipes cleanly into `wikify corpus show`.

### 7. Round-trip invariant test
- [ ] One pytest: for the `ald_all_marker` corpus, every handle emitted by `corpus find` (in both `compact` and `quiet` modes) is accepted by `corpus show`. Same for `corpus traverse`.
- [ ] One pytest mirror on wiki side using the warm-corpus / warm-wiki fixture (whatever's available).

### 8. Skill doc updates
- [ ] `.claude/skills/wikify-search-corpus/SKILL.md`: add `traverse` to capability surface, add the recursive recipe.
- [ ] `.claude/skills/wikify-search-corpus/references/corpus-cli-patterns.md`: document `--by`, `--rank`, `--format`, `traverse`, short handles.
- [ ] `.claude/skills/wikify-search-corpus/references/corpus-recursive-search.md`: rewrite the recursive examples to use `traverse` + short handles + pipes.
- [ ] `.claude/skills/wikify-search-wiki/SKILL.md` + references: mirror the same shape.

## Out of scope (Tier 2 — explicit follow-ups, not now)

- `--match field=val`, `--since YEAR`, `--where k=v` filter flags on `find`.
- `traverse` relations: `authors`, `coauthors`, `sections`, `figures`, `equations`, `nearby-figures`, `nearby-equations`, `neighborhood --hops N`.
- REPL stateful subject (`back`, `save`, implicit current set).
- `corpus show doc:` surfacing citation count and citing-doc list (currently shows year + author count only).
- Hybrid scoring (`--rank hybrid` with RRF).

## Verification

After implementation:

```bash
# The original question — should be one line, no piping:
uv run wikify corpus find "atomic layer deposition" \
    --by paper --rank citation_count --top-k 3 \
    --corpus data/corpora/ald_all_marker

# Recursive flow:
uv run wikify corpus find "atomic layer deposition" --by paper --top-k 1 --quiet \
    --corpus data/corpora/ald_all_marker \
  | xargs -I {} uv run wikify corpus traverse {} --to cited-by \
        --rank citation_count --top-k 5 --corpus data/corpora/ald_all_marker

# In-corpus citations from a chunk:
uv run wikify corpus traverse chunk:<short-hash> --to cited-in-corpus \
    --rank citation_count --top-k 5 --corpus data/corpora/ald_all_marker
```

Plus:
- `uv run ruff check src/wikify tests/wikify`
- `uv run pytest tests/wikify -q`

## Review

Shipped Tier 1 in one pass.

**New code**
- `src/wikify/corpus/handles.py` — short-handle resolution: `short_id`,
  `resolve` (4-tier: exact, hash, underscore-suffix, loose-suffix),
  `format_handle`, `AmbiguousHandleError`.
- `src/wikify/cli/_format.py` — `resolve_format(auto|quiet|compact|table|json)`
  with TTY auto-detect; `format_row` for tab-separated output.

**Modified**
- `src/wikify/cli/__init__.py` — `_force_utf8_stdio()` reconfigures
  stdout/stderr to UTF-8 so non-ASCII titles don't crash on Windows
  cp1252.
- `src/wikify/corpus/queries.py` — `get_doc`/`get_chunk` resolve short
  handles; new `resolve_doc_id`, `resolve_chunk_id`, `search_papers`,
  `rank_docs`, `doc_metrics`, `traverse_doc`, `traverse_chunk`. The
  metrics helper falls back to zeros when the corpus has no graph
  (test fixtures).
- `src/wikify/corpus/graph.py` — `_MARKER_RE` and `parse_citation_markers`
  now accept the unicode dash family (en-dash, em-dash, etc.) so
  `[52–54]` parses. Pre-existing bug surfaced by `cited-in-corpus`.
- `src/wikify/cli/corpus.py` — `cmd_show` uses resolver + reports
  ambiguous matches. `cmd_find` grew `--by chunk|paper`,
  `--rank semantic|citation_count|pagerank`, `--format auto|quiet|...`,
  pool-widening when re-ranking. New `cmd_traverse` with `--to <relation>`
  for `cited-by | references | chunks | source | cited-in-corpus`.
- `src/wikify/bundle/wiki/queries.py` — `resolve_slug` (exact + unique
  prefix), `traverse_page` for `links | linked-by | co-evidence | evidence`,
  `AmbiguousSlugError`.
- `src/wikify/cli/wiki.py` — `wiki show` strips `page:` prefix and
  reports ambiguous slugs. `wiki find` gained `--format quiet|compact|json`.
  New `wiki traverse` mirrors corpus traverse; `evidence` relation emits
  `chunk:` handles for piping into corpus.

**Tests**
- `tests/wikify/test_corpus_handles.py` — 11 tests for resolution.
- `tests/wikify/test_cli_corpus.py` — 7 added: short handle, unique
  suffix, quiet mode, compact mode `cites=` column, find→show
  round-trip, traverse `chunk → source`, find `--by paper --rank
  citation_count`.
- Full suite: 779 passed. Ruff clean.

**Verification against `data/corpora/ald_all_marker`** (208 docs, 4985 chunks)

```
$ wikify corpus find "atomic layer deposition" --by paper --rank citation_count --top-k 3 --format compact
0.818  cites=41  n=2  doc:bdead88c7417  Memristive Device Characteristics Engineering...
0.861  cites=32  n=1  doc:0a8b39976e69  Study of MoS2 high-k Interface...
0.843  cites=21  n=2  doc:ce66c4722ebc  Enhanced Linearity in CBRAM Synapse...

$ ... --top-k 1 --format quiet | xargs -I {} wikify corpus traverse {} --to cited-by --rank citation_count --top-k 5 --format compact
cites=7   pr=0.0021  doc:f30a5a8fad06  Toward Advancement of Fabrication Techniques...
cites=7   pr=0.0019  doc:0acca5129f9a  Enhanced synaptic properties in HfO2-based...
[...]

$ wikify corpus traverse chunk:ade5e4d2 --to cited-in-corpus --rank citation_count --format compact
cites=5   pr=0.0026  doc:b4ca2c2967a9  Word Document
```

All three Tier 1 verification scenarios pass. The original "most cited
paper that talks about ALD" question is now a single CLI call.

**Out-of-scope items remain Tier 2**: `--match`, `--since`, additional
traverse relations (authors, sections, figures, equations,
neighborhood), REPL stateful subject, `show doc:` surfacing citations,
hybrid scoring.

## Known issues surfaced during verification

- **`Word Document` titles in `ald_all_marker`**. Several docs in this
  corpus have `title == "Word Document"` because the parser fell back
  to the file's default `<title>` element instead of extracting the
  paper's actual title from the body. Surfaced via the cited-in-corpus
  verification (e.g. `doc:ee0bfcb003d3`, `doc:9be21f6faf92`,
  `doc:02adb0aae0ec`). Fix on the **ingest** side, not the CLI:
  - Ingest should reject `Word Document` (and the analogous
    `Microsoft Word - <filename>.doc` strings) as titles and fall back
    to the first heading in the body, the bibliographic citation, or a
    final last-resort that does NOT use the docx default title.
  - Add a `corpus check` warning that flags any doc whose title is one
    of a small blocklist of known-bad defaults, so future re-parses
    catch this without ad-hoc inspection.
  - Until that lands, downstream CLI consumers can detect the failure
    by checking `title` against the blocklist; nothing in the new
    `find`/`traverse` plumbing depends on title quality.
