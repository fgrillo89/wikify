# wikify-investigate profiling — friction & bug log

Run: `data/bundles/ald_investigate_profile_run` over corpus `ald_docling_2026_05_15` (207 docs / 5280 chunks).
Branch: `wikify-investigate-profiling`. Editor=Opus, explorers/writers/data=Sonnet, classifiers=Haiku.

Severity legend: **blocker** (run cannot proceed) / **major** (wrong output or large waste) / **minor** (papercut) / **note** (observation).
Each entry: symptom, where, root-cause hypothesis, severity, proposed fix. `[BUG]` = confirmed defect, `[ROUGH]` = rough edge.

---

## Run-loop frictions

### F1 [BUG] Editor cannot fold notebook `covered_chunks` — no CLI, helper is Python-only
- **Symptom**: `wikify-investigate/SKILL.md` and `wikify-investigate-explore/SKILL.md` instruct the editor to fold each explorer's `covered_chunks_delta` into notebook frontmatter "between Tasks" via the `notebook.merge_covered_chunks` / `append_exploration_log` Python helpers. The editor skill's `allowed-tools` is only `Bash(wikify *)` + MCP + Task — there is **no CLI** that writes notebook provenance (`rg covered.chunks src/wikify/cli` → nothing; only `notebook-init` exists).
- **Where**: `src/wikify/cli/work.py` (missing command); `.claude/skills/wikify-investigate/SKILL.md` (DISPATCH/explore contract); `.claude/skills/wikify-investigate-explore/SKILL.md` ("editor folds ... via the helpers").
- **Root-cause hypothesis**: the contract was written assuming a Python orchestrator; the skill-driven editor has no Python execution path, so the fold step is undoable as documented.
- **Severity**: major (contract gap). Mitigated by `coverage.py:_in_flight_chunk_ids` unioning the active evidence ledger, so `chunk_coverage_ratio` stays correct even with empty `covered_chunks`. But the documented explorer dedup (`seen_chunks` from `notebook.provenance.covered_chunks`) silently no-ops across rounds → repeated re-judging of the same chunks (budget waste).
- **Proposed fix**: add `wikify work notebook merge-covered <slug> --chunks <jsonl> [--log ...]`, OR change the explore skill so `seen_chunks` is seeded from the slug's evidence ledger (already on disk) instead of notebook provenance. Workaround used this run: instruct explorers to seed `seen_chunks` from both the notebook AND the slug's `evidence.jsonl`.

### F2 [BUG] MCP `corpus_find`/`corpus_show` never expose the canonical chunk id the contract demands
- **Symptom**: both the explorer and the data-extractor reported that MCP corpus tools return only the short `chunk:<8hex>` handle. The explore SKILL.md says the evidence `chunk_id` "MUST be the corpus CANONICAL id (the long `id` field ...), never the short `chunk:<hex>` handle" and warns handles "silently zero out coverage and citation grounding when the corpus is not reachable." But there is no MCP field that returns the canonical id, so a subagent literally cannot comply from MCP alone.
- **Where**: `mcp__wikify__corpus_find` / `corpus_show` result rows (no `canonical_id`/`id` field); contract in `.claude/skills/wikify-investigate-explore/SKILL.md` Shared mechanics.
- **Root-cause hypothesis**: MCP result serialization drops the canonical id in favor of the short handle; the contract was written against the CLI/DB view.
- **Severity**: major. Mitigated for `work add evidence` (it resolves short→canonical when the corpus is reachable — it was here, so coverage worked). NOT mitigated for the data path (see F6).
- **Proposed fix**: add a `canonical_id` (or `id`) field to chunk rows in `corpus_find`/`corpus_show` output (with `include_text=True` already returns text; also return the id). Then the contract is followable.

### F3 [ROUGH] Kind detection gives explorers no feedback; `memristor`/`in-memory-computing` silently missed `mechanism`
- **Symptom**: explorer believed every concept's quotes covered definition+mechanism+application, but `work maturity` shows `memristor` kinds=`[application,definition]` and `in-memory-computing`=`[application,definition,limitation]` — both missing `mechanism`. The explorer can't see `_MECHANISM_RE`, so it guessed wrong (its "mechanism" quotes used words like "switching" that the regex doesn't match).
- **Where**: `src/wikify/bundle/work/maturity.py` `_detect_kinds`; evidence-add path.
- **Root-cause hypothesis**: kind detection is a hidden regex; `work add evidence` returns only a count, no per-record kind feedback, so explorers fly blind.
- **Severity**: minor (quality/efficiency). Fix: have `work add evidence` (or `work maturity`) echo detected kinds per slug so the explorer/editor can target the missing kind on the next GROW pass. Worked around by scheduling a GROW pass for the two slugs.

### F4 [NOTE] Agent return payloads >64KB can't be re-read (harness Read limit) — not a wikify bug
- The explorer staged a large JSONL and could not re-read it via Read (token ceiling). Harness limitation; mention only because it pushes subagents toward many small `corpus_find` round-trips. No wikify fix.

### F5 [NOTE] Short chunk handles collide across docs
- **Symptom**: the explorer saw the same `chunk:<hex>` short handle appear under two different doc prefixes. Reinforces F2/F6: the 8-hex short handle is not globally unique, so any code keying on it risks cross-doc collision.
- **Severity**: note (latent). Fix folds into F2 (use canonical id).

### F6 [BUG] `data add` verification rejects short chunk handles — exact-match lookup, no resolution
- **Symptom**: data-extractor found that staging a point whose `chunk_id` is the short MCP handle makes `read_chunks_by_id` exact-match miss → `source_text_for()` returns "" → the number can't be located → point rejected. It had to query SQLite with `LIKE '%<hash>'` to recover the full id. So the same short-handle the MCP tools return is unusable for `data add` grounding.
- **Where**: data verification path (`read_chunks_by_id` / `source_text_for`); `wikify data add`.
- **Root-cause hypothesis**: unlike `work add evidence`, the data gate does not resolve short handles to canonical ids before fetching source text.
- **Severity**: major (silent mass rejection if not worked around). Fix: resolve short→canonical in the data gate (reuse the evidence-add resolver), and/or fix F2 so canonical ids are available upstream.

### F7 [NOTE] OCR control chars (U+0001) in corpus text break naive JSONL grounding quotes
- **Symptom**: chunks from doc:5f92b0389ccd contain U+0001 in place of degree/×/µ signs (Docling OCR artifact). Bare U+0001 in a JSONL quote is a parse error; escaped `` requires exact source match. The gate's whitespace-collapse path saves it, but it is a trap.
- **Severity**: note (corpus data quality). Fix: document the control-char hazard in `wikify-extract-data`; consider normalizing control chars at parse time.

### F8 [BUG] Data gate accepts semantically-wrong number from OCR-mangled `value_original`
- **Symptom**: when `value_original` is OCR-mangled (e.g. `"1 10 5 ohm cm"` for 1e5), `parse_leading_number` extracts `1.0` not `100000.0`, yet verification PASSES because `1 ∈ q_nums ∩ s_nums`. The stored point's numeric value is wrong but verified.
- **Where**: data verification (`parse_leading_number` + number-intersection check).
- **Root-cause hypothesis**: the gate checks "does any number in the value appear in the source," which is too weak for mangled values.
- **Severity**: major (verified-but-wrong data). Fix: require the *leading/primary* number to match, or store an explicit `value_num` and verify that exact value appears.

### F9 [ROUGH] `--keep-rejected` leaves an unpurgeable stale rejected point; no data cleanup command
- **Symptom**: a debug run with `--keep-rejected` stored a rejected point that now permanently shows in `data coverage` (`by_status.rejected=1`); there is no CLI to delete it.
- **Where**: `wikify data` (missing `prune`/`delete`).
- **Severity**: minor. Fix: add `wikify data prune --status rejected` (or `data delete <claim_id>`).

### F10 [NOTE] µ (U+00B5 micro) vs μ (U+03BC Greek mu) mismatch traps extractors
- Copying a visually-similar Greek mu instead of the literal U+00B5 in source breaks the grounding-quote match. Guidance-only; document in `wikify-extract-data`.

### F11 [BUG] `budget.spent_haiku_eq` never reconciles with `cost.totals.haiku_eq`
- **Symptom**: after recording call telemetry, `run show --full` shows `budget.spent_haiku_eq = 0` while `cost.totals.haiku_eq = 991202`. The STOP CHECK condition `budget_haiku_eq >= target` reads `budget.spent_haiku_eq`, which never updates from recorded calls, so the budget stop can never fire.
- **Where**: `src/wikify/bundle/run/` (budget vs cost aggregate); `run show`.
- **Root-cause hypothesis**: `budget.spent_haiku_eq` is a static state field; nothing folds the per-call cost aggregate into it.
- **Severity**: major (budget bound silently inert). Fix: derive `spent_haiku_eq` from `cost.totals.haiku_eq` in `run show`/stop-check, or reconcile the field when `record-call(s)` runs. Workaround this run: editor tracks spend via `cost.totals.haiku_eq`.

### F12 [ROUGH] Canonical chunk id lives in different DBs per subagent — confusing
- **Symptom**: one explorer reported `corpus.sqlite` "has no tables" and resolved canonical ids via the bundle `wikify.db` chunks table; the round-0 data agent resolved via the corpus `corpus.sqlite` with `LIKE`. Subagents had to guess where canonical ids live.
- **Where**: corpus dir has `corpus.db`, `corpus.sqlite`, `store.db` (three SQLite files); unclear which is authoritative for chunk ids.
- **Severity**: minor (folds into F2). Fix: expose canonical id via MCP (F2) so no DB spelunking is needed; document which corpus DB is canonical.

### F13 [ROUGH] No CLI to emit P5 inbox suggestions; subagents hand-write JSONL
- **Symptom**: `wikify work add` exposes only `concept`/`evidence`/`feedback`. The P5 explorer had to append raw JSONL to `work/inbox/{concept,evidence}_suggestions.jsonl` by hand. The schema is undocumented, so drift between writer and `work tend` consumer is likely.
- **Where**: `wikify work add` (missing `suggestion`); `.claude/skills/wikify-investigate-explore/SKILL.md` P5 (`emit_evidence_suggestion`/`emit_concept_suggestion`).
- **Severity**: minor-major. Fix: add `wikify work add suggestion --kind concept|evidence ...` so the inbox schema is enforced by the CLI.

### F14 [BUG, CONFIRMED] `corpus_find(by="chunk", rank="pagerank")` returns DOC-level rows, not chunks
- **Symptom**: the P5 GAP explorer (round 3) confirmed every result of `corpus_find(by="chunk", rank="pagerank", top_k=20)` had `type="doc"` with `meta.n_chunks` — zero chunk handles. The P5 pattern's literal call `corpus_find(query="", by="chunk", rank="pagerank", top_k=budget_chunks)` (per `wikify-investigate-explore/SKILL.md`) therefore cannot rank residual *chunks*; the coverage driver silently degrades to doc-level granularity.
- **Where**: `mcp__wikify__corpus_find` / `corpus` API graph-metric ranking for `by="chunk"`; pattern in `wikify-investigate-explore/SKILL.md` P5.
- **Root-cause hypothesis**: pagerank is a document-graph metric; ranking `by="chunk"` on it falls through to the doc population instead of erroring or projecting pagerank onto chunks.
- **Severity**: major (the primary coverage objective's driver is degraded). Fix: either project doc pagerank onto its chunks for `by="chunk"` ranking, or have P5 rank docs then sample residual chunks within them (and fix the skill to match). At minimum, error loudly instead of silently returning docs.

### F15 [ROUGH] `data add` staging needs the long `doc_id` string, not the `doc:<hash>` handle
- **Symptom**: the data-extractor found that the staging `doc_id` must be the long form, while `work add evidence` accepts handle forms. Inconsistent across the two ingest paths.
- **Severity**: minor. Fix: accept handle forms in `data add` like the evidence path does.

### F16 [NOTE] Property names normalize on ingest (`on/off ratio` -> `on off ratio`)
- The store strips punctuation; specs must reference the normalized spelling (see F22). Note only.

### F17 [ROUGH] `work tend` auto-creates concept cards from P5 `concept_suggestions` with zero evidence
- **Symptom**: after round 1, `work tend` turned 8 P5 concept_suggestions into full concept cards (roster 6 -> 14), all band `new`, no evidence. They inflate `concept_count` (feeds the SEED trigger) and clutter `work maturity --all`.
- **Where**: `wikify work tend` inbox consolidation.
- **Severity**: minor-major. Fix: gate stub creation (require >=1 evidence record or a CURATE confirmation) or keep suggestions in a staging band that does not count toward `concept_count` until evidenced.

### F18 [BUG] Empty `chunk_text` evidence records reach the dossier/draft and get silently dropped
- **Symptom**: every writer hit evidence records whose `chunk_text` was empty (neuromorphic e11-e13, electronic-synapse e11, in-memory e16/e17). The validator can't ground them, so writers dropped those markers — wasted evidence and fewer citations than the dossier advertises.
- **Where**: `draft build` evidence assembly; some chunk ids resolve to empty bodies.
- **Root-cause hypothesis**: certain corpus chunks have empty/whitespace bodies (figure/table/caption residue), or id resolution returned an empty chunk. They should be filtered at `draft build`, not silently passed to the writer.
- **Severity**: major. Fix: `draft build` should drop or flag empty-body evidence and warn, so maturity `n_chunks` and the dossier reflect usable evidence only.

### F19 [BUG] Dossier/draft "Selected quote" text is CLEANED but the validator checks RAW `chunk_text` — every writer wasted iterations
- **Symptom**: ALL five writers independently discovered the dossier's displayed "Selected quote" (and the `quotes` field in `draft.json`) strips inline citation markers (`[1-3]`) and renders OCR control chars as spaces, but `draft check` validates `[^eN]` quotes as VERBATIM substrings of the RAW `chunk_text`. So a writer that trusts the dossier (which the skill calls "the substrate, not a hint") fails grounding and must re-extract quotes programmatically from raw `chunk_text` in `draft.json`. Cost: 1-2 extra validation iterations per page, every page.
- **Where**: `draft build` dossier rendering vs `draft check` grounding; `wikify-write-page/SKILL.md` (tells the writer to trust the dossier).
- **Root-cause hypothesis**: the dossier renderer cleans text for readability; the validator did not get the same normalization, so "what the writer sees" != "what the validator checks."
- **Severity**: major (systemic; the single biggest writer-side time sink). Fix: validate quotes against the SAME normalized text shown in the dossier (apply the dossier's cleaning to both sides), OR show raw quotable spans in the dossier, OR expose a per-marker "verbatim quotable span" the writer can copy safely.

### F20 [ROUGH] `chunk_text` truncated to ~503 chars in CLI output; full text only in `draft.json`
- Writers needing a quote past 503 chars must open `draft.json`. Minor (folds into F19). Fix: note in the writer skill, or stop truncating in the dossier.

### F21 [ROUGH] `figure_id` must match the draft figures list byte-for-byte (title prefix + Unicode hyphens)
- **Symptom**: writers wrote ASCII hyphens / short labels for `figure_id`; the validator requires the exact draft string including U+2010 non-breaking hyphens and the full title prefix. Caused failed validations.
- **Severity**: minor-major. Fix: match `figure_id` by a normalized/looser key, or surface the exact copy-pasteable id prominently in the dossier figure-candidates section.

### F22 [BUG] `data consolidate` silently yields empty columns when spec property names don't match normalized store spelling
- **Symptom**: spec properties are matched against `normalized_property_norm`; a spec listing `on/off ratio` instead of `on off ratio` produces an EMPTY column with no error.
- **Where**: `wikify data consolidate`.
- **Severity**: major (silent data loss in the artifact). Fix: warn/error when a spec property matches zero stored points; echo the available normalized property names.

### F23 [NOTE] `draft finalize` release step reports `released=False`
- The editor did not `work claim` before writing, so finalize's release is a no-op (`released=False`). Harmless here but implies a claim/lock contract the skill-driven editor skips. Minor. Fix: document that claim acquisition is optional for single-writer runs, or have finalize acquire+release atomically.

### F24 [ROUGH] `wiki list --format json` returns `page_id: null`
- **Symptom**: `wiki list --format json` rows show `page_id`/`title` as null (only `kind` populated), so the editor can't map committed pages to slugs from this command.
- **Severity**: minor (reporting). Fix: populate `page_id`/`title` in the JSON rows.

### F25 [ROUGH] `work maturity --all` reports committed concepts as band `ready`
- **Symptom**: after committing 3 pages, `work maturity --all` still lists them as `ready` (maturity ignores commit status), so the editor must cross-reference `wiki list` to avoid re-dispatching the WRITE wave on already-committed slugs.
- **Severity**: minor. Fix: mark committed slugs as a distinct band (`committed`) or exclude them from `--all`.

### F26 [BUG] P5's `wiki_find(mode="semantic")` is empty until `wiki rebuild` runs — but P5 runs every round, before rebuild
- **Symptom**: the GAP explorer's `wiki_find(mode="semantic")` returned 0 results every round; wiki vectors are only built by `wiki rebuild` at finalize. The P5 pattern (per the explore skill) uses `mode="semantic"` to route residual chunks to existing pages, so routing was impossible mid-loop; the agent fell back to `mode="hybrid"/"bm25"` which worked.
- **Where**: `wiki rebuild` builds `derived/vectors.npz`; P5 in `wikify-investigate-explore/SKILL.md`.
- **Root-cause hypothesis**: wiki embeddings are a finalize-time artifact; mid-loop the committed-page vectors don't exist, so semantic wiki search can't function during the loop that depends on it.
- **Severity**: major (P5 routing to existing pages is inert mid-loop). Fix: incrementally embed pages at `draft finalize` (so `wiki_find` semantic works next round), or change P5 to use `mode="hybrid"` which degrades gracefully.

### F27 [BUG] `draft check` rejects valid JSON `\uXXXX` escapes and escaped inner double-quotes in reference quotes
- **Symptom**: writers found `draft check` rejects quotes containing literal `\uXXXX` escapes (valid JSON) — Unicode must be emitted as direct chars (`ensure_ascii=False`); and the `[^eN]:` reference parser cuts a quote at the first escaped inner `\"`, so quotes containing double-quotes spuriously fail. Both forced workarounds.
- **Where**: `wikify draft check` reference-quote extraction/normalization.
- **Severity**: major (rejects correctly-grounded content). Fix: JSON-decode reference strings before substring-matching (so `\uXXXX` == the char), and parse the `[^eN]:` block as structured data rather than a quote-delimited regex.

### F28 [BUG] Committed data artifacts are not registered in the wiki DB — navigation can't group them
- **Symptom**: `data commit` writes the artifact page to `wiki/data/<title>.md` and `data list-artifacts` shows it, but it is ABSENT from `wiki list` (not in the wiki SQLite DB). The organizer hit a FOREIGN KEY IntegrityError trying to place it in a nav group and had to dump it into `ungrouped_page_ids`. So data artifacts are second-class: unreachable from the topic hierarchy.
- **Where**: `wikify data commit` vs the wiki page DB; `wikify-organize-wiki` apply-navigation.
- **Root-cause hypothesis**: `data commit` and `wiki commit` write to different stores; the data path never inserts a wiki_page row.
- **Severity**: major (artifact orphaned in navigation/index). Fix: have `data commit` (or `data rebuild`) register a `wiki_page` row (kind=data) so navigation/index/graph can reference it.

### F29 [BUG] Figure evidence markers with no inline `[^eN]` prose ref render without citation (render warns)
- **Symptom**: `render` warned: "figure-citation marker(s) with no body footnote ref (page Atomic Layer Deposition): e2" and "(In-memory computing): e3". A figure was attached as evidence but never cited inline in prose, so it renders with no numeric citation — exactly the "figure added but not referenced in prose" defect.
- **Where**: writer output; `render` html.
- **Severity**: minor-major (page-quality defect the render correctly flags but still ships). Fix: writer must add an inline `[^eN]` for any figure it includes; consider making this a `draft check` error rather than a render-time warning.

### F30 [NOTE] M5 hit-rate is `n/a` — explorers emit no `chunk_read` events
- Eval M5 needs `chunk_read` events; the explore subagents never emit them, so M5 is unmeasurable. Note. Fix: have the explore vetter emit `chunk_read` events, or drop M5 from the investigate eval expectations.

---

## HTML review findings

Reviewed all 10 rendered pages (6 articles, 1 data table, index, references, graph). Overall: all 6 articles GENERALIZE (no single-paper-summary/stub defects), no `[[wikilinks]]`/chunk-id/control-char leaks in article prose, no em-dashes, all internal links resolve, graph.html loads (7 nodes/15 edges incl. the data node), index.html reaches all pages, data table NOT orphaned in the rendered site (surfaced under a "Data tables" nav section, softening F28). The real defects are in citation/figure rendering:

### F31 [BUG] Figure-caption citation renders a DEAD `[eN]` anchor + leaks the raw `[eN]` token
- **Symptom**: on `Atomic_Layer_Deposition.html` the figure caption emits `<sup class="figure-citation"><a href="#fn:e2">[e2]</a></sup>`, but no `<li id="fn:e2">` exists on the page -> the link is dead AND the literal token `[e2]` shows to the reader instead of a citation number. Same on `In-memory_computing.html` (`#fn:e3`, token `[e3]`). This is the render-time warning F29 manifesting as a user-visible defect.
- **Where**: render html figure-citation emission (the `figure-citation` sup); a figure whose evidence marker (e2/e3) was never cited inline in prose has no body footnote, so the anchor dangles.
- **Root-cause hypothesis**: the figure is attached to evidence marker eN; render links the caption to `#fn:eN` unconditionally, but eN only gets an `<li id="fn:eN">` if it was ALSO cited in body prose. Figure-only markers therefore dangle.
- **Severity**: major (visible broken citation + leaked raw token on 2 of 6 pages). Fix: when a figure marker has no body footnote, either (a) still emit the footnote for the figure's source so the anchor resolves, or (b) suppress the `[eN]` sup entirely. Promote the current render-time warning to a `draft check` error so it cannot ship.

### F32 [BUG] `references.html` leaks raw chunk/doc-hash citations from the data artifact
- **Symptom**: the first 5 entries of `references.html` are raw `<code>` blocks like `[2020 Wang] Flexible 3D memristor array..._26f048c4fea6, 2.2 High-density binary memory` and `[2022 Ismail]_65456d1402fa, 2. RESULTS AND DISCUSSION` — internal doc-hash fragments and section labels, not CS1 bibliographic entries. They come from the data artifact's per-cell source citations.
- **Where**: references.html aggregation + the data-artifact reference format (see F33 — same root).
- **Severity**: major (internal machinery on a public page). Fix: format data-artifact sources as clean citations (Author, Year, Title) before they reach references.html; strip the `_<dochash>__cNNNN_<hex>` id fragments.

### F33 [BUG] Data-artifact footnote LABELS embed the raw canonical chunk id and doc-hash
- **Symptom**: every `[^dN]` definition in `wiki/data/Memristor and RRAM Device Performance Comparison.md` (and thus the rendered page) reads:
  `[2023 Sahu] Linear and symmetric ... in oxide_4dbfd151d2dc__c0006_f39949f4 ([2023 Sahu] ...oxide_4dbfd151d2dc, Table 1) > "quote"`.
  The `_4dbfd151d2dc__c0006_f39949f4` (canonical chunk id) and `_4dbfd151d2dc` (doc hash) are raw internal ids shown to the reader. For `[2022 Ismail]_65456d1402fa` the human title is MISSING entirely — only the hash shows.
- **Where**: `wikify data consolidate` / artifact reference builder (the code that formats each cell's source citation).
- **Root-cause hypothesis**: the citation string is built from the chunk's canonical id (which is `<title>_<dochash>__cNNNN_<hex>`) without stripping the id suffix or resolving a clean human title; when the title is absent it falls back to the bare hash.
- **Severity**: major (leaked machinery + missing titles on the data page and references.html). Fix: build data-artifact citations as `[Year Author] <clean title> — <section> > "quote"`; derive the clean title (underscores->spaces, strip `_<12hex>__cNNNN_<hex>` and trailing `_<12hex>`); resolve the title from the corpus doc metadata rather than the chunk id. Add a render/format test asserting no `_[0-9a-f]{12}` or `__c\d+_` survives into artifact output.

### F34 [VERIFY] HTML may collapse the 25 per-cell data footnotes (reviewer reported 5 in HTML)
- **Symptom**: markdown has 25 distinct `[^d1..d25]` definitions each with its own grounding quote (confirmed). One reviewer reported the rendered HTML shows only 5 footnote entries with many backlinks, dropping per-cell grounding quotes. UNVERIFIED by me (reviewer counts were internally inconsistent). If true, it is a markdown->HTML footnote-dedup bug.
- **Severity**: major IF confirmed. Action: verify the rendered footnote count during remediation; fix only if the HTML genuinely drops footnotes the markdown defines.

### F35 [ROUGH] Concept articles have no in-body "Related data" link to the data artifact
- **Symptom**: none of the 6 articles (incl. Memristor/Resistive switching/ALD, for which the table is primary) carry an in-body "Related data" section linking the data artifact. The write skill says naming a data artifact in prose auto-links it under Related data.
- **Root-cause hypothesis**: partly run-sequencing (the round-2 writers ran in parallel with the consolidator, so the artifact did not exist in their dossier; the round-3 writers had it in the "Available data" index but were not instructed to name it, and did not). So the auto-link never triggered.
- **Severity**: minor-major (cross-link missing; data is still reachable via nav). Fix (process): consolidate BEFORE the writers that should cite the artifact, and have the writer name a relevant committed artifact in prose; or have `draft build` inject a "Related data" stub when a relevant artifact exists. Could remediate the Memristor page via `wikify-refine`.

### F36 [BUG] Figure `alt` text is truncated mid-word with a trailing `...`
- **Symptom**: every figure's `alt` attribute is cut off mid-word, e.g. `alt="Resistivity and O/Ti ratio of ALD TiO2 films ... demonstrating temperature-controlled oxygen sto..."`. Hurts accessibility.
- **Where**: render figure alt-text generation (truncation).
- **Severity**: minor. Fix: use the full caption as alt text, or truncate at a word boundary / sentence end rather than mid-word.

### Minor / cosmetic
- Memristor figure has no explicit prose callout (placement only) — writer-side, minor.
- Empty data-table cells render as bare `<td></td>` (no en-dash placeholder) — cosmetic.
- Figure alt truncation (F36) applies to all 3 figured pages.

---

## Remediation (code fixes landed on this branch)

Fixed the four render/citation defects that are clear code bugs (tests added; `uv run ruff check src/wikify tests/wikify` and `uv run pytest tests/wikify -q` green at 1504 passed / 1 skipped; bundle re-rendered + re-reviewed):

- **F33 + F32 fixed** (`src/wikify/data/artifact_page.py` + `src/wikify/render/html/render.py`): data-artifact footnote labels are now built from a cleaned source label (`_clean_source_label` strips the `__cNNNN_<hex>` chunk suffix and the `_<12hex>` doc hash); the raw doc_id is retained only in a parser-only parenthetical on disk (so the Related-data cross-link matcher still resolves source docs), and that parenthetical is now stripped from the rendered display (`_DATA_FOOTNOTE_DOCID_TAIL_RE` in `_clean_evidence_lines` for `kind="data"`). Result: `references.html` is fully clean (0 hash leaks, 0 raw `<code>` blocks) and the data page footnotes show clean human citations + grounding quotes. (One residual `_<12hex>` remains only inside an `<img src>` asset filename, not reader-facing prose — acceptable.)
- **F34 fixed** (`render.py` `_clean_evidence_lines`, gated on `kind="data"`): data pages skip the same-paper footnote collapse + quote-drop, so all 25 per-cell `[^dN]` footnotes survive with their grounding quotes (was collapsing to 5). Verified in re-render (25 distinct `fn:dN`).
- **F31 fixed** (`render.py` `_remap_figure_citation_numbers`): orphan figure-citation sups (figure marker with no body footnote) are now removed entirely instead of emitting a dead `#fn:eN` anchor + raw `[eN]` token. Verified: ALD and In-memory pages no longer show `[e2]`/`[e3]` or dead anchors.
- **F36 fixed** (`render.py` `_figure_alt_text`): alt text now truncates at a word boundary instead of mid-word.

Tests added: `tests/wikify/test_render_citations.py`, `tests/wikify/test_artifact_page_citations.py`; extended `tests/wikify/test_render_figure_citation_numbers.py`, `tests/wikify/test_data_subsystem.py`.

**Not remediated in code (rationale):** F35 (Related-data in-body link) is primarily a run-sequencing artifact (the consolidate wave ran in parallel with some writers; writers were not instructed to name the artifact) — the cross-link *feature* works (covered by `test_related_data_cross_link_rendered`) but fires only on shared-source overlap; recommended as a process/skill change, not a code bug. The contract/CLI frictions F1–F30 are logged as findings for the maintainers; fixing all of them is out of scope for this profiling pass. F8/F18/F22/F26/F14 are flagged major for follow-up.

---

## Adversarial review findings

An adversarial reviewer (Opus) attacked the branch diff (`git diff master...HEAD`) and re-inspected every rendered artifact. **Verdict: CLEAN — no critical or major findings.** ruff pass; pytest 1504 passed / 1 skipped.

- Regex `_DATA_FOOTNOTE_DOCID_TAIL_RE` checked adversarially against quotes containing parens (`(metallic phase)`), `(p. 3)` locators, hashless doc_ids, and a 12-hex-in-parens-before-`> "` — no over-match or under-match.
- `kind="data"` branch is isolated; non-data (article) footnote behavior is byte-identical to before (covered by tests).
- Orphan figure-sup removal substitutes the whole `<sup>…</sup>` (no dangling markup); only fires for markers with no body footnote-ref.
- **Cross-link integrity confirmed**: the on-disk `wiki/data/*.md` retains the `(<doc_id>_<12hex>)` parser tail on all 25 footnotes; the hash strip is render-only; `Page._parse_evidence_value` still recovers `doc_id`. End-to-end, the Related-data cross-link correctly fires on the pages that share a source doc with the artifact (**Memristor**, **Resistive switching**); the other four articles do not cite the device-table source papers, so no link is the correct outcome (the reviewer's "all 6" was an overstatement — verified directly: 2 of 6, which is right).
- Two nits raised, both "no change required" (data pages intentionally skip CS1 formatting; a redundant on-disk parenthetical for hashless ids with no reader impact).

No remediation required after the adversarial pass.
